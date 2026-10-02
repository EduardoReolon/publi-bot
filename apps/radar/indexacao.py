"""O artigo publicado esta no Google? A URL Inspection do Search Console diz.

O Google nao tem API para PEDIR a indexacao de um artigo (a Indexing API e so
para vaga de emprego e transmissao ao vivo; o "ping" do sitemap foi desligado
em 2023). O que funciona e o sitemap do site com `lastmod` real, e conferir:
artigo que nao entrou aparece na revisao com o motivo e o link da inspecao no
Search Console, onde o botao "Solicitar indexacao" fica a um clique.

Quando conferir (cota de 2.000 por dia por propriedade, folgada):
* depois de 1 dia no ar;
* fora do indice: todo dia nos primeiros 30 dias, depois uma vez por semana;
* no indice: uma vez por mes, para ver se saiu.
"""

from __future__ import annotations

import datetime
import logging

from django.db.models import Q
from django.utils import timezone

from apps.radar.provedores import ProvedorIndisponivel

logger = logging.getLogger("publibot.radar")

ESPERA_DEPOIS_DE_PUBLICAR = datetime.timedelta(days=1)
JANELA_DIARIA = datetime.timedelta(days=30)
DE_NOVO_FORA = datetime.timedelta(days=1)
DE_NOVO_FORA_DEPOIS = datetime.timedelta(days=7)
DE_NOVO_DENTRO = datetime.timedelta(days=30)
POR_RODADA = 30
# Depois disto fora do indice, o painel avisa: o normal e entrar em dias.
ATRASO = datetime.timedelta(days=7)


def _propriedade() -> str:
    from apps.radar.models import ConfiguracaoDoRadar
    from apps.radar.search_console import conta_de_servico

    if conta_de_servico() is None:
        return ""
    return ConfiguracaoDoRadar.carregar().propriedade_search_console


def ligada() -> bool:
    """Ha conta de servico e propriedade: da para conferir."""
    return bool(_propriedade())


def resumir(resultado: dict) -> dict:
    """O que a revisao mostra, a partir do `inspectionResult`."""
    status = resultado.get("indexStatusResult") or {}
    return {
        "indexada": status.get("verdict") == "PASS",
        "situacao": status.get("coverageState") or "",
        "rastreada_em": status.get("lastCrawlTime") or "",
        "canonical_do_google": status.get("googleCanonical") or "",
        "canonical_do_site": status.get("userCanonical") or "",
        "no_sitemap": bool(status.get("sitemap")),
        "link": resultado.get("inspectionResultLink") or "",
    }


def conferir(artigo) -> dict:
    """Consulta o Google e grava no artigo. Levanta ProvedorIndisponivel."""
    from apps.radar.search_console import inspecionar

    propriedade = _propriedade()
    if not propriedade:
        raise ProvedorIndisponivel(
            "configure o Search Console (conta de servico e propriedade na tela do Radar)."
        )
    if not artigo.published_url:
        raise ProvedorIndisponivel("o artigo ainda nao tem endereco publicado.")
    try:
        dados = resumir(inspecionar(propriedade, artigo.published_url))
    except ProvedorIndisponivel as exc:
        dados = {**(artigo.indexacao or {}), "erro": str(exc)}
        _gravar(artigo, dados)
        raise
    _gravar(artigo, dados)
    return dados


def _gravar(artigo, dados: dict) -> None:
    artigo.indexacao = dados
    artigo.indexacao_conferida_em = timezone.now()
    artigo.save(update_fields=["indexacao", "indexacao_conferida_em"])


def a_conferir(agora=None):
    """Os artigos no ar cuja vez de conferir chegou, os mais antigos primeiro."""
    from apps.content.models import Article

    agora = agora or timezone.now()
    no_ar = Article.objects.filter(
        status=Article.Status.PUBLISHED, published_at__lte=agora - ESPERA_DEPOIS_DE_PUBLICAR
    ).exclude(published_url="")
    fora = ~Q(indexacao__indexada=True)
    return no_ar.filter(
        Q(indexacao_conferida_em__isnull=True)
        | (
            fora
            & Q(published_at__gte=agora - JANELA_DIARIA)
            & Q(indexacao_conferida_em__lte=agora - DE_NOVO_FORA)
        )
        | (fora & Q(indexacao_conferida_em__lte=agora - DE_NOVO_FORA_DEPOIS))
        | (Q(indexacao__indexada=True) & Q(indexacao_conferida_em__lte=agora - DE_NOVO_DENTRO))
    ).order_by("indexacao_conferida_em", "published_at")


def conferir_pendentes(limite: int = POR_RODADA) -> int:
    """Uma rodada (tarefa diaria). Sem Search Console configurado, nada."""
    if not _propriedade():
        return 0
    total = 0
    for artigo in a_conferir()[:limite]:
        try:
            conferir(artigo)
        except ProvedorIndisponivel as exc:
            # Sem acesso, ou Google fora: os outros dariam o mesmo erro.
            logger.warning("Indexacao nao conferida (%s): %s", artigo.pk, exc)
            break
        total += 1
    return total


def fora_do_google(agora=None):
    """No ar ha mais de ATRASO e o Google respondeu que nao esta no indice."""
    from apps.content.models import Article

    agora = agora or timezone.now()
    return (
        Article.objects.filter(
            status=Article.Status.PUBLISHED,
            published_at__lte=agora - ATRASO,
            indexacao__has_key="situacao",
        )
        .exclude(indexacao__indexada=True)
        .exclude(published_url="")
    )


def ultimo_erro() -> str:
    """O erro da conferencia mais recente que falhou (acesso, Google fora), ou ""."""
    from apps.content.models import Article

    artigo = (
        Article.objects.filter(status=Article.Status.PUBLISHED, indexacao__has_key="erro")
        .order_by("-indexacao_conferida_em")
        .first()
    )
    return artigo.indexacao["erro"] if artigo else ""
