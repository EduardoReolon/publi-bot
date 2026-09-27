"""Artigos publicados que vale atualizar.

Tres motivos, cada um com evidencia na tela:

* **acrescentar**: um tema com demanda que a canibalizacao barrou de virar
  pauta — perto demais de um artigo que ja existe. Em vez de descartar, o
  radar aponta QUAL artigo deveria responder aquelas perguntas;
* **quase la**: a pagina aparece entre a 8a e a 20a posicao para buscas com
  impressoes (Search Console). E o empurrao mais barato que existe;
* **perdeu posicao**: a posicao media piorou 3 ou mais de um retrato do
  Search Console para o outro.

A atualizacao em si e feita pela pessoa, no artigo; o contrato com o site
ainda nao tem republicacao. Marcada como feita, a mesma sugestao nao volta por
60 dias — o tempo de o Google reavaliar a pagina.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import numpy as np
from django.db.models import Sum
from django.utils import timezone

from apps.radar.models import ColetaDoConsole, GrupoDeDemanda, SugestaoDeAtualizacao

logger = logging.getLogger("publibot.radar")

CANIBALIZACAO_MINIMA = 0.8
DEMANDA_MINIMA = 0.3
PIORA_MINIMA = 3.0
ESPERA_DEPOIS_DE_FEITA = timezone.timedelta(days=60)


def _chave(url: str) -> str:
    partes = urlparse(url or "")
    return f"{(partes.hostname or '').lower().removeprefix('www.')}{partes.path.rstrip('/')}"


def paginas_publicadas() -> list[dict]:
    """Artigos do PubliBot e publicacoes do site, sem repetir a mesma URL."""
    from apps.content.models import Article
    from apps.integrations.models import SitePost

    vistas: dict[str, dict] = {}
    for artigo in Article.objects.exclude(published_url=""):
        vistas[_chave(artigo.published_url)] = {
            "url": artigo.published_url,
            "titulo": artigo.title,
            "artigo": artigo,
        }
    for post in SitePost.objects.exclude(url=""):
        vistas.setdefault(_chave(post.url), {"url": post.url, "titulo": post.title, "artigo": None})
    return list(vistas.values())


def _sugerir(pagina: dict, tipo: str, evidencia: dict, prioridade: float) -> bool:
    """Cria ou atualiza a sugestao aberta. Devolve True se criou."""
    recente = SugestaoDeAtualizacao.objects.filter(
        url=pagina["url"],
        tipo=tipo,
        situacao__in=[
            SugestaoDeAtualizacao.Situacao.FEITA,
            SugestaoDeAtualizacao.Situacao.DISPENSADA,
        ],
        decidida_em__gte=timezone.now() - ESPERA_DEPOIS_DE_FEITA,
    ).exists()
    if recente:
        return False
    aberta = SugestaoDeAtualizacao.objects.filter(
        url=pagina["url"], tipo=tipo, situacao=SugestaoDeAtualizacao.Situacao.ABERTA
    ).first()
    if aberta:
        aberta.evidencia = evidencia
        aberta.prioridade = prioridade
        aberta.titulo = pagina["titulo"][:300]
        aberta.save(update_fields=["evidencia", "prioridade", "titulo", "atualizada_em"])
        return False
    SugestaoDeAtualizacao.objects.create(
        url=pagina["url"],
        titulo=pagina["titulo"][:300],
        artigo=pagina["artigo"],
        tipo=tipo,
        evidencia=evidencia,
        prioridade=prioridade,
    )
    return True


def pela_canibalizacao(paginas: list[dict]) -> int:
    """Tema com demanda, barrado por estar perto de uma pagina: acrescente la."""
    from apps.radar.agrupamento import _distancia, _vetor
    from apps.radar.models import SinalDeDemanda

    if not paginas:
        return 0
    grupos = [
        g
        for g in GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.NOVO).exclude(
            centroide__isnull=True
        )
        if g.parcelas.get("canibalizacao", 0) >= CANIBALIZACAO_MINIMA
        and g.parcelas.get("demanda", 0) >= DEMANDA_MINIMA
    ]
    if not grupos:
        return 0
    vetores = [_vetor(p["titulo"]) for p in paginas]
    novas = 0
    for grupo in grupos:
        centroide = np.asarray(grupo.centroide, dtype=np.float32)
        distancias = [_distancia(centroide, v) for v in vetores]
        pagina = paginas[int(np.argmin(distancias))]
        sinais = list(
            grupo.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO)
            .order_by("-volume")
            .values("texto", "volume", "fonte")[:10]
        )
        evidencia = {"tema": grupo.rotulo, "volume": grupo.volume_total, "sinais": sinais}
        if _sugerir(pagina, SugestaoDeAtualizacao.Tipo.ACRESCENTAR, evidencia, grupo.nota):
            novas += 1
    return novas


def pelo_search_console(paginas: list[dict]) -> int:
    """Quase la e perda de posicao, pelos dois ultimos retratos."""
    from apps.radar.search_console import quase_la

    coletas = list(ColetaDoConsole.objects.order_by("-coletada_em")[:2])
    if not coletas:
        return 0
    por_chave = {_chave(p["url"]): p for p in paginas}
    novas = 0

    # Quase la: as consultas de cada pagina entre a 8a e a 20a posicao.
    consultas_por_pagina: dict[str, list] = {}
    for linha in quase_la(coletas[0])[:300]:
        consultas_por_pagina.setdefault(_chave(linha.pagina), []).append(
            {
                "consulta": linha.consulta,
                "posicao": round(linha.posicao, 1),
                "impressoes": linha.impressoes,
            }
        )
    for chave, consultas in consultas_por_pagina.items():
        pagina = por_chave.get(chave)
        if pagina is None:
            continue
        impressoes = sum(c["impressoes"] for c in consultas)
        if _sugerir(
            pagina,
            SugestaoDeAtualizacao.Tipo.QUASE_LA,
            {"consultas": consultas[:10], "impressoes": impressoes},
            min(100.0, impressoes / 10),
        ):
            novas += 1

    # Perdeu posicao: media ponderada por impressoes, retrato contra retrato.
    if len(coletas) == 2:
        for pagina in paginas:
            atual = _posicao(coletas[0], pagina["url"])
            anterior = _posicao(coletas[1], pagina["url"])
            if atual is None or anterior is None or atual - anterior < PIORA_MINIMA:
                continue
            if _sugerir(
                pagina,
                SugestaoDeAtualizacao.Tipo.PERDEU_POSICAO,
                {"posicao": round(atual, 1), "posicao_anterior": round(anterior, 1)},
                min(100.0, 10 * (atual - anterior)),
            ):
                novas += 1
    return novas


def _posicao(coleta: ColetaDoConsole, url: str) -> float | None:
    linhas = coleta.linhas_set.filter(pagina=url)
    agregado = linhas.aggregate(impressoes=Sum("impressoes"))
    if not agregado["impressoes"]:
        return None
    # Ponderada: a posicao de uma busca com 900 impressoes pesa mais que a de
    # uma com 5.
    soma = sum(linha.posicao * linha.impressoes for linha in linhas)
    return soma / agregado["impressoes"]


def atualizar_sugestoes() -> int:
    paginas = paginas_publicadas()
    return pela_canibalizacao(paginas) + pelo_search_console(paginas)
