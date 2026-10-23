"""Voz: texto para fala, para a narracao dos videos das redes.

Tres adaptadores cobrem as APIs mais usadas — e todas sao a mesma ideia: manda
o texto e a voz, recebe o audio (MP3):

* **ElevenLabs** (`elevenlabs`): `POST /v1/text-to-speech/{voz}`, chave em
  `xi-api-key`; modelo padrao `eleven_multilingual_v2` (fala portugues).
* **OpenAI e compativeis** (`openai_compatible` com a carga `voz`):
  `POST /v1/audio/speech`, `{model, voice, input}`. Serve a OpenAI (`tts-1`,
  `gpt-4o-mini-tts`) e servidores locais que imitam a rota (Kokoro,
  openedai-speech) — a voz pode sair da propria placa.
* **Google Text-to-Speech** (`google_tts`): `POST /v1/text:synthesize?key=`,
  audio em base64; a voz e o nome do Google (ex.: `pt-BR-Neural2-B`).

`get_voice_provider` e o unico lugar que sabe qual classe atende qual tipo.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod

import httpx

from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError


class VoiceClient(ABC):
    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    @abstractmethod
    def falar(
        self,
        *,
        texto: str,
        voz: str,
        modelo: str = "",
        idioma: str = "pt-BR",
        antes: str = "",
        depois: str = "",
    ) -> bytes:
        """O audio (MP3) do texto, na voz pedida. `antes` e `depois`: as falas
        vizinhas (o servico que aceita usa para a entonacao continuar)."""

    def _post(self, url: str, **kwargs) -> httpx.Response:
        try:
            resposta = httpx.post(url, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"nao foi possivel falar com {url}: {exc}") from exc
        if resposta.status_code in (408, 429) or resposta.status_code >= 500:
            erro = ProviderTransientError(
                f"{url} respondeu {resposta.status_code}: {resposta.text[:300]}"
            )
            # 503 com Retry-After: a placa esta ocupada (a voz do Qwen espera
            # uma animacao); quem chama volta depois desse tempo.
            try:
                erro.retry_after = int(resposta.headers.get("Retry-After") or 0) or None
            except ValueError:
                erro.retry_after = None
            raise erro
        if resposta.status_code >= 400:
            raise ProviderPermanentError(
                f"{url} recusou ({resposta.status_code}): {resposta.text[:300]}"
            )
        return resposta


class ElevenLabsClient(VoiceClient):
    MODELO_PADRAO = "eleven_multilingual_v2"
    # Voz pronta da biblioteca (a dos exemplos da documentacao): so para testar e
    # para conta sem voz escolhida; a da conta vai em Configurar > a conta > Video e voz.
    VOZ_PADRAO = "JBFqnCBsd6RMkjVDRZzb"
    # Entonacao de conversa, e nao de leitura: menos estabilidade varia mais o
    # tom, e o estilo puxa a expressao da voz. Os valores padrao da ElevenLabs
    # (estabilidade 0,5, estilo 0) soavam como alguem lendo.
    AJUSTES = {
        "stability": 0.35,
        "similarity_boost": 0.75,
        "style": 0.3,
        "use_speaker_boost": True,
    }

    def falar(
        self,
        *,
        texto: str,
        voz: str,
        modelo: str = "",
        idioma: str = "pt-BR",
        antes: str = "",
        depois: str = "",
    ) -> bytes:
        voz = voz or self.VOZ_PADRAO
        corpo = {
            "text": texto,
            "model_id": modelo or self.MODELO_PADRAO,
            "voice_settings": self.AJUSTES,
        }
        # As falas vizinhas: cada cena e um pedido, e sem elas cada frase saia
        # como se comecasse e terminasse uma conversa.
        if antes:
            corpo["previous_text"] = antes
        if depois:
            corpo["next_text"] = depois
        resposta = self._post(
            f"{self.base_url}/v1/text-to-speech/{voz}?output_format=mp3_44100_128",
            headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
            json=corpo,
        )
        return resposta.content


class OpenAIVoiceClient(VoiceClient):
    MODELO_PADRAO = "tts-1"

    def falar(
        self,
        *,
        texto: str,
        voz: str,
        modelo: str = "",
        idioma: str = "pt-BR",
        antes: str = "",
        depois: str = "",
    ) -> bytes:
        from apps.inference.providers.openai_compatible import raiz_da_api

        resposta = self._post(
            f"{raiz_da_api(self.base_url)}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={
                "model": modelo or self.MODELO_PADRAO,
                "voice": voz or "alloy",
                "input": texto,
                "response_format": "mp3",
            },
        )
        return resposta.content


class GoogleVoiceClient(VoiceClient):
    def falar(
        self,
        *,
        texto: str,
        voz: str,
        modelo: str = "",
        idioma: str = "pt-BR",
        antes: str = "",
        depois: str = "",
    ) -> bytes:
        resposta = self._post(
            f"{self.base_url}/v1/text:synthesize",
            params={"key": self.api_key},
            json={
                "input": {"text": texto},
                "voice": {"languageCode": idioma, **({"name": voz} if voz else {})},
                "audioConfig": {"audioEncoding": "MP3"},
            },
        )
        conteudo = resposta.json().get("audioContent")
        if not conteudo:
            raise ProviderPermanentError("o Google nao devolveu audio.")
        return base64.b64decode(conteudo)


# Endereco de cada servico, para o comando `configurar_api`.
ENDERECOS = {
    "elevenlabs": "https://api.elevenlabs.io",
    "openai": "https://api.openai.com",
    "google_tts": "https://texttospeech.googleapis.com",
}


def get_voice_provider(connection, *, timeout: float | None = None) -> VoiceClient:
    from apps.inference.models import InferenceConnection
    from apps.inference.security import decifrar_chave

    mapa = {
        InferenceConnection.Kind.ELEVENLABS: ElevenLabsClient,
        InferenceConnection.Kind.GOOGLE_TTS: GoogleVoiceClient,
        InferenceConnection.Kind.OPENAI_COMPATIBLE: OpenAIVoiceClient,
    }
    classe = mapa.get(connection.kind)
    if classe is None:
        raise ProviderPermanentError(
            f"A conexao {connection.name!r} e do tipo {connection.kind!r}, que nao fala."
        )
    return classe(
        base_url=connection.base_url,
        api_key=decifrar_chave(connection),
        timeout=timeout if timeout is not None else 120.0,
    )
