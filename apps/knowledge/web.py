"""Buscar uma pagina da web e ficar com o texto que importa.

Dois cuidados, e cada um corresponde a um jeito conhecido de dar errado:

* **O destino.** O servidor faz a requisicao, entao uma URL digitada (ou
  achada numa busca) e superficie de SSRF: `http://169.254.169.254/` alcanca o
  servico de metadados da nuvem. Todo destino — inclusive cada redirecionamento
  — passa pela mesma conferencia de endereco publico.
* **O conteudo.** Uma pagina tem menu, rodape, anuncio, "leia tambem". A
  trafilatura separa o texto principal, e a htmldate (que vem com ela) acha a
  data de publicacao nos metadados, no JSON-LD ou na propria URL. Sem data, a
  validade da fonte conta de quando ela foi buscada.

O texto que sai daqui NUNCA vai direto para o indice: vira um documento que
passa pela curadoria, como qualquer arquivo enviado.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from django.core.exceptions import ValidationError

logger = logging.getLogger("publibot.knowledge")

LIMITE_DE_BYTES = 5 * 1024 * 1024
MAXIMO_DE_REDIRECIONAMENTOS = 5
TIMEOUT_SEGUNDOS = 20.0
AGENTE = "PubliBot/1.0 (+curadoria de fontes; contato pelo site do cliente)"


class PaginaIndisponivel(RuntimeError):
    """Nao foi possivel obter a pagina, ou ela nao tem texto aproveitavel."""


def conferir_destino(url: str) -> None:
    """Recusa esquema estranho e endereco interno. Levanta `PaginaIndisponivel`."""
    from apps.integrations.validators import NOMES_PROIBIDOS, _resolver

    partes = urlparse(url)
    if partes.scheme not in {"http", "https"}:
        raise PaginaIndisponivel(f"esquema nao permitido: {partes.scheme!r}")
    anfitriao = (partes.hostname or "").lower()
    if not anfitriao or anfitriao in NOMES_PROIBIDOS:
        raise PaginaIndisponivel("endereco sem host, ou proibido.")
    for endereco in _resolver(anfitriao):
        if not endereco.is_global or endereco.is_reserved:
            raise PaginaIndisponivel(f"o endereco resolve para rede interna ({endereco}).")


def baixar(url: str) -> tuple[bytes, str, str]:
    """Baixa a pagina, seguindo redirecionamentos conferidos um a um.

    Devolve (conteudo, url final, content-type).
    """
    atual = url
    for _ in range(MAXIMO_DE_REDIRECIONAMENTOS + 1):
        try:
            conferir_destino(atual)
        except ValidationError as exc:  # pragma: no cover - defensivo
            raise PaginaIndisponivel(str(exc)) from exc

        try:
            with httpx.Client(
                timeout=TIMEOUT_SEGUNDOS,
                follow_redirects=False,
                headers={"User-Agent": AGENTE, "Accept": "text/html,application/pdf,*/*"},
            ) as cliente:
                with cliente.stream("GET", atual) as resposta:
                    if resposta.is_redirect:
                        destino = resposta.headers.get("location", "")
                        if not destino:
                            raise PaginaIndisponivel("redirecionamento sem destino.")
                        atual = urljoin(atual, destino)
                        continue
                    if resposta.status_code >= 400:
                        raise PaginaIndisponivel(f"HTTP {resposta.status_code} em {atual}")
                    corpo = bytearray()
                    for pedaco in resposta.iter_bytes():
                        corpo.extend(pedaco)
                        if len(corpo) > LIMITE_DE_BYTES:
                            raise PaginaIndisponivel(
                                f"a pagina passa de {LIMITE_DE_BYTES // 1024 // 1024} MB."
                            )
                    tipo = resposta.headers.get("content-type", "")
                    return bytes(corpo), str(resposta.url), tipo
        except httpx.HTTPError as exc:
            raise PaginaIndisponivel(f"nao foi possivel buscar {atual}: {exc}") from exc

    raise PaginaIndisponivel("redirecionamentos demais.")


@dataclass(frozen=True)
class Pagina:
    markdown: str
    titulo: str
    autor: str
    data: datetime.date | None
    site: str


def extrair_pagina(html: bytes | str, *, url: str = "") -> Pagina:
    """Texto principal em Markdown, com titulo, autor, data e nome do site."""
    import trafilatura

    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    markdown = trafilatura.extract(
        html,
        url=url or None,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        favor_precision=True,
    )
    if not markdown or len(markdown.strip()) < 200:
        raise PaginaIndisponivel(
            "a pagina nao tem texto principal aproveitavel (pode depender de "
            "JavaScript, ou ser so uma listagem)."
        )

    metadados = trafilatura.extract_metadata(html, default_url=url or None)
    titulo = (getattr(metadados, "title", "") or "").strip()
    autor = (getattr(metadados, "author", "") or "").strip()
    site = (getattr(metadados, "sitename", "") or "").strip()
    data = _data(getattr(metadados, "date", "") or "")

    return Pagina(
        markdown=markdown.strip(),
        titulo=titulo[:500],
        autor=autor[:300],
        data=data,
        site=site[:200],
    )


def _data(valor: str) -> datetime.date | None:
    try:
        data = datetime.date.fromisoformat(valor[:10])
    except ValueError:
        return None
    # Data no futuro e erro de metadado, nao publicacao.
    return data if data <= datetime.date.today() else None
