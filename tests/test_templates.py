"""Armadilhas de template que o Django nao acusa."""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_nenhum_comentario_curto_ocupa_mais_de_uma_linha():
    """`{# ... #}` so vale numa linha. Quebrado em duas, o Django nao da erro:
    imprime o texto na pagina. Ja apareceu no meio do menu."""
    problemas = []
    for arquivo in (RAIZ / "templates").rglob("*.html"):
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if linha.count("{#") > linha.count("#}"):
                problemas.append(f"{arquivo.relative_to(RAIZ)}:{numero}")
    assert not problemas, "use {% comment %} em: " + ", ".join(problemas)
