"""Coleta de leitura e conversoes dos sites (recurso `insights` do contrato).

Uma vez por dia, para cada site que declara o recurso: pede os ultimos dias
(o site pode fechar a contagem de ontem com atraso) e grava por cima. Leitura
e o total do dia, entao substitui; conversao tem id, entao nao duplica.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from django.utils import timezone

from apps.integrations.models import ConversaoDoSite, LeituraDoDia, Site

logger = logging.getLogger("publibot.integrations")

# Na primeira coleta, quanto do passado pedir; depois, quantos dias refazer.
PRIMEIRA_JANELA = 90
DIAS_REFEITOS = 3
MAXIMO_DE_PAGINAS = 50
MAXIMO_NA_JORNADA = 20
CAMPOS_DE_LEITURA = (
    "views",
    "engaged_views",
    "engaged_seconds",
    "read_to_end",
    "cta_views",
    "cta_clicks",
)


def _inteiro(valor, teto: int = 10_000_000) -> int:
    try:
        return max(0, min(int(valor), teto))
    except (TypeError, ValueError):
        return 0


def _dia(valor) -> date | None:
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _canal(valor) -> str:
    """Valor desconhecido (versao futura do site) vira "outro", e vazio, vazio."""
    if not valor:
        return ""
    return valor if valor in ConversaoDoSite.Canal.values else ConversaoDoSite.Canal.OTHER


def _jornada(bruta) -> list[dict]:
    if not isinstance(bruta, list):
        return []
    itens = []
    for item in bruta[-MAXIMO_NA_JORNADA:]:
        if not isinstance(item, dict) or not item.get("remote_id"):
            continue
        itens.append(
            {
                "remote_id": str(item["remote_id"])[:120],
                "engaged_seconds": _inteiro(item.get("engaged_seconds"), 86_400),
            }
        )
    return itens


def gravar(site: Site, dados: dict) -> tuple[int, int]:
    """Grava uma pagina da resposta. Devolve (leituras, conversoes novas)."""
    leituras = 0
    for linha in dados.get("reading") or []:
        dia = _dia(linha.get("date"))
        remote_id = str(linha.get("remote_id") or "")[:120]
        if dia is None or not remote_id:
            continue
        LeituraDoDia.objects.update_or_create(
            site=site,
            remote_id=remote_id,
            dia=dia,
            defaults={campo: _inteiro(linha.get(campo)) for campo in CAMPOS_DE_LEITURA},
        )
        leituras += 1

    novas = 0
    for conversao in dados.get("conversions") or []:
        dia = _dia(conversao.get("date"))
        externo = str(conversao.get("id") or "")[:120]
        if dia is None or not externo:
            continue
        _, criada = ConversaoDoSite.objects.get_or_create(
            site=site,
            external_id=externo,
            defaults={
                "dia": dia,
                "tipo": str(conversao.get("kind") or "")[:40],
                "via_cta": bool(conversao.get("via_cta")),
                "jornada": _jornada(conversao.get("journey")),
                "canal_de_entrada": _canal(conversao.get("first_channel")),
                "canal_final": _canal(conversao.get("last_channel")),
            },
        )
        novas += int(criada)
    return leituras, novas


def coletar(site: Site) -> tuple[int, int]:
    from apps.integrations.client import SiteClient

    hoje = timezone.localdate()
    if site.insights_synced_at:
        desde = timezone.localdate(site.insights_synced_at) - timedelta(days=DIAS_REFEITOS)
    else:
        desde = hoje - timedelta(days=PRIMEIRA_JANELA)

    cliente = SiteClient(site)
    total_leituras = total_conversoes = 0
    cursor = ""
    for _pagina in range(MAXIMO_DE_PAGINAS):
        dados = cliente.insights(desde=desde, cursor=cursor) or {}
        leituras, conversoes = gravar(site, dados)
        total_leituras += leituras
        total_conversoes += conversoes
        cursor = dados.get("next_cursor") or ""
        if not cursor:
            break
    site.insights_synced_at = timezone.now()
    site.save(update_fields=["insights_synced_at"])
    return total_leituras, total_conversoes


def coletar_de_todos() -> int:
    """Os sites deste tenant que declaram `insights`."""
    total = 0
    for site in Site.objects.all():
        if not site.suporta("insights"):
            continue
        try:
            leituras, conversoes = coletar(site)
        except Exception:
            logger.exception("Falha ao coletar metricas do site %s.", site.pk)
            continue
        logger.info(
            "Site %s: %s linha(s) de leitura, %s conversao(oes) nova(s).",
            site.pk,
            leituras,
            conversoes,
        )
        total += leituras + conversoes
    return total
