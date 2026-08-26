"""Conversao de Markdown para HTML seguro, e insercao determinista de links.

Duas defesas moram aqui, e as duas existem por causa da mesma propriedade
incomoda do produto: **ele insere links de saida como funcionalidade central**.
Um backlink editorial num site tematico e exatamente o objetivo mais valioso de
quem tentaria manipular o sistema — e o sistema faz isso por conta propria.
Basta convence-lo de qual URL usar.

**1. O modelo nunca ve uma URL.**
O prompt manda emitir marcadores `[[FONTE_1]]`, e a substituicao acontece
depois, aqui, com a URL vinda do documento confirmado por um humano. Isso
elimina de uma vez dois problemas: a URL alucinada ou truncada (comum em
modelos locais quantizados, e um link quebrado num artigo e pior que link
nenhum) e a injecao via prompt, porque nao existe caminho pelo qual um texto
consiga fazer o modelo emitir um destino.

**2. Nada sai sem sanitizacao com lista de permissao.**
O HTML gerado por modelo vai direto para o site de um terceiro. Sem
sanitizacao, uma unica saida maliciosa vira script permanente em todas as
paginas daquele site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import markdown as markdown_lib
import nh3

# Marcador que o prompt manda o modelo emitir no lugar de um link.
PADRAO_MARCADOR = re.compile(r"\[\[FONTE_(\d+)\]\]")

# Qualquer coisa parecida com URL escrita pelo proprio modelo.
PADRAO_URL_SOLTA = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

# Tags aceitas no corpo publicado. Lista de PERMISSAO: o que nao esta aqui e
# removido. Uma lista de proibicao erraria por omissao a cada tag nova.
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

# `javascript:` e `data:` ficam de fora: sao os vetores classicos de script em
# atributo de link.
ESQUEMAS_PERMITIDOS = {"http", "https", "mailto"}


class LinkAlucinado(ValueError):
    """O modelo escreveu uma URL por conta propria.

    Nunca deveria acontecer: o prompt pede marcadores. Quando acontece, ou o
    prompt falhou, ou algo tentou induzir o modelo — e nos dois casos o texto
    nao pode ser publicado como esta.
    """


class MarcadorSemFonte(ValueError):
    """O texto referencia uma fonte que nao foi recuperada."""


@dataclass(frozen=True)
class Fonte:
    """Uma fonte confirmada, pronta para virar link."""

    url: str
    anchor: str


def validar_saida_do_modelo(texto: str, *, max_marcadores: int = 2) -> list[int]:
    """Confere que o modelo respeitou o contrato de marcadores.

    Devolve os indices usados. Levanta se houver URL solta ou marcadores demais.
    """
    soltas = PADRAO_URL_SOLTA.findall(texto)
    if soltas:
        raise LinkAlucinado(
            f"o modelo escreveu {len(soltas)} URL(s) diretamente: {soltas[:3]}. "
            f"O prompt exige marcadores [[FONTE_N]]."
        )

    indices = [int(n) for n in PADRAO_MARCADOR.findall(texto)]
    distintos = sorted(set(indices))
    if len(distintos) > max_marcadores:
        raise LinkAlucinado(
            f"o texto usa {len(distintos)} fontes distintas e o limite e "
            f"{max_marcadores}. Excesso de links de saida descaracteriza a "
            f"curadoria que o formato imita."
        )
    return distintos


def substituir_marcadores(texto: str, fontes: dict[int, Fonte]) -> str:
    """Troca `[[FONTE_N]]` por Markdown de link, com URL vinda do banco.

    Esta e a unica funcao do sistema que insere uma URL num texto gerado. A URL
    vem de `fontes`, montado a partir de documentos confirmados por humano —
    nunca de algo que o modelo tenha produzido.
    """

    def trocar(achado: re.Match) -> str:
        indice = int(achado.group(1))
        fonte = fontes.get(indice)
        if fonte is None:
            raise MarcadorSemFonte(
                f"o texto cita [[FONTE_{indice}]], mas essa fonte nao esta entre "
                f"as recuperadas ({sorted(fontes)})."
            )
        # Sem `rel="nofollow"`: a ausencia do atributo E o comportamento
        # desejado. Nao existe `rel="dofollow"` em HTML — e um engano comum.
        return f"[{fonte.anchor}]({fonte.url})"

    return PADRAO_MARCADOR.sub(trocar, texto)


def markdown_para_html(texto: str) -> str:
    """Converte e sanitiza. As duas coisas juntas, sempre."""
    bruto = markdown_lib.markdown(
        texto,
        extensions=["tables", "fenced_code", "attr_list", "sane_lists", "nl2br"],
        output_format="html",
    )
    return sanitizar_html(bruto)


def sanitizar_html(html: str, *, dominios_permitidos: set[str] | None = None) -> str:
    """Aplica a lista de permissao e restringe os destinos de link.

    `dominios_permitidos`, quando informado, remove qualquer link para fora
    dessa lista. E a defesa contra um destino que tenha entrado por outro
    caminho: mesmo que uma URL escape das camadas anteriores, ela precisa
    pertencer a um documento confirmado por um humano para sobreviver aqui.
    """
    limpo = nh3.clean(
        html,
        tags=TAGS_PERMITIDAS,
        attributes=ATRIBUTOS_PERMITIDOS,
        url_schemes=ESQUEMAS_PERMITIDOS,
        link_rel=None,
        strip_comments=True,
    )
    if dominios_permitidos is not None:
        limpo = _remover_links_fora_da_lista(limpo, dominios_permitidos)
    return limpo


def _remover_links_fora_da_lista(html: str, dominios: set[str]) -> str:
    """Desfaz ancoras cujo destino nao esta na lista, preservando o texto."""
    from urllib.parse import urlparse

    def avaliar(achado: re.Match) -> str:
        href = achado.group("href")
        anfitriao = (urlparse(href).hostname or "").lower()
        anfitriao = anfitriao.removeprefix("www.")
        if anfitriao in dominios:
            return achado.group(0)
        return achado.group("texto")

    padrao = re.compile(
        r'<a\s[^>]*href="(?P<href>[^"]*)"[^>]*>(?P<texto>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    return padrao.sub(avaliar, html)


def contar_palavras(texto: str) -> int:
    return len(re.findall(r"\b\w+\b", texto))


def proporcao_editada(original: str, publicado: str) -> float:
    """Quanto o humano mudou, entre 0 e 1.

    Proximo de zero significa que a revisao foi carimbo. Este numero nao
    bloqueia nada — ele existe para ser olhado.
    """
    import difflib

    if not original and not publicado:
        return 0.0
    if not original:
        return 1.0

    proporcao_igual = difflib.SequenceMatcher(None, original, publicado).ratio()
    return round(1.0 - proporcao_igual, 4)


def verificar_sobreposicao_literal(
    gerado: str, fontes: list[str], *, tamanho_ngrama: int = 8
) -> list[str]:
    """Procura trechos copiados quase ao pe da letra das fontes.

    E controle de qualidade e de risco autoral ao mesmo tempo: com um resumo de
    250 palavras como unica fonte e um modelo local quantizado, a copia quase
    literal e resultado provavel, nao excecao.

    Limitacao conhecida e importante: **so funciona quando o artigo e a fonte
    compartilham idioma**. Com fonte em ingles e artigo em portugues a
    sobreposicao de n-gramas e estruturalmente zero, e esta protecao nao atua.
    """

    def ngramas(texto: str) -> set[str]:
        palavras = re.findall(r"\w+", texto.lower())
        return {
            " ".join(palavras[i : i + tamanho_ngrama])
            for i in range(len(palavras) - tamanho_ngrama + 1)
        }

    do_gerado = ngramas(gerado)
    if not do_gerado:
        return []

    encontrados: set[str] = set()
    for fonte in fontes:
        encontrados |= do_gerado & ngramas(fonte)
    return sorted(encontrados)
