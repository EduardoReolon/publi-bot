"""Classificacao dos erros do contrato.

A distincao entre retentavel e terminal e o ponto central. Sem ela:

* um `400` por payload invalido seria retentado para sempre;
* um `401` por chave rotacionada geraria tentativas infinitas em vez de alerta;
* o painel so conseguiria exibir "erro".
"""

from __future__ import annotations


class SiteError(Exception):
    """Falha ao falar com um site."""

    def __init__(self, mensagem: str, *, code: str = "", status: int | None = None):
        super().__init__(mensagem)
        self.code = code
        self.status = status


class SiteTransientError(SiteError):
    """Vale a pena tentar de novo: 5xx, 408, 429, timeout, conexao recusada."""

    def __init__(
        self,
        mensagem: str,
        *,
        code: str = "",
        status: int | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(mensagem, code=code, status=status)
        # Quando o site informa `Retry-After`, respeitar o valor dele e melhor
        # que aplicar o proprio backoff: ele sabe quando volta.
        self.retry_after = retry_after


class SitePermanentError(SiteError):
    """Nao adianta repetir: demais 4xx."""


class SiteAuthError(SitePermanentError):
    """Credencial recusada. Precisa de intervencao, nao de nova tentativa."""


# Codigos que o contrato define. Um envelope unico permite ao painel exibir a
# causa em vez de apenas "erro".
CODIGOS = {
    "invalid_payload": 400,
    "invalid_api_key": 401,
    "signature_expired": 401,
    "signature_invalid": 401,
    "forbidden": 403,
    "not_found": 404,
    "duplicate_idempotency_key": 409,
    "payload_too_large": 413,
    "content_rejected": 422,
    "rate_limited": 429,
    "temporarily_unavailable": 503,
}

# Normativo: 5xx, 408 e 429 sao retentaveis; os demais 4xx sao terminais.
STATUS_RETENTAVEIS = frozenset({408, 425, 429, 500, 502, 503, 504})


def classificar(
    status: int, *, code: str = "", mensagem: str = "", retry_after: int | None = None
) -> SiteError:
    """Transforma uma resposta de erro na excecao certa."""
    texto = mensagem or f"HTTP {status}"

    if status in (401, 403):
        return SiteAuthError(texto, code=code or "invalid_api_key", status=status)

    if status in STATUS_RETENTAVEIS:
        return SiteTransientError(
            texto,
            code=code or "temporarily_unavailable",
            status=status,
            retry_after=retry_after,
        )

    return SitePermanentError(texto, code=code or "invalid_payload", status=status)
