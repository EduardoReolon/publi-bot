"""O que os concorrentes cobrem e o publico deles reclama.

E a "analise de lacuna de conteudo" (content gap) do mercado: temas em que os
concorrentes tem paginas, ou em que aparecem na busca, e o site ainda nao. A
lacuna em si nao e calculada aqui: cada pagina ou busca do concorrente vira um
SINAL, e o que ja existe no radar decide o resto — o agrupamento junta temas
parecidos, e a parcela de canibalizacao da nota derruba o que o site ja
escreveu. O que sobra com nota alta e a lacuna.

Tres fontes, da gratuita para as pagas:

1. **O sitemap** (gratuito). Todo site que quer ser achado publica a lista das
   proprias paginas (`/sitemap.xml`, apontado no `robots.txt`). O titulo sai
   do endereco (`/blog/como-calcular-bdi` -> "como calcular bdi"): nenhuma
   pagina do concorrente e baixada, so a lista. O XML e lido com `defusedxml`,
   porque vem de terceiro.
2. **As buscas em que o concorrente aparece** (DataForSEO Labs, pago). Ja vem
   com volume e posicao; e o "keyword gap" das ferramentas de SEO.
3. **As avaliacoes do concorrente no Google** (DataForSEO Business Data, pago,
   na fila). Nota baixa e pergunta viram sinal de dor do publico. Avaliacao
   sozinha nao vira pauta nem da titulo a um grupo: ela reforca um tema que
   outra fonte trouxe.
"""

from __future__ import annotations

import gzip
import logging
import re
from decimal import Decimal
from urllib.parse import unquote, urlparse

from defusedxml import ElementTree

from apps.radar import custos
from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas
from apps.radar.provedores import ProvedorIndisponivel, _post_dataforseo

logger = logging.getLogger("publibot.radar")

MAXIMO_DE_SITEMAPS = 10

# Trecho de caminho que nao e conteudo: listagem, conta, institucional.
_CAMINHOS_IGNORADOS = re.compile(
    r"/(tag|tags|categoria|categorias|category|categories|autor|author|page|pagina|"
    r"wp-content|wp-json|feed|carrinho|cart|checkout|minha-conta|my-account|login|"
    r"busca|search)(/|$)",
    re.IGNORECASE,
)
_PAGINAS_INSTITUCIONAIS = {
    "contato",
    "fale conosco",
    "sobre",
    "sobre nos",
    "quem somos",
    "politica de privacidade",
    "termos de uso",
    "termos",
    "privacidade",
    "trabalhe conosco",
    "home",
    "inicio",
    "blog",
}


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------
def _baixar(url: str) -> bytes | None:
    from apps.knowledge.web import PaginaIndisponivel, baixar

    try:
        conteudo, _url, _tipo = baixar(url)
    except PaginaIndisponivel as exc:
        logger.info("Concorrente: %s", exc)
        return None
    if conteudo[:2] == b"\x1f\x8b":
        try:
            conteudo = gzip.decompress(conteudo)
        except OSError:
            return None
    return conteudo


def _sitemaps_do_robots(dominio: str) -> list[str]:
    conteudo = _baixar(f"https://{dominio}/robots.txt") or b""
    return [
        linha.split(":", 1)[1].strip()
        for linha in conteudo.decode("utf-8", "replace").splitlines()
        if linha.lower().startswith("sitemap:")
    ]


def _nome(elemento) -> str:
    return elemento.tag.rsplit("}", 1)[-1]


def paginas_do_sitemap(dominio: str, *, limite: int) -> list[tuple[str, str]]:
    """(url, lastmod) das paginas do dominio, as mais recentes primeiro."""
    fila = _sitemaps_do_robots(dominio) or [
        f"https://{dominio}/sitemap.xml",
        f"https://{dominio}/sitemap_index.xml",
    ]
    vistos: set[str] = set()
    paginas: dict[str, str] = {}
    while fila and len(vistos) < MAXIMO_DE_SITEMAPS:
        endereco = fila.pop(0)
        if endereco in vistos:
            continue
        vistos.add(endereco)
        conteudo = _baixar(endereco)
        if not conteudo:
            continue
        try:
            raiz = ElementTree.fromstring(conteudo)
        except Exception as exc:  # XML quebrado ou recusado pelo defusedxml
            logger.info("Sitemap ilegivel em %s: %s", endereco, exc)
            continue
        for item in raiz:
            campos = {_nome(filho): (filho.text or "").strip() for filho in item}
            local = campos.get("loc", "")
            if not local:
                continue
            if _nome(item) == "sitemap":
                fila.append(local)
            elif _nome(item) == "url":
                anfitriao = (urlparse(local).hostname or "").lower().removeprefix("www.")
                if anfitriao == dominio:
                    paginas[local] = campos.get("lastmod", "")
    # Sem data vai para o fim; datas ISO ordenam como texto.
    ordenadas = sorted(paginas.items(), key=lambda p: p[1], reverse=True)
    return ordenadas[:limite]


def titulo_da_url(url: str) -> str | None:
    """O tema de uma pagina a partir do endereco, ou None se nao for conteudo."""
    caminho = urlparse(url).path
    if _CAMINHOS_IGNORADOS.search(caminho):
        return None
    pedaco = unquote(caminho.rstrip("/").rsplit("/", 1)[-1])
    pedaco = re.sub(r"\.(html?|php|aspx?)$", "", pedaco, flags=re.IGNORECASE)
    palavras = [p for p in re.split(r"[-_+\s]+", pedaco.lower()) if p]
    # Numero comprido e identificador (post 123456), nao tema. Ano fica.
    palavras = [p for p in palavras if not (p.isdigit() and len(p) != 4)]
    titulo = " ".join(palavras)
    if len(palavras) < 2 or titulo in _PAGINAS_INSTITUCIONAIS:
        return None
    return titulo


def colher_conteudo(config: ConfiguracaoDoRadar, *, rodada, limite: int) -> list:
    from apps.radar.coleta import _novo_sinal
    from apps.radar.models import SinalDeDemanda

    novos = []
    for concorrente in config.lista_de_concorrentes:
        dominio = concorrente["dominio"]
        paginas = paginas_do_sitemap(dominio, limite=limite)
        custos.registrar(
            provedor=ChamadaExterna.Provedor.WEB,
            endpoint="sitemap",
            finalidade=ChamadaExterna.Finalidade.CONCORRENTES,
            consulta=dominio,
            itens=len(paginas),
            sucesso=bool(paginas),
            erro="" if paginas else "nenhum sitemap legivel",
        )
        for url, data in paginas:
            titulo = titulo_da_url(url)
            if titulo:
                sinal = _novo_sinal(
                    titulo,
                    SinalDeDemanda.Fonte.CONCORRENTE_CONTEUDO,
                    rodada=rodada,
                    dominio=dominio,
                    url=url,
                    data=data,
                )
                if sinal is not None:
                    novos.append(sinal)
    return novos


# ---------------------------------------------------------------------------
# Buscas em que o concorrente aparece (DataForSEO Labs)
# ---------------------------------------------------------------------------
def colher_buscas(
    config: ConfiguracaoDoRadar, contas: ContasExternas, *, rodada, limite: int
) -> list:
    """Palavras em que cada concorrente esta entre os 20 primeiros.

    So existe ao vivo (o Labs nao tem fila). Ja vem com volume, entao estes
    sinais nao entram no pedido de volume da rodada.
    """
    from apps.radar.coleta import _novo_sinal
    from apps.radar.models import SinalDeDemanda

    caminho = "/dataforseo_labs/google/ranked_keywords/live"
    novos = []
    for concorrente in config.lista_de_concorrentes:
        dominio = concorrente["dominio"]
        custos.conferir_teto(custos.ESTIMATIVAS[("dataforseo", "labs")])
        corpo = [
            {
                "target": dominio,
                "location_code": config.codigo_de_local,
                "language_code": config.codigo_de_idioma,
                "limit": limite,
                "order_by": ["keyword_data.keyword_info.search_volume,desc"],
                "filters": [["ranked_serp_element.serp_item.rank_group", "<=", 20]],
            }
        ]
        try:
            dados = _post_dataforseo(caminho, corpo, contas)
        except ProvedorIndisponivel as exc:
            custos.registrar(
                provedor=ChamadaExterna.Provedor.DATAFORSEO,
                endpoint=caminho.lstrip("/"),
                finalidade=ChamadaExterna.Finalidade.CONCORRENTES,
                consulta=dominio,
                sucesso=False,
                erro=str(exc),
            )
            raise

        itens = ((dados["tasks"][0].get("result") or [{}])[0] or {}).get("items") or []
        custos.registrar(
            provedor=ChamadaExterna.Provedor.DATAFORSEO,
            endpoint=caminho.lstrip("/"),
            finalidade=ChamadaExterna.Finalidade.CONCORRENTES,
            consulta=dominio,
            custo=Decimal(str(dados.get("cost") or 0)),
            itens=len(itens),
        )
        for item in itens:
            palavra = (item.get("keyword_data") or {}).get("keyword")
            info = (item.get("keyword_data") or {}).get("keyword_info") or {}
            serp = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
            sinal = _novo_sinal(
                palavra,
                SinalDeDemanda.Fonte.CONCORRENTE_BUSCA,
                rodada=rodada,
                dominio=dominio,
                posicao=serp.get("rank_group"),
                url=serp.get("url") or "",
            )
            if sinal is not None:
                volume = info.get("search_volume")
                if volume is not None:
                    sinal.volume = int(volume)
                    sinal.save(update_fields=["volume"])
                novos.append(sinal)
    return novos


# ---------------------------------------------------------------------------
# Avaliacoes no Google (DataForSEO Business Data, so na fila)
# ---------------------------------------------------------------------------
NOTA_DE_RECLAMACAO = 3


def postar_avaliacoes(
    config: ConfiguracaoDoRadar, contas: ContasExternas, *, rodada, profundidade: int
) -> int:
    """Posta uma tarefa por concorrente com nome. Devolve quantas foram."""
    from apps.radar.fila import postar
    from apps.radar.models import TarefaNaFila

    tarefas = [
        (
            {
                "keyword": c["nome"],
                "location_code": config.codigo_de_local,
                "language_code": config.codigo_de_idioma,
                "depth": profundidade,
                "sort_by": "lowest_rating",
            },
            {"concorrente": c["nome"], "dominio": c["dominio"]},
        )
        for c in config.lista_de_concorrentes
        if c["nome"]
    ]
    if not tarefas:
        return 0
    return len(
        postar(
            TarefaNaFila.Tipo.AVALIACOES,
            tarefas,
            contas=contas,
            finalidade=ChamadaExterna.Finalidade.CONCORRENTES,
            rodada=rodada,
        )
    )


def ler_avaliacoes(resultado: dict, contexto: dict, *, rodada) -> list:
    """Reclamacao (nota ate 3) e pergunta viram sinal; elogio nao."""
    from apps.radar.coleta import _novo_sinal
    from apps.radar.models import SinalDeDemanda

    novos = []
    for item in ((resultado.get("result") or [{}])[0] or {}).get("items") or []:
        texto = (item.get("review_text") or "").strip()
        nota = (item.get("rating") or {}).get("value")
        if not texto:
            continue
        if (nota is not None and nota <= NOTA_DE_RECLAMACAO) or "?" in texto:
            sinal = _novo_sinal(
                texto,
                SinalDeDemanda.Fonte.AVALIACAO,
                rodada=rodada,
                concorrente=contexto.get("concorrente", ""),
                nota=nota,
            )
            if sinal is not None:
                novos.append(sinal)
    return novos
