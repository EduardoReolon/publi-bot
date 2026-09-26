"""Buscadores e dados de palavra-chave, atras de uma interface so.

Dois buscadores, trocaveis na tela:

* **SearXNG** — meta-buscador auto-hospedado, gratuito. Devolve resultados e
  sugestoes; NAO devolve "as pessoas tambem perguntam". Os buscadores que ele
  consulta bloqueiam de vez em quando, e por isso a falha dele cai para a
  DataForSEO quando o tenant tem conta.
* **DataForSEO** — pago por chamada, estavel, com a pagina de resultados do
  Google inteira (inclusive as perguntas relacionadas).

Com o gratuito em uso, uma fracao configuravel das buscas e repetida no pago
e as duas listas sao comparadas (`ComparacaoDeBusca`). E o que responde, com
numero, se o gratuito serve para aquele tenant.

Toda chamada — paga ou nao, com sucesso ou nao — vai para o livro-caixa.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.radar import custos
from apps.radar.models import ChamadaExterna, ComparacaoDeBusca, ConfiguracaoDoRadar, ContasExternas

logger = logging.getLogger("publibot.radar")

DATAFORSEO_BASE = "https://api.dataforseo.com/v3"
TIMEOUT = 60.0


class ProvedorIndisponivel(RuntimeError):
    """O provedor nao respondeu, recusou, ou nao esta configurado."""


@dataclass(frozen=True)
class ItemDeBusca:
    url: str
    titulo: str
    trecho: str = ""


@dataclass
class ResultadoDeBusca:
    provedor: str
    resultados: list[ItemDeBusca] = field(default_factory=list)
    perguntas: list[str] = field(default_factory=list)
    relacionadas: list[str] = field(default_factory=list)
    custo: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# DataForSEO
# ---------------------------------------------------------------------------
def _credenciais_dataforseo(contas: ContasExternas) -> tuple[str, str]:
    from apps.inference.security import decifrar

    senha = decifrar(contas.dataforseo_senha_ciphertext) if contas.tem_dataforseo else None
    if not contas.dataforseo_login or not senha:
        raise ProvedorIndisponivel(
            "a conta da DataForSEO nao esta configurada (Radar > Contas externas)."
        )
    return contas.dataforseo_login, senha


def _post_dataforseo(caminho: str, corpo: list[dict], contas: ContasExternas) -> dict:
    login, senha = _credenciais_dataforseo(contas)
    try:
        resposta = httpx.post(
            f"{DATAFORSEO_BASE}{caminho}", json=corpo, auth=(login, senha), timeout=TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise ProvedorIndisponivel(f"DataForSEO nao respondeu: {exc}") from exc
    if resposta.status_code == 401:
        raise ProvedorIndisponivel("DataForSEO recusou o login e a senha (401).")
    if not resposta.is_success:
        raise ProvedorIndisponivel(f"DataForSEO respondeu HTTP {resposta.status_code}.")
    dados = resposta.json()
    # A DataForSEO responde 200 com o erro no corpo: 20000 e sucesso, o resto
    # nao. Olhar so o HTTP faria um saldo zerado passar por resultado vazio.
    if dados.get("status_code") != 20000:
        raise ProvedorIndisponivel(
            f"DataForSEO: {dados.get('status_code')} {dados.get('status_message', '')}"
        )
    tarefa = (dados.get("tasks") or [{}])[0]
    if tarefa.get("status_code") != 20000:
        raise ProvedorIndisponivel(
            f"DataForSEO: {tarefa.get('status_code')} {tarefa.get('status_message', '')}"
        )
    return dados


def buscar_dataforseo(
    consulta: str, *, config: ConfiguracaoDoRadar, contas: ContasExternas, finalidade: str
) -> ResultadoDeBusca:
    """Pagina de resultados do Google: organicos, perguntas e relacionadas."""
    custos.conferir_teto(custos.ESTIMATIVAS[("dataforseo", "serp")])
    corpo = [
        {
            "keyword": consulta,
            "location_code": config.codigo_de_local,
            "language_code": config.codigo_de_idioma,
            "device": "desktop",
            "depth": 10,
        }
    ]
    try:
        dados = _post_dataforseo("/serp/google/organic/live/advanced", corpo, contas)
    except ProvedorIndisponivel as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.DATAFORSEO,
            endpoint="serp/google/organic/live/advanced",
            finalidade=finalidade,
            consulta=consulta,
            sucesso=False,
            erro=str(exc),
        )
        raise

    resultado = ler_serp(dados["tasks"][0])
    resultado.custo = Decimal(str(dados.get("cost") or 0))

    custos.registrar(
        provedor=ChamadaExterna.Provedor.DATAFORSEO,
        endpoint="serp/google/organic/live/advanced",
        finalidade=finalidade,
        consulta=consulta,
        custo=resultado.custo,
        itens=len(resultado.resultados) + len(resultado.perguntas),
    )
    return resultado


def ler_serp(tarefa: dict) -> ResultadoDeBusca:
    """Le a tarefa de SERP (ao vivo ou colhida da fila: o formato e o mesmo)."""
    resultado = ResultadoDeBusca(provedor="dataforseo")
    for bloco in ((tarefa.get("result") or [{}])[0] or {}).get("items") or []:
        tipo = bloco.get("type")
        if tipo == "organic" and bloco.get("url"):
            resultado.resultados.append(
                ItemDeBusca(
                    url=bloco["url"],
                    titulo=bloco.get("title") or "",
                    trecho=bloco.get("description") or "",
                )
            )
        elif tipo == "people_also_ask":
            for pergunta in bloco.get("items") or []:
                if pergunta.get("title"):
                    resultado.perguntas.append(pergunta["title"])
        elif tipo == "related_searches":
            for relacionada in bloco.get("items") or []:
                texto = relacionada if isinstance(relacionada, str) else relacionada.get("title")
                if texto:
                    resultado.relacionadas.append(texto)
    return resultado


# O Google Ads recusa estes simbolos na palavra-chave, e UMA palavra invalida
# derruba a tarefa inteira. Pergunta ("como calcular o bdi?") e o caso comum.
_SIMBOLOS_RECUSADOS = re.compile(r"[,!@%^()={};~`<>?\\|*\[\]\"'.:+#$&]")


def palavra_para_volume(texto: str) -> str | None:
    """A forma que vai para o volume de busca, ou None se nao couber.

    Limites do Google Ads: ate 80 caracteres e 10 palavras.
    """
    limpa = " ".join(_SIMBOLOS_RECUSADOS.sub(" ", texto.lower()).split())
    if not limpa or len(limpa) > 80 or len(limpa.split()) > 10:
        return None
    return limpa


def ler_volumes(tarefa: dict) -> dict[str, int]:
    volumes = {}
    for item in tarefa.get("result") or []:
        if item.get("keyword") is not None and item.get("search_volume") is not None:
            volumes[item["keyword"].lower()] = int(item["search_volume"])
    return volumes


def volume_dataforseo(
    palavras: list[str], *, config: ConfiguracaoDoRadar, contas: ContasExternas, finalidade: str
) -> dict[str, int]:
    """Volume mensal de busca, o mesmo do Planejador do Google Ads.

    Ate 1000 palavras numa tarefa, e o custo e por tarefa: por isso quem chama
    junta tudo numa chamada so. As chaves do retorno estao na forma de
    `palavra_para_volume`.
    """
    palavras = list(dict.fromkeys(filter(None, map(palavra_para_volume, palavras))))[:1000]
    if not palavras:
        return {}
    custos.conferir_teto(custos.ESTIMATIVAS[("dataforseo", "volume")])
    corpo = [
        {
            "keywords": palavras,
            "location_code": config.codigo_de_local,
            "language_code": config.codigo_de_idioma,
        }
    ]
    try:
        dados = _post_dataforseo("/keywords_data/google_ads/search_volume/live", corpo, contas)
    except ProvedorIndisponivel as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.DATAFORSEO,
            endpoint="keywords_data/google_ads/search_volume/live",
            finalidade=finalidade,
            consulta=f"{len(palavras)} palavras",
            sucesso=False,
            erro=str(exc),
        )
        raise

    volumes = ler_volumes(dados["tasks"][0])

    custos.registrar(
        provedor=ChamadaExterna.Provedor.DATAFORSEO,
        endpoint="keywords_data/google_ads/search_volume/live",
        finalidade=finalidade,
        consulta=f"{len(palavras)} palavras",
        custo=dados.get("cost") or 0,
        itens=len(volumes),
    )
    return volumes


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------
def url_do_searxng(contas: ContasExternas) -> str:
    return (contas.searxng_url or getattr(settings, "SEARXNG_URL", "") or "").rstrip("/")


def buscar_searxng(
    consulta: str, *, config: ConfiguracaoDoRadar, contas: ContasExternas, finalidade: str
) -> ResultadoDeBusca:
    base = url_do_searxng(contas)
    if not base:
        raise ProvedorIndisponivel("nenhuma instancia do SearXNG configurada (SEARXNG_URL).")

    idioma = "pt-BR" if config.codigo_de_idioma == "pt" else config.codigo_de_idioma
    try:
        resposta = httpx.get(
            f"{base}/search",
            params={"q": consulta, "format": "json", "language": idioma},
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except (httpx.HTTPError, ValueError) as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.SEARXNG,
            endpoint="search",
            finalidade=finalidade,
            consulta=consulta,
            sucesso=False,
            erro=str(exc),
        )
        raise ProvedorIndisponivel(f"SearXNG nao respondeu: {exc}") from exc

    resultado = ResultadoDeBusca(provedor="searxng")
    for item in dados.get("results") or []:
        if item.get("url"):
            resultado.resultados.append(
                ItemDeBusca(
                    url=item["url"],
                    titulo=item.get("title") or "",
                    trecho=item.get("content") or "",
                )
            )
    resultado.resultados = resultado.resultados[:10]
    resultado.relacionadas = [s for s in dados.get("suggestions") or [] if isinstance(s, str)]
    # Pergunta vinda dos proprios titulos: o SearXNG nao tem "as pessoas tambem
    # perguntam", mas pagina que responde duvida costuma trazer a pergunta no
    # titulo. E um substituto fraco, e esta assumido como tal.
    resultado.perguntas = [r.titulo for r in resultado.resultados if r.titulo.strip().endswith("?")]

    custos.registrar(
        provedor=ChamadaExterna.Provedor.SEARXNG,
        endpoint="search",
        finalidade=finalidade,
        consulta=consulta,
        itens=len(resultado.resultados),
    )
    return resultado


# ---------------------------------------------------------------------------
# A busca que o resto do sistema chama
# ---------------------------------------------------------------------------
def buscar(consulta: str, *, finalidade: str) -> ResultadoDeBusca:
    """Busca com o provedor configurado, com recurso ao pago e comparacao.

    * buscador pago: vai direto a DataForSEO;
    * buscador gratuito: tenta o SearXNG; falhando, cai para a DataForSEO se
      houver conta. Com sucesso, sorteia se esta busca entra na comparacao.
    """
    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()

    if config.buscador == ConfiguracaoDoRadar.Buscador.DATAFORSEO:
        return buscar_dataforseo(consulta, config=config, contas=contas, finalidade=finalidade)

    try:
        gratuito = buscar_searxng(consulta, config=config, contas=contas, finalidade=finalidade)
    except ProvedorIndisponivel as falha:
        if not contas.tem_dataforseo:
            raise
        logger.warning("SearXNG falhou (%s); usando a DataForSEO.", falha)
        pago = buscar_dataforseo(consulta, config=config, contas=contas, finalidade=finalidade)
        if config.taxa_de_comparacao:
            ComparacaoDeBusca.objects.create(
                consulta=consulta[:500], resultados_pago=len(pago.resultados), gratuito_falhou=True
            )
        return pago

    if (
        contas.tem_dataforseo
        and config.taxa_de_comparacao
        and random.random() * 100 < config.taxa_de_comparacao  # noqa: S311
    ):
        try:
            pago = buscar_dataforseo(
                consulta,
                config=config,
                contas=contas,
                finalidade=ChamadaExterna.Finalidade.COMPARACAO,
            )
        except (ProvedorIndisponivel, custos.TetoAtingido) as exc:
            logger.info("Comparacao pulada: %s", exc)
        else:
            comparar(consulta, gratuito, pago)
            # A busca paga ja foi feita e paga: usar a lista mais completa.
            gratuito.perguntas = list(dict.fromkeys(gratuito.perguntas + pago.perguntas))

    return gratuito


def _url_normalizada(url: str) -> str:
    partes = urlparse(url)
    anfitriao = (partes.hostname or "").lower().removeprefix("www.")
    return f"{anfitriao}{partes.path.rstrip('/')}"


def _dominio(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def comparar(consulta: str, gratuito: ResultadoDeBusca, pago: ResultadoDeBusca):
    """Quanto do top 10 pago o gratuito tambem trouxe, por URL e por dominio."""
    urls_pago = {_url_normalizada(r.url) for r in pago.resultados[:10]}
    urls_gratuito = {_url_normalizada(r.url) for r in gratuito.resultados[:10]}
    dom_pago = {_dominio(r.url) for r in pago.resultados[:10]}
    dom_gratuito = {_dominio(r.url) for r in gratuito.resultados[:10]}

    def fracao(a: set, b: set) -> float:
        return len(a & b) / len(a) if a else 0.0

    return ComparacaoDeBusca.objects.create(
        consulta=consulta[:500],
        gratuito=gratuito.provedor,
        resultados_gratuito=len(gratuito.resultados),
        resultados_pago=len(pago.resultados),
        sobreposicao_urls=fracao(urls_pago, urls_gratuito),
        sobreposicao_dominios=fracao(dom_pago, dom_gratuito),
        so_no_pago=sorted(dom_pago - dom_gratuito)[:10],
        so_no_gratuito=sorted(dom_gratuito - dom_pago)[:10],
    )
