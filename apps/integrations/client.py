"""Cliente do contrato `/api/v1`.

Agnostico de plataforma: fala HTTP e JSON, e nao sabe nada sobre Django,
WordPress ou qualquer outro sistema do outro lado.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.errors import SitePermanentError, SiteTransientError, classificar
from apps.integrations.models import Site, SiteApiCall
from apps.integrations.signing import AssinaturaHttpx

logger = logging.getLogger("publibot.integrations")

VERSAO_DO_CONTRATO = "v1"

# Conexao curta, leitura longa: publicar um artigo pode levar tempo do outro
# lado, mas estabelecer conexao nao.
TEMPOS_LIMITE = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


@dataclass(frozen=True)
class RespostaDePublicacao:
    status: str
    remote_id: str
    url: str
    slug: str = ""
    post_status: str = ""
    published_at: str | None = None
    ja_existia: bool = False
    # O no responde se quer a foto do autor. Ele e passivo e nao tem cadastro
    # proprio: ou ja recebeu esta foto antes, ou precisa dela agora.
    precisa_da_foto: bool = False


class SiteClient:
    """Fala com um site externo pelo contrato v1."""

    def __init__(self, site: Site):
        self.site = site

    # -- infraestrutura -----------------------------------------------------

    def _auth(self) -> AssinaturaHttpx:
        from apps.inference.security import decifrar

        chave = decifrar(self.site.api_key_ciphertext)
        segredo = decifrar(self.site.signing_secret_ciphertext)
        if not chave:
            raise SitePermanentError(
                f"o site {self.site.name!r} nao tem chave de API cadastrada",
                code="invalid_api_key",
            )
        return AssinaturaHttpx(api_key=chave, signing_secret=segredo or chave)

    def _requisitar(
        self,
        metodo: str,
        rota: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.site.base_url.rstrip('/')}/api/{VERSAO_DO_CONTRATO}{rota}"
        inicio = time.perf_counter()
        registro = {
            "site": self.site,
            "method": metodo,
            "path": rota,
            "idempotency_key": (headers or {}).get("Idempotency-Key"),
        }

        try:
            # `verify=True` explicito. Desligar a verificacao do certificado
            # anula o TLS inteiro, e e o atalho mais comum quando um
            # certificado incomoda — ha hook de pre-commit que recusa isso.
            with httpx.Client(
                timeout=TEMPOS_LIMITE,
                verify=True,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                follow_redirects=False,
            ) as cliente:
                resposta = cliente.request(
                    metodo,
                    url,
                    json=json,
                    data=data,
                    files=files,
                    params=params,
                    headers=headers or {},
                    auth=self._auth(),
                )
        except httpx.TimeoutException as exc:
            self._registrar(
                registro,
                status=None,
                code="timeout",
                latencia=int((time.perf_counter() - inicio) * 1000),
            )
            raise SiteTransientError(f"tempo esgotado ao falar com {url}", code="timeout") from exc
        except httpx.HTTPError as exc:
            self._registrar(
                registro,
                status=None,
                code="connection_error",
                latencia=int((time.perf_counter() - inicio) * 1000),
            )
            raise SiteTransientError(str(exc), code="connection_error") from exc

        latencia = int((time.perf_counter() - inicio) * 1000)
        corpo = self._interpretar(resposta)

        if not resposta.is_success:
            erro_detalhado = (corpo or {}).get("error") or {}
            excecao = classificar(
                resposta.status_code,
                code=erro_detalhado.get("code", ""),
                mensagem=erro_detalhado.get("message", resposta.text[:300]),
                retry_after=_ler_retry_after(resposta),
            )
            self._registrar(
                registro, status=resposta.status_code, code=excecao.code, latencia=latencia
            )
            raise excecao

        self._registrar(registro, status=resposta.status_code, code="", latencia=latencia)
        return corpo or {}

    @staticmethod
    def _interpretar(resposta: httpx.Response) -> dict | None:
        try:
            return resposta.json()
        except ValueError:
            return None

    @staticmethod
    def _registrar(base: dict, *, status: int | None, code: str, latencia: int) -> None:
        SiteApiCall.objects.create(
            site=base["site"],
            method=base["method"],
            path=base["path"],
            http_status=status,
            error_code=code,
            latency_ms=latencia,
            idempotency_key=base.get("idempotency_key"),
        )

    # -- rotas do contrato --------------------------------------------------

    def health(self) -> dict:
        """Descobre o que o site suporta.

        Sem esse aperto de mao, adicionar um campo obrigatorio ou os cabecalhos
        de assinatura quebraria todos os sites instalados, sem forma de saber
        qual versao cada um fala.
        """
        return self._requisitar("GET", "/health/")

    def seo_context(
        self, *, limite: int = 100, cursor: str = "", publicados_apos: str = ""
    ) -> dict:
        """Contexto do site, para evitar competir com o proprio conteudo."""
        params: dict[str, Any] = {"limit": limite}
        if cursor:
            params["cursor"] = cursor
        if publicados_apos:
            params["published_after"] = publicados_apos
        return self._requisitar("GET", "/seo-context/", params=params)

    def pending_questions(self, *, limite: int = 50, cursor: str = "") -> dict:
        """Perguntas ainda nao respondidas, paginadas por cursor.

        Sem cursor e sem confirmacao, cada ciclo reimportaria as mesmas
        perguntas — a unica coisa que as remove do estado pendente e a
        publicacao, que so acontece apos revisao humana, potencialmente dias
        depois.
        """
        params: dict[str, Any] = {"limit": limite}
        if cursor:
            params["cursor"] = cursor
        return self._requisitar("GET", "/pending-questions/", params=params)

    def acknowledge_questions(self, remote_ids: list[str]) -> dict:
        """Confirma o recebimento das perguntas importadas."""
        return self._requisitar("POST", "/pending-questions/ack/", json={"ids": remote_ids})

    def publish(self, payload: dict, *, idempotency_key: str) -> RespostaDePublicacao:
        """Envia conteudo para publicacao.

        A chave de idempotencia e gerada UMA vez e reenviada identica em toda
        tentativa. Sem ela, o cenario classico de timeout de leitura — o site
        grava e responde, a resposta se perde, o SaaS retenta — publicaria o
        mesmo conteudo duas vezes. Conteudo duplicado no site do cliente e
        exatamente o problema que o produto existe para evitar.
        """
        if not settings.PUBLISHING_ENABLED:
            raise SitePermanentError(
                "publicacao desligada globalmente (PUBLISHING_ENABLED)",
                code="publishing_disabled",
            )
        if self.site.publishing_paused:
            raise SitePermanentError(
                f"publicacao pausada para {self.site.name!r}", code="publishing_paused"
            )

        dados = self._requisitar(
            "POST",
            "/publish/",
            json=payload,
            headers={"Idempotency-Key": str(idempotency_key)},
        )
        return RespostaDePublicacao(
            status=dados.get("status", "success"),
            remote_id=str(dados.get("remote_id", "")),
            url=dados.get("url", ""),
            slug=dados.get("slug", ""),
            post_status=dados.get("post_status", ""),
            published_at=dados.get("published_at"),
            # O site devolve 200 quando a chave se repete, em vez de criar de
            # novo. O SaaS trata 200 e 409 como sucesso.
            ja_existia=dados.get("status") == "already_exists",
            precisa_da_foto=bool(dados.get("author_photo_required")),
        )

    def enviar_foto_de_autor(
        self,
        *,
        referencia: str,
        conteudo: bytes,
        nome_do_arquivo: str,
        sha256: str,
    ) -> dict:
        """Entrega a foto de perfil na rota de arquivos do no.

        Multipart, e nao JSON com base64: o corpo assinado ja e o arquivo bruto,
        e base64 inflaria em 33% um envio que existe justamente por ser grande
        demais para caber no corpo da publicacao.

        A rota e assincrona do lado do no — ela aceita e processa depois. Nao ha
        URL para guardar aqui, so a confirmacao de recebimento.
        """
        return self._requisitar(
            "POST",
            "/author-photos/",
            data={"author_reference": referencia, "sha256": sha256},
            files={"photo": (nome_do_arquivo, conteudo, "image/webp")},
        )

    def reconciliar(self, idempotency_key: str) -> RespostaDePublicacao | None:
        """Pergunta ao site se um envio anterior chegou.

        Chamado antes da primeira nova tentativa apos um timeout: e possivel
        que o conteudo tenha sido gravado e apenas a resposta tenha se perdido.
        """
        try:
            dados = self._requisitar(
                "GET", "/publications/", params={"idempotency_key": str(idempotency_key)}
            )
        except SitePermanentError:
            return None

        itens = dados.get("results") or []
        if not itens:
            return None

        primeiro = itens[0]
        return RespostaDePublicacao(
            status="already_exists",
            remote_id=str(primeiro.get("remote_id", "")),
            url=primeiro.get("url", ""),
            slug=primeiro.get("slug", ""),
            post_status=primeiro.get("post_status", ""),
            published_at=primeiro.get("published_at"),
            ja_existia=True,
        )


def _ler_retry_after(resposta: httpx.Response) -> int | None:
    valor = resposta.headers.get("Retry-After")
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        return None
