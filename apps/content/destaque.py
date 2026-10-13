"""O destaque no meio do artigo: a frase que resume a ideia mais importante de
uma secao, repetida em letra maior (o "olho" das revistas; em ingles, pull
quote). Quem le na diagonal pega a ideia so pelos destaques — e e o que faz
parar de rolar.

O modelo marca, na secao, uma linha propria comecando com `>> `. Aqui:

* no markdown, os 2 primeiros viram a marca `[[DESTAQUE]]...[[/DESTAQUE]]` (os
  demais saem: sao repeticao do texto, e destaque demais nao destaca nada);
* no HTML, ja sanitizado, a marca vira
  `<aside data-publibot="destaque"><p><strong>frase</strong></p></aside>`.

Site com a biblioteca antiga (sem o destaque) tira o `aside` e fica o
paragrafo em negrito: degrada para enfase, sem quebrar.
"""

from __future__ import annotations

import re

from apps.content.rendering import PADRAO_MARCADOR

MAXIMO = 2
_LINHA = re.compile(r"^[ \t]*>>[ \t]*(.+?)[ \t]*$", re.M)
_PARAGRAFO = re.compile(r"<p>\s*\[\[DESTAQUE\]\](.*?)\[\[/DESTAQUE\]\]\s*</p>", re.S)
_SOLTA = re.compile(r"\[\[/?DESTAQUE\]\]")


def marcar(markdown: str) -> str:
    """As linhas `>> frase` viram a marca (as 2 primeiras); as outras saem."""
    vistos = 0

    def trocar(achado: re.Match) -> str:
        nonlocal vistos
        frase = PADRAO_MARCADOR.sub("", achado.group(1)).strip().strip('"“”')
        vistos += 1
        if vistos > MAXIMO or not frase:
            return ""
        return f"\n[[DESTAQUE]]{frase}[[/DESTAQUE]]\n"

    return re.sub(r"\n{3,}", "\n\n", _LINHA.sub(trocar, markdown or ""))


def no_html(html: str) -> str:
    """A marca vira o elemento do site (depois da sanitizacao)."""
    html = _PARAGRAFO.sub(
        lambda a: f'<aside data-publibot="destaque"><p><strong>{a.group(1).strip()}</strong>'
        "</p></aside>",
        html,
    )
    return _SOLTA.sub("", html)


def contar(markdown: str) -> int:
    return len(_LINHA.findall(markdown or ""))
