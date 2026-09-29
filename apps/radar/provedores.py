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
    # Bloco "Google Academico" (so a DataForSEO traz): titulo, url, autor, descricao.
    academicos: list[dict] = field(default_factory=list)
    # Bloco "Principais noticias": quem o Google trata como imprensa neste tema.
    noticias: list[dict] = field(default_factory=list)
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


def saldo_dataforseo(contas: ContasExternas) -> Decimal | None:
    """O saldo da conta, pela rota gratuita de dados do usuario.

    None quando a resposta nao traz o saldo. Levanta `ProvedorIndisponivel` se
    a conta recusar ou nao responder.
    """
    login, senha = _credenciais_dataforseo(contas)
    try:
        resposta = httpx.get(
            f"{DATAFORSEO_BASE}/appendix/user_data", auth=(login, senha), timeout=15.0
        )
    except httpx.HTTPError as exc:
        raise ProvedorIndisponivel(f"DataForSEO nao respondeu: {exc}") from exc
    conferir_http_dataforseo(resposta)
    tarefa = (resposta.json().get("tasks") or [{}])[0]
    if tarefa.get("status_code") != 20000:
        raise ProvedorIndisponivel(
            f"{tarefa.get('status_code')} {tarefa.get('status_message', '')}"
        )
    saldo = ((tarefa.get("result") or [{}])[0].get("money") or {}).get("balance")
    return Decimal(str(saldo)) if saldo is not None else None


def atualizar_saldo_dataforseo() -> None:
    """Le e guarda o saldo. Nunca falha: e so para a tela."""
    from django.utils import timezone

    contas = ContasExternas.carregar()
    if not contas.tem_dataforseo:
        return
    try:
        saldo = saldo_dataforseo(contas)
    except ProvedorIndisponivel as exc:
        logger.info("saldo da DataForSEO nao lido: %s", exc)
        return
    if saldo is not None:
        ContasExternas.objects.filter(pk=contas.pk).update(
            saldo_dataforseo=saldo, saldo_em=timezone.now()
        )


def conferir_http_dataforseo(resposta: httpx.Response) -> None:
    """Levanta com a mensagem que a DataForSEO mandou, e nao so o numero.

    "HTTP 403" sozinho nao diz se e conta sem API liberada, IP fora da lista
    permitida ou saldo; o corpo costuma dizer.
    """
    if resposta.is_success:
        return
    try:
        corpo = resposta.json()
        detalhe = f"{corpo.get('status_code', '')} {corpo.get('status_message', '')}".strip()
    except ValueError:
        detalhe = resposta.text[:200].strip()
    dicas = {
        401: "login ou API password errados (a API password nao e a senha do site)",
        402: "sem saldo na conta",
        403: (
            "acesso recusado: confira no painel da DataForSEO se a conta esta "
            "ativada e se ha lista de IPs permitidos que nao inclui este servidor"
        ),
    }
    partes = [f"DataForSEO respondeu HTTP {resposta.status_code}"]
    if detalhe:
        partes.append(detalhe)
    if resposta.status_code in dicas:
        partes.append(dicas[resposta.status_code])
    raise ProvedorIndisponivel(" — ".join(partes) + ".")


def _post_dataforseo(caminho: str, corpo: list[dict], contas: ContasExternas) -> dict:
    login, senha = _credenciais_dataforseo(contas)
    try:
        resposta = httpx.post(
            f"{DATAFORSEO_BASE}{caminho}", json=corpo, auth=(login, senha), timeout=TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise ProvedorIndisponivel(f"DataForSEO nao respondeu: {exc}") from exc
    conferir_http_dataforseo(resposta)
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
    consulta: str,
    *,
    config: ConfiguracaoDoRadar,
    contas: ContasExternas,
    finalidade: str,
    local: int | None = None,
) -> ResultadoDeBusca:
    """Pagina de resultados do Google: organicos, perguntas e relacionadas.

    `local` e a regiao; sem ele, a primeira das escolhidas (ou o pais).
    """
    custos.conferir_teto(custos.ESTIMATIVAS[("dataforseo", "serp")])
    corpo = [
        {
            "keyword": consulta,
            "location_code": local or config.local_principal,
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
        elif tipo == "scholarly_articles":
            # O bloco "Google Academico" que o Google poe em buscas de tema de
            # pesquisa: ate uns 3 artigos, com link para o Scholar. Ja veio
            # nesta busca paga; aproveitar nao custa nada.
            for artigo in bloco.get("items") or []:
                if artigo.get("title"):
                    resultado.academicos.append(
                        {
                            "titulo": artigo["title"],
                            "url": artigo.get("url") or "",
                            "autor": (artigo.get("author") or "").strip("\u200e "),
                            "descricao": artigo.get("description") or "",
                        }
                    )
        elif tipo == "top_stories":
            for noticia in bloco.get("items") or []:
                url = noticia.get("url") or ""
                dominio = noticia.get("domain") or urlparse(url).hostname or ""
                if dominio:
                    resultado.noticias.append(
                        {
                            "dominio": dominio.lower().removeprefix("www."),
                            "url": url,
                            "titulo": noticia.get("title") or "",
                            "fonte": noticia.get("source") or "",
                        }
                    )
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


def ler_metricas(tarefa: dict) -> dict[str, dict]:
    """Volume, historico mensal e custo por clique de cada palavra.

    O historico e o custo por clique vem na MESMA resposta do volume, sem
    custo a mais: o historico mede crescimento, e o custo por clique mostra
    onde empresas ja pagam para alcancar quem busca.
    """
    metricas = {}
    for item in tarefa.get("result") or []:
        if item.get("keyword") is None:
            continue
        meses = [
            [int(m["year"]), int(m["month"]), int(m.get("search_volume") or 0)]
            for m in item.get("monthly_searches") or []
            if m.get("year") and m.get("month")
        ]
        metricas[item["keyword"].lower()] = {
            "volume": (
                int(item["search_volume"]) if item.get("search_volume") is not None else None
            ),
            "cpc": float(item["cpc"]) if item.get("cpc") is not None else None,
            "competicao": item.get("competition_index"),
            "meses": sorted(meses),
        }
    return metricas


def corpo_de_volume(palavras: list[str], *, config: ConfiguracaoDoRadar, local: int) -> dict:
    """A tarefa de volume, com dois anos de historico mensal.

    Dois anos, e nao o padrao de doze meses: crescimento se mede comparando um
    mes com o MESMO mes do ano anterior, senao todo tema sazonal parece
    novidade no seu pico.
    """
    import datetime

    hoje = datetime.date.today()
    return {
        "keywords": palavras[:1000],
        "location_code": local,
        "language_code": config.codigo_de_idioma,
        "date_from": hoje.replace(year=hoje.year - 2, day=1).isoformat(),
    }


def metricas_dataforseo(
    palavras: list[str],
    *,
    config: ConfiguracaoDoRadar,
    contas: ContasExternas,
    finalidade: str,
    local: int | None = None,
) -> dict[str, dict]:
    """Volume mensal de busca (o do Planejador do Google Ads), com historico.

    Ate 1000 palavras numa tarefa, e o custo e por tarefa: por isso quem chama
    junta tudo numa chamada so. As chaves do retorno estao na forma de
    `palavra_para_volume`.
    """
    palavras = list(dict.fromkeys(filter(None, map(palavra_para_volume, palavras))))[:1000]
    if not palavras:
        return {}
    custos.conferir_teto(custos.ESTIMATIVAS[("dataforseo", "volume")])
    corpo = [corpo_de_volume(palavras, config=config, local=local or config.local_principal)]
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

    metricas = ler_metricas(dados["tasks"][0])
    from apps.radar.valor import guardar_custos

    guardar_custos(metricas, local=local or config.local_principal)

    custos.registrar(
        provedor=ChamadaExterna.Provedor.DATAFORSEO,
        endpoint="keywords_data/google_ads/search_volume/live",
        finalidade=finalidade,
        consulta=f"{len(palavras)} palavras",
        custo=dados.get("cost") or 0,
        itens=len(metricas),
    )
    return metricas


def volume_dataforseo(palavras: list[str], **kwargs) -> dict[str, int]:
    """So o volume de cada palavra. Ver `metricas_dataforseo`."""
    return {
        p: m["volume"]
        for p, m in metricas_dataforseo(palavras, **kwargs).items()
        if m["volume"] is not None
    }


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
def buscador_efetivo(config: ConfiguracaoDoRadar, contas: ContasExternas) -> str:
    """O buscador que vai de fato atender.

    SearXNG escolhido sem instancia configurada e DataForSEO disfarcada: cada
    busca registraria uma falha do gratuito antes de cair no pago. Com conta
    na DataForSEO, vai direto a ela.
    """
    if (
        config.buscador == ConfiguracaoDoRadar.Buscador.SEARXNG
        and not url_do_searxng(contas)
        and contas.tem_dataforseo
    ):
        return ConfiguracaoDoRadar.Buscador.DATAFORSEO
    return config.buscador


def buscar(consulta: str, *, finalidade: str) -> ResultadoDeBusca:
    """Busca com o provedor configurado, com recurso ao pago e comparacao.

    * buscador pago: vai direto a DataForSEO;
    * buscador gratuito: tenta o SearXNG; falhando, cai para a DataForSEO se
      houver conta. Com sucesso, sorteia se esta busca entra na comparacao.
    """
    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()

    if buscador_efetivo(config, contas) == ConfiguracaoDoRadar.Buscador.DATAFORSEO:
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
