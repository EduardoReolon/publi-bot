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

# `error.code` que o worker de GPU manda num 503 e que NAO adianta repetir
# igual: o trabalho estourou o orcamento de tempo da maquina. Repetir o mesmo
# pedido daria o mesmo resultado, e como adiamento nao gasta tentativa, ele
# seria reagendado para sempre. O worker sublinha isso omitindo o
# `Retry-After` justamente neste codigo.
#
# Qualquer OUTRO codigo — inclusive um que este projeto ainda nao conheca — e
# adiavel. E regra publicada do worker, e existe para que a lista de codigos
# possa crescer sem quebrar cliente nenhum.
CODIGOS_SEM_VOLTA = frozenset({"timeout"})


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
        codigo = _codigo_do_erro(resposta)

        if resposta.status_code in STATUS_TERMINAIS:
            raise ProviderPermanentError(f"HTTP {resposta.status_code}: {detalhe}", code=codigo)

        if codigo in CODIGOS_SEM_VOLTA:
            raise ProviderPermanentError(
                f"o worker desistiu por tempo: {detalhe}\n"
                f"Repetir o mesmo pedido daria o mesmo resultado. Reduza o "
                f"trabalho — prompt menor, `max_tokens` menor, imagem menor.",
                code=codigo,
            )

        raise ProviderTransientError(
            f"HTTP {resposta.status_code}: {detalhe}",
            retry_after=_retry_after(resposta),
            code=codigo,
        )

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
        self,
        *,
        model: str,
        prompt: str,
        quantidade: int = 3,
        tamanho: str = "1024x1024",
        negativo: str = "",
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
            lote = self._chamar(
                model=model, prompt=prompt, n=faltam, tamanho=tamanho, negativo=negativo
            )
            if not lote:
                break
            imagens.extend(lote[:faltam])

        if not imagens:
            raise ProviderPermanentError(
                f"{self.base_url} nao devolveu nenhuma imagem para o modelo {model!r}."
            )
        return imagens

    def _chamar(
        self, *, model: str, prompt: str, n: int, tamanho: str, negativo: str = ""
    ) -> list[ImagemGerada]:
        import base64

        corpo = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": tamanho,
            "response_format": "b64_json",
        }
        # O worker nao tem negativo proprio desde o contrato 2.6: o que for
        # mandado aqui e o que vale. Vazio nao viaja — um campo vazio seria
        # lido como "sem negativo" por uns provedores e recusado por outros.
        if negativo:
            corpo["negative_prompt"] = negativo

        try:
            with httpx.Client(timeout=self.timeout, verify=True) as cliente:
                resposta = cliente.post(
                    f"{self.base_url}/v1/images/generations",
                    json=corpo,
                    headers=self._headers(),
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Recusada ANTES de enviar: o worker estava fora do ar ou
            # reiniciando, e nada rodou. Adiavel, com teto.
            raise ProviderTransientError(
                f"nao foi possivel conectar a {self.base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            # Cortada DEPOIS de aceita — conexao derrubada, resposta vazia, ou
            # nenhuma resposta dentro do timeout (que e maior que o prazo duro
            # do worker). O processo morreu no meio do trabalho, quase sempre
            # por memoria, e o mesmo pedido tende a mata-lo de novo. Falha, e
            # nao adiamento (contrato 2.6 do worker).
            raise ProviderPermanentError(
                f"a conexao com {self.base_url} caiu no meio da geracao "
                f"({type(exc).__name__}: {exc}). O worker provavelmente morreu "
                f"durante o trabalho — veja o journal da maquina da placa."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(str(exc)) from exc

        self._levantar_se_erro(resposta)

        try:
            dados = resposta.json()
        except ValueError as exc:
            raise ProviderPermanentError(
                f"{self.base_url} respondeu {resposta.status_code} sem um JSON legivel."
            ) from exc
        if not isinstance(dados, dict):
            raise ProviderPermanentError(f"{self.base_url} respondeu num formato inesperado.")

        geradas = []
        for item in dados.get("data") or []:
            bruto = item.get("b64_json") if isinstance(item, dict) else None
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
        # 500 COM `error.code` e o worker dizendo que quebrou (hoje,
        # `worker_travado`: passou do prazo duro e se encerrou). E o oposto do
        # 503 — esperar nao resolve, e repetir so prende a placa de novo. Um
        # 500 sem codigo continua no caminho geral: pode ser um provedor pago
        # com um soluco.
        if resposta.status_code == 500:
            codigo = _codigo_do_erro(resposta)
            if codigo:
                raise ProviderPermanentError(
                    f"o worker quebrou ({codigo}): {_mensagem_do_erro(resposta)}\n"
                    f"Nao e repetido sozinho: alguem precisa olhar a maquina da placa.",
                    code=codigo,
                )
        OpenAICompatibleClient._levantar_se_erro(resposta)

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0, verify=True) as cliente:
                return cliente.get(f"{self.base_url}/v1/models", headers=self._headers()).is_success
        except httpx.HTTPError:
            return False


def _codigo_do_erro(resposta: httpx.Response) -> str:
    """O `error.code` do corpo, ou string vazia.

    Nunca levanta. Um provedor que nao seja o worker devolve HTML, texto puro
    ou um JSON de outra forma — e descobrir isso nao pode transformar um erro
    ja identificado noutro erro, dentro do tratamento de erro.
    """
    try:
        corpo = resposta.json()
    except ValueError:
        return ""
    if not isinstance(corpo, dict):
        return ""
    erro = corpo.get("error")
    if not isinstance(erro, dict):
        return ""
    codigo = erro.get("code")
    return codigo if isinstance(codigo, str) else ""


def _mensagem_do_erro(resposta: httpx.Response) -> str:
    """O `error.message` do corpo, ou o texto cru. Nunca levanta."""
    try:
        corpo = resposta.json()
    except ValueError:
        return resposta.text[:500]
    erro = corpo.get("error") if isinstance(corpo, dict) else None
    mensagem = erro.get("message") if isinstance(erro, dict) else None
    return mensagem if isinstance(mensagem, str) else resposta.text[:500]


def _retry_after(resposta: httpx.Response) -> int | None:
    """O `Retry-After` em segundos, quando o provedor manda um.

    `.get` e nao acesso direto: o worker OMITE o cabecalho no codigo
    `timeout`, e um `KeyError` aqui trocaria um erro legivel por um estouro
    dentro do tratamento de erro. A forma de data do HTTP nao e aceita —
    nenhum provedor daqui a usa, e adivinhar fuso para calcular uma espera
    erraria mais do que acertaria.
    """
    bruto = resposta.headers.get("Retry-After")
    if not bruto:
        return None
    try:
        segundos = int(bruto)
    except ValueError:
        return None
    return segundos if segundos > 0 else None
