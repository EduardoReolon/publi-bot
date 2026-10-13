"""APIs externas: os adaptadores de voz (ElevenLabs, OpenAI, Google), a raiz
da API compativel com a OpenAI e o comando `configurar_api`."""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from django.core.management import call_command

from apps.inference.models import InferenceConnection
from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError
from apps.inference.providers.openai_compatible import raiz_da_api
from apps.inference.providers.voz import (
    ElevenLabsClient,
    GoogleVoiceClient,
    OpenAIVoiceClient,
    get_voice_provider,
)
from apps.inference.security import decifrar_chave


@pytest.fixture
def chamadas(monkeypatch):
    """httpx.post falso: guarda o pedido e devolve a resposta da vez."""
    feitas, respostas = [], []

    def post(url, **kwargs):
        feitas.append((url, kwargs))
        status, corpo = respostas.pop(0) if respostas else (200, b"MP3")
        conteudo = corpo if isinstance(corpo, bytes) else None
        return httpx.Response(
            status,
            content=conteudo,
            json=None if conteudo is not None else corpo,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    return feitas, respostas


def test_raiz_da_api_poe_v1_so_quando_falta():
    assert raiz_da_api("https://api.openai.com") == "https://api.openai.com/v1"
    assert raiz_da_api("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    gemini = "https://generativelanguage.googleapis.com/v1beta/openai"
    assert raiz_da_api(gemini) == gemini
    assert raiz_da_api("https://api.groq.com/openai") == "https://api.groq.com/openai/v1"


def test_elevenlabs_manda_a_voz_na_rota_e_a_chave_no_cabecalho(chamadas):
    feitas, _ = chamadas
    audio = ElevenLabsClient(base_url="https://api.elevenlabs.io", api_key="k").falar(
        texto="Ola", voz="abc"
    )
    url, kwargs = feitas[0]
    assert audio == b"MP3"
    assert url.startswith("https://api.elevenlabs.io/v1/text-to-speech/abc")
    assert kwargs["headers"]["xi-api-key"] == "k"
    assert kwargs["json"] == {"text": "Ola", "model_id": "eleven_multilingual_v2"}
    ElevenLabsClient(base_url="https://api.elevenlabs.io").falar(texto="Ola", voz="")
    assert ElevenLabsClient.VOZ_PADRAO in feitas[1][0]  # sem voz: a padrao


def test_openai_e_google(chamadas):
    feitas, respostas = chamadas
    OpenAIVoiceClient(base_url="https://api.openai.com", api_key="k").falar(
        texto="Ola", voz="nova", modelo="gpt-4o-mini-tts"
    )
    url, kwargs = feitas[0]
    assert url == "https://api.openai.com/v1/audio/speech"
    assert kwargs["json"]["voice"] == "nova" and kwargs["json"]["model"] == "gpt-4o-mini-tts"

    respostas.append((200, {"audioContent": base64.b64encode(b"som").decode()}))
    audio = GoogleVoiceClient(base_url="https://texttospeech.googleapis.com", api_key="g").falar(
        texto="Ola", voz="pt-BR-Neural2-B"
    )
    url, kwargs = feitas[1]
    assert audio == b"som" and url.endswith("/v1/text:synthesize")
    assert kwargs["params"] == {"key": "g"}
    assert kwargs["json"]["voice"] == {"languageCode": "pt-BR", "name": "pt-BR-Neural2-B"}


def test_erros_de_voz_viram_transitorio_ou_permanente(chamadas):
    _, respostas = chamadas
    cliente = ElevenLabsClient(base_url="https://api.elevenlabs.io", api_key="k")
    respostas.append((429, b"devagar"))
    with pytest.raises(ProviderTransientError):
        cliente.falar(texto="Ola", voz="v")
    respostas.append((401, b"chave errada"))
    with pytest.raises(ProviderPermanentError):
        cliente.falar(texto="Ola", voz="v")


@pytest.mark.django_db
def test_configurar_api_cria_e_atualiza_a_conexao(public_tenant):
    saida = io.StringIO()
    call_command("configurar_api", "gemini", "--chave", "AIza-teste", stdout=saida)
    conexao = InferenceConnection.objects.get(name="Gemini (API)")
    assert conexao.kind == InferenceConnection.Kind.OPENAI_COMPATIBLE
    assert conexao.base_url.endswith("/v1beta/openai")
    assert conexao.workloads == [InferenceConnection.Workload.OUTRA_IA]
    assert decifrar_chave(conexao) == "AIza-teste"
    assert "AIza-teste" not in saida.getvalue()

    call_command(
        "configurar_api", "gemini", "--chave", "outra", "--modelo", "gemini-x", stdout=saida
    )
    assert InferenceConnection.objects.filter(name="Gemini (API)").count() == 1
    assert InferenceConnection.objects.get(name="Gemini (API)").default_model == "gemini-x"

    call_command("configurar_api", "elevenlabs", "--chave", "xi", stdout=saida)
    voz = InferenceConnection.objects.get(name="ElevenLabs (voz)")
    assert voz.workloads == [InferenceConnection.Workload.VOZ]
    assert isinstance(get_voice_provider(voz), ElevenLabsClient)
