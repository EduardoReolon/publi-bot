"""Sanitizacao do HTML recebido.

O PubliBot ja sanitiza antes de enviar. Esta camada existe assim mesmo porque
**quem grava e o responsavel final pelo que sai na propria pagina**: um cliente
nao deve depender da correcao de um sistema de terceiro para nao servir script
malicioso aos proprios visitantes.
"""

from __future__ import annotations

import nh3

TAGS_PERMITIDAS = {
    "p",
    "br",
    "hr",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "s",
    "blockquote",
    "code",
    "pre",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "figure",
    "figcaption",
    "span",
    "div",
}

ATRIBUTOS_PERMITIDOS = {
    "a": {"href", "title", "rel", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "th": {"scope", "colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}

ESQUEMAS_PERMITIDOS = {"http", "https", "mailto"}

# Presenca destas tags e motivo para recusar com 422, em vez de apenas
# remover: indicam que algo esta errado na origem, e aceitar em silencio
# esconderia o problema.
TAGS_QUE_RECUSAM = ("<script", "<iframe", "<object", "<embed", "<style")


class ConteudoRecusado(ValueError):
    """O HTML contem algo que nao deve ser aceito nem sanitizado."""


def sanitizar(html: str) -> str:
    minusculo = (html or "").lower()
    for proibida in TAGS_QUE_RECUSAM:
        if proibida in minusculo:
            raise ConteudoRecusado(f"conteudo contem {proibida}>")

    return nh3.clean(
        html or "",
        tags=TAGS_PERMITIDAS,
        attributes=ATRIBUTOS_PERMITIDOS,
        url_schemes=ESQUEMAS_PERMITIDOS,
        # Obrigatorio quando `rel` esta entre os atributos permitidos de <a>:
        # sem isto o nh3 aborta o processo (nao levanta excecao, aborta) por
        # conflito com o `rel` que ele adicionaria sozinho.
        link_rel=None,
        strip_comments=True,
    )


def sanitizar_texto(valor: str, *, limite: int = 300) -> str:
    """Remove toda marcacao. Para titulo, nome de autor e afins."""
    return nh3.clean(valor or "", tags=set())[:limite]
