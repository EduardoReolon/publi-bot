"""Animacao: uma imagem vira um clipe curto (imagem para video).

Um dialeto so, o da OpenAI (`/v1/videos`), que o worker-gpu tambem fala
(docs/WORKER_VOZ_E_ANIMACAO.md) — assim a mesma conexao serve a placa propria
ou a Sora, trocando so o endereco:

    POST /v1/videos               multipart: prompt, model, seconds, size,
                                  input_reference (a imagem)  -> {"id", "status"}
    GET  /v1/videos/{id}          -> {"status": queued|in_progress|completed|failed,
                                      "progress", "error": {"message"}}
    GET  /v1/videos/{id}/content  -> o MP4

E assincrono: gerar video leva minutos (na placa propria de 8 GB, uns 20 min
por 2,4 s). O cliente consulta a cada `INTERVALO` segundos ate `limite`.
503 (placa ocupada) e 429 viram
`ProviderTransientError` (a tarefa tenta mais tarde, sem gastar tentativa);
um id que sumiu (o worker reiniciou e esqueceu o trabalho) tambem — pedir de
novo resolve.
"""

from __future__ import annotations

import time

import httpx

from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError

INTERVALO = 10.0


class OpenAIVideoClient:
    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        from apps.inference.providers.openai_compatible import raiz_da_api

        self.raiz = raiz_da_api(base_url)
        self.api_key = api_key or ""
        self.timeout = timeout

    def _cabecalhos(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _conferir(self, resposta: httpx.Response, onde: str) -> httpx.Response:
        if resposta.status_code in (408, 429) or resposta.status_code >= 500:
            espera = resposta.headers.get("Retry-After", "")
            raise ProviderTransientError(
                f"{onde} respondeu {resposta.status_code}"
                + (f" (tente em {espera}s)" if espera else "")
                + f": {resposta.text[:300]}"
            )
        if resposta.status_code == 404 and "/videos/" in onde:
            raise ProviderTransientError(f"{onde}: o trabalho sumiu (servidor reiniciou?)")
        if resposta.status_code >= 400:
            raise ProviderPermanentError(
                f"{onde} recusou ({resposta.status_code}): {resposta.text[:300]}"
            )
        return resposta

    def _chamar(self, metodo: str, caminho: str, **kwargs) -> httpx.Response:
        url = f"{self.raiz}{caminho}"
        try:
            resposta = httpx.request(
                metodo, url, headers=self._cabecalhos(), timeout=self.timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"nao foi possivel falar com {url}: {exc}") from exc
        return self._conferir(resposta, caminho)

    def criar(
        self,
        *,
        imagem: bytes,
        prompt: str,
        modelo: str,
        segundos: int,
        tamanho: str,
        seed: int | None = None,
    ) -> str:
        return self.criar_trabalho(
            imagem=imagem,
            prompt=prompt,
            modelo=modelo,
            segundos=segundos,
            tamanho=tamanho,
            seed=seed,
        )["id"]

    def criar_trabalho(
        self,
        *,
        imagem: bytes,
        prompt: str,
        modelo: str,
        segundos: int,
        tamanho: str,
        seed: int | None = None,
    ) -> dict:
        """O trabalho criado (`id`, e `seed`: a usada, inclusive a sorteada,
        quando o servidor a devolve — o worker devolve)."""
        dados = {"prompt": prompt, "model": modelo, "seconds": str(segundos), "size": tamanho}
        if seed is not None:
            # So o worker aceita (a Sora recusa campo que nao conhece): a mesma
            # imagem, o mesmo movimento e a mesma seed repetem o clipe.
            dados["seed"] = str(seed)
        resposta = self._chamar(
            "POST",
            "/videos",
            data=dados,
            files={"input_reference": ("quadro.png", imagem, "image/png")},
        )
        trabalho = resposta.json()
        if not trabalho.get("id"):
            raise ProviderPermanentError("o servico de video nao devolveu o id do trabalho.")
        return trabalho

    def esperar(self, identificador: str, *, limite: float, dormir=time.sleep) -> None:
        fim = time.monotonic() + limite
        while True:
            dados = self._chamar("GET", f"/videos/{identificador}").json()
            situacao = dados.get("status")
            if situacao == "completed":
                return
            if situacao == "failed":
                erro = (dados.get("error") or {}).get("message") or "sem motivo"
                raise ProviderPermanentError(f"a animacao falhou: {erro}")
            if time.monotonic() > fim:
                raise ProviderTransientError(
                    f"a animacao passou de {int(limite)}s ({dados.get('progress', '?')}%)"
                )
            dormir(INTERVALO)

    def situacao(self, identificador: str) -> dict:
        """O trabalho como o servidor o ve agora, sem esperar. `{"status":
        "sumiu"}` quando ele nao o conhece mais (reiniciou): pede-se de novo. Sem
        conexao (worker desligado): `ProviderTransientError`, confere-se depois."""
        url = f"{self.raiz}/videos/{identificador}"
        try:
            resposta = httpx.request("GET", url, headers=self._cabecalhos(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"nao foi possivel falar com {url}: {exc}") from exc
        if resposta.status_code == 404:
            return {"status": "sumiu"}
        return self._conferir(resposta, f"/videos/{identificador}").json()

    def baixar(self, identificador: str) -> bytes:
        return self._chamar("GET", f"/videos/{identificador}/content").content

    def animar(
        self,
        *,
        imagem: bytes,
        prompt: str,
        modelo: str,
        segundos: int = 4,
        tamanho: str = "720x1280",
        seed: int | None = None,
        limite: float = 5400.0,
        dormir=time.sleep,
    ) -> bytes:
        """O MP4 da imagem animada (criar, esperar, baixar)."""
        identificador = self.criar(
            imagem=imagem,
            prompt=prompt,
            modelo=modelo,
            segundos=segundos,
            tamanho=tamanho,
            seed=seed,
        )
        self.esperar(identificador, limite=limite, dormir=dormir)
        return self.baixar(identificador)


def get_animation_provider(connection) -> OpenAIVideoClient:
    from apps.inference.models import InferenceConnection
    from apps.inference.security import decifrar_chave

    if connection.kind != InferenceConnection.Kind.OPENAI_COMPATIBLE:
        raise ProviderPermanentError(
            f"A conexao {connection.name!r} e do tipo {connection.kind!r}, que nao anima imagem."
        )
    return OpenAIVideoClient(base_url=connection.base_url, api_key=decifrar_chave(connection))
