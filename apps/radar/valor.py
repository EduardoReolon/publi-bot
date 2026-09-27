"""Quanto valem os cliques organicos, no preco do Google Ads.

"Valor do trafego" e a medida de mercado (Ahrefs, Semrush) para comparar SEO
com anuncio: para cada busca que trouxe cliques a uma pagina, os cliques vezes
o custo por clique que um anunciante pagaria por aquela busca. E o que o site
gastaria em anuncio para receber os mesmos visitantes.

* Os cliques vem do Search Console (consulta x pagina).
* O custo por clique vem da DataForSEO, na mesma chamada do volume, e fica
  guardado por palavra (`CustoDaPalavra`): o radar ja paga por ele.
* As consultas do Search Console que ainda nao tem preco sao consultadas numa
  chamada so (ate 300 palavras, cerca de US$ 0,09), quando a DataForSEO esta
  configurada. O preco vale 90 dias.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.radar.models import CustoDaPalavra

logger = logging.getLogger("publibot.radar")

MAXIMO_DE_CONSULTAS = 300
VALIDADE = timedelta(days=90)


def guardar_custos(metricas: dict[str, dict], *, local: int | None) -> int:
    """Upsert do custo e do volume de cada palavra da resposta."""
    agora = timezone.now()
    total = 0
    for palavra, metrica in (metricas or {}).items():
        if metrica.get("cpc") is None and metrica.get("volume") is None:
            continue
        CustoDaPalavra.objects.update_or_create(
            palavra=palavra[:200],
            local=int(local or 0),
            defaults={
                "cpc": metrica.get("cpc"),
                "volume": metrica.get("volume"),
                "competicao": metrica.get("competicao"),
                "atualizado_em": agora,
            },
        )
        total += 1
    return total


def custos(palavras, *, local: int | None = None) -> dict[str, float]:
    """palavra normalizada -> custo por clique (US$). Prefere a regiao pedida."""
    from apps.radar.provedores import palavra_para_volume

    normalizadas = {p: palavra_para_volume(p) for p in palavras}
    alvo = {n for n in normalizadas.values() if n}
    saida: dict[str, float] = {}
    for custo in CustoDaPalavra.objects.filter(palavra__in=alvo, cpc__isnull=False).order_by(
        "atualizado_em"
    ):
        # A regiao principal vence as outras; entre as outras, a mais recente.
        if custo.palavra in saida and local and custo.local != local:
            continue
        saida[custo.palavra] = custo.cpc
    return saida


def precificar_consultas(coleta) -> int:
    """Custo por clique das consultas com clique, que ainda nao tem preco."""
    from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas
    from apps.radar.provedores import metricas_dataforseo, palavra_para_volume

    contas = ContasExternas.carregar()
    if not contas.tem_dataforseo:
        return 0
    config = ConfiguracaoDoRadar.carregar()
    recentes = set(
        CustoDaPalavra.objects.filter(
            local=config.local_principal, atualizado_em__gte=timezone.now() - VALIDADE
        ).values_list("palavra", flat=True)
    )
    faltam = []
    for consulta in (
        coleta.linhas_set.filter(cliques__gt=0)
        .order_by("-cliques")
        .values_list("consulta", flat=True)
    ):
        palavra = palavra_para_volume(consulta)
        if palavra and palavra not in recentes and palavra not in faltam:
            faltam.append(palavra)
        if len(faltam) >= MAXIMO_DE_CONSULTAS:
            break
    if not faltam:
        return 0
    try:
        metricas = metricas_dataforseo(
            faltam,
            config=config,
            contas=contas,
            finalidade=ChamadaExterna.Finalidade.VALOR,
        )
    except Exception:
        # Teto do mes, conta bloqueada, rede: o retrato do Search Console vale
        # sem o preco, e a proxima coleta tenta de novo.
        logger.exception("Falha ao buscar o custo por clique das consultas.")
        return 0
    return len(metricas)


def valor_do_trafego(coleta=None) -> dict[str, dict]:
    """Por pagina: cliques, cliques com preco e o valor em US$ (ultimo retrato)."""
    from apps.radar.atualizacoes import _chave
    from apps.radar.models import ColetaDoConsole, ConfiguracaoDoRadar
    from apps.radar.provedores import palavra_para_volume

    coleta = coleta or ColetaDoConsole.objects.order_by("-coletada_em").first()
    if coleta is None:
        return {}
    linhas = list(coleta.linhas_set.filter(cliques__gt=0).values("consulta", "pagina", "cliques"))
    precos = custos(
        [linha["consulta"] for linha in linhas],
        local=ConfiguracaoDoRadar.carregar().local_principal,
    )
    por_pagina: dict[str, dict] = {}
    for linha in linhas:
        pagina = por_pagina.setdefault(
            _chave(linha["pagina"]), {"cliques": 0, "cliques_com_preco": 0, "valor_usd": 0.0}
        )
        pagina["cliques"] += linha["cliques"]
        cpc = precos.get(palavra_para_volume(linha["consulta"]) or "")
        if cpc is not None:
            pagina["cliques_com_preco"] += linha["cliques"]
            pagina["valor_usd"] += linha["cliques"] * cpc
    return por_pagina
