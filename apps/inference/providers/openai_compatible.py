"""Adaptador para a API de chat compativel com OpenAI.

Cobre Ollama, Together, Groq, OpenAI, DeepSeek, vLLM e LM Studio — todos falam
`POST /v1/chat/completions` com o mesmo formato. E por isso que trocar de
provedor neste projeto e uma edicao de linha no painel, e nao um framework.
"""

from __future__ import annotations

import time

import httpx

from apps.inference.providers.base import (
    LLMClient,
    LLMResponse,
    ProviderPermanentError,
    ProviderTransientError,
)

# 4xx que nao adianta repetir. O 429 fica de fora de proposito: e transitorio.
STATUS_TERMINAIS = frozenset({400, 401, 403, 404, 405, 413, 422})


class OpenAICompatibleClient(LLMClient):
    def _headers(self) -> dict[str, str]:
        cabecalhos = {"Content-Type": "application/json"}
        if self.api_key:
            cabecalhos["Authorization"] = f"Bearer {self.api_key}"
        return cabecalhos

    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        corpo: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            corpo["max_tokens"] = max_tokens
        if json_schema:
            corpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "resposta", "schema": json_schema, "strict": True},
            }

        inicio = time.perf_counter()
        try:
            # `verify=True` explicito: o contrato exige TLS, e desligar a
            # verificacao do certificado e o "conserto" mais comum quando um
            # certificado incomoda — o que anula o TLS inteiro. Ha hook de
            # pre-commit que recusa desligar isso.
            with httpx.Client(timeout=self.timeout, verify=True) as cliente:
                resposta = cliente.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=corpo,
                    headers=self._headers(),
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            # A GPU local desligada cai aqui. O trabalho nao se perde: espera.
            raise ProviderTransientError(
                f"nao foi possivel falar com {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(str(exc)) from exc

        latencia = int((time.perf_counter() - inicio) * 1000)
        self._levantar_se_erro(resposta)

        dados = resposta.json()
        try:
            texto = dados["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderPermanentError(
                f"resposta em formato inesperado de {self.base_url}: {dados}"
            ) from exc

        uso = dados.get("usage") or {}
        return LLMResponse(
            text=texto,
            model=dados.get("model", model),
            input_tokens=uso.get("prompt_tokens", 0),
            output_tokens=uso.get("completion_tokens", 0),
            latency_ms=latencia,
            raw=dados,
        )

    @staticmethod
    def _levantar_se_erro(resposta: httpx.Response) -> None:
        if resposta.is_success:
            return
        detalhe = resposta.text[:500]
        if resposta.status_code in STATUS_TERMINAIS:
            raise ProviderPermanentError(f"HTTP {resposta.status_code}: {detalhe}")
        raise ProviderTransientError(f"HTTP {resposta.status_code}: {detalhe}")

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0, verify=True) as cliente:
                return cliente.get(f"{self.base_url}/v1/models", headers=self._headers()).is_success
        except httpx.HTTPError:
            return False
