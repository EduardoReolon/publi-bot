"""Adaptador para a API de Mensagens da Anthropic.

O unico provedor que precisa de codigo proprio: os demais falam o formato
compativel com OpenAI.
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

STATUS_TERMINAIS = frozenset({400, 401, 403, 404, 413, 422})

VERSAO_DA_API = "2023-06-01"


class AnthropicClient(LLMClient):
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
            # Obrigatorio nesta API, ao contrario da compativel com OpenAI.
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if json_schema:
            # Sem modo JSON nativo: preenchemos o inicio da resposta do
            # assistente com "{", o que leva o modelo a continuar em JSON.
            corpo["messages"].append({"role": "assistant", "content": "{"})

        inicio = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, verify=True) as cliente:
                resposta = cliente.post(
                    f"{self.base_url}/v1/messages",
                    json=corpo,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": self.api_key or "",
                        "anthropic-version": VERSAO_DA_API,
                    },
                )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ProviderTransientError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(str(exc)) from exc

        latencia = int((time.perf_counter() - inicio) * 1000)

        if not resposta.is_success:
            detalhe = resposta.text[:500]
            erro = (
                ProviderPermanentError
                if resposta.status_code in STATUS_TERMINAIS
                else ProviderTransientError
            )
            raise erro(f"HTTP {resposta.status_code}: {detalhe}")

        dados = resposta.json()
        try:
            texto = "".join(
                bloco.get("text", "") for bloco in dados["content"] if bloco.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise ProviderPermanentError(f"resposta inesperada: {dados}") from exc

        if json_schema:
            # Recompoe a chave de abertura que enviamos como prefixo.
            texto = "{" + texto

        uso = dados.get("usage") or {}
        return LLMResponse(
            text=texto,
            model=dados.get("model", model),
            input_tokens=uso.get("input_tokens", 0),
            output_tokens=uso.get("output_tokens", 0),
            latency_ms=latencia,
            raw=dados,
        )

    def health(self) -> bool:
        # Nao ha endpoint de saude publico; uma chamada minima serve de sonda.
        try:
            self.chat(model="claude-haiku-4-5-20251001", system="", user="ok", max_tokens=1)
            return True
        except ProviderTransientError:
            return False
        except ProviderPermanentError:
            # Credencial invalida significa endpoint no ar, configuracao errada.
            return True
