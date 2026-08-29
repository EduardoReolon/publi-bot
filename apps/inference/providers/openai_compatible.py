"""Adaptador para a API de chat compativel com OpenAI.

Cobre Ollama, Together, Groq, OpenAI, DeepSeek, vLLM e LM Studio — todos falam
`POST /v1/chat/completions` com o mesmo formato. E por isso que trocar de
provedor neste projeto e uma edicao de linha no painel, e nao um framework.
"""

from __future__ import annotations

import time

import httpx

from apps.inference.providers.base import (
    ImageClient,
    ImagemGerada,
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


class OpenAICompatibleImageClient(ImageClient):
    """Geracao de imagem por `POST /v1/images/generations`.

    Mesma escolha do adaptador de texto: uma unica implementacao cobre OpenAI,
    LocalAI, e os varios servidores locais que expoem a rota compativel.

    Duas particularidades do formato, ambas com consequencia pratica:

    **`n` nem sempre e respeitado.** Alguns modelos (o dall-e-3 e o caso
    conhecido) recusam `n > 1` ou simplesmente devolvem uma imagem so. Como o
    ponto aqui e ter opcoes para comparar, o cliente completa o que faltou com
    chamadas adicionais em vez de devolver menos do que foi pedido.

    **`b64_json`, nao `url`.** A alternativa devolve um link temporario do
    provedor que expira em cerca de uma hora; guardar esse link levaria a uma
    imagem quebrada no artigo dias depois, na hora da publicacao.
    """

    def _headers(self) -> dict[str, str]:
        cabecalhos = {"Content-Type": "application/json"}
        if self.api_key:
            cabecalhos["Authorization"] = f"Bearer {self.api_key}"
        return cabecalhos

    def generate(
        self, *, model: str, prompt: str, quantidade: int = 3, tamanho: str = "1024x1024"
    ) -> list[ImagemGerada]:
        imagens: list[ImagemGerada] = []

        # Pede tudo de uma vez e completa o que faltar. Nao da para saber de
        # antemao se o modelo aceita `n > 1` — descobrir custa uma chamada — e
        # o pior caso (uma imagem por chamada) fecha em `quantidade` chamadas,
        # que e o limite do laco.
        for _ in range(quantidade):
            faltam = quantidade - len(imagens)
            if faltam <= 0:
                break
            lote = self._chamar(model=model, prompt=prompt, n=faltam, tamanho=tamanho)
            if not lote:
                break
            imagens.extend(lote[:faltam])

        if not imagens:
            raise ProviderPermanentError(
                f"{self.base_url} nao devolveu nenhuma imagem para o modelo {model!r}."
            )
        return imagens

    def _chamar(self, *, model: str, prompt: str, n: int, tamanho: str) -> list[ImagemGerada]:
        import base64

        corpo = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": tamanho,
            "response_format": "b64_json",
        }

        try:
            with httpx.Client(timeout=self.timeout, verify=True) as cliente:
                resposta = cliente.post(
                    f"{self.base_url}/v1/images/generations",
                    json=corpo,
                    headers=self._headers(),
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            raise ProviderTransientError(
                f"nao foi possivel falar com {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(str(exc)) from exc

        self._levantar_se_erro(resposta)
        dados = resposta.json()

        geradas = []
        for item in dados.get("data") or []:
            bruto = item.get("b64_json")
            if not bruto:
                continue
            try:
                conteudo = base64.b64decode(bruto)
            except (ValueError, TypeError) as exc:
                raise ProviderPermanentError(
                    f"{self.base_url} devolveu b64_json invalido."
                ) from exc
            geradas.append(
                ImagemGerada(conteudo=conteudo, prompt_revisado=item.get("revised_prompt", ""))
            )
        return geradas

    @staticmethod
    def _levantar_se_erro(resposta: httpx.Response) -> None:
        OpenAICompatibleClient._levantar_se_erro(resposta)

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0, verify=True) as cliente:
                return cliente.get(f"{self.base_url}/v1/models", headers=self._headers()).is_success
        except httpx.HTTPError:
            return False
