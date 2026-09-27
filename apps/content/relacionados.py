"""Artigos relacionados: os links internos que o site mostra em "Leia tambem".

Link interno e uma das poucas alavancas de SEO que dependem de conhecer o
conteudo inteiro: ele leva o leitor (e o Google) de um artigo ao vizinho do
mesmo assunto. O PubliBot conhece o assunto de cada artigo (embedding) e sabe
quais convertem — o site nao. Por isso a lista sai daqui, e o site so exibe.

Regras: ate 3, publicados, perto o bastante (distancia de cosseno ate 0,25),
e entre os perto, os que convertem acima da media sobem.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("publibot.content")

MAXIMO = 3
VIZINHANCA = 0.25
# Quanto a conversao pode adiantar um vizinho (em distancia de cosseno).
BONUS_DE_CONVERSAO = 0.05


def relacionados(article) -> list[dict]:
    """[{"remote_id", "title", "url"}] para o payload. Vazio se nao houver."""
    from apps.content.models import Article
    from apps.radar.agrupamento import _distancia, _vetor
    from apps.radar.atualizacoes import _texto_do_artigo, vigia_de

    consulta = (
        Article.objects.filter(status=Article.Status.PUBLISHED)
        .exclude(published_url="")
        .exclude(remote_id="")
        .exclude(pk=article.pk)
    )
    if article.remote_id:
        # A propria pagina, em qualquer versao.
        consulta = consulta.exclude(remote_id=article.remote_id)
    candidatos = list(consulta)
    if not candidatos:
        return []
    vetor = _vetor(_texto_do_artigo(article))
    notas = _nota_de_conversao()

    perto = []
    for candidato in candidatos:
        vigia = vigia_de(candidato)
        if vigia.vetor is None:
            continue
        distancia = _distancia(vetor, np.asarray(vigia.vetor, dtype=np.float32))
        if distancia > VIZINHANCA:
            continue
        ajuste = BONUS_DE_CONVERSAO * notas.get(candidato.remote_id, 0.0)
        perto.append((distancia - ajuste, candidato))
    perto.sort(key=lambda par: par[0])
    return [
        {"remote_id": c.remote_id, "title": c.title, "url": c.published_url}
        for _nota, c in perto[:MAXIMO]
    ]


def _nota_de_conversao() -> dict[str, float]:
    """remote_id -> 0 a 1 (1 = o dobro da taxa media do site ou mais)."""
    from apps.content.desempenho import MINIMO_DE_LEITURAS, painel

    linhas = [linha for linha in painel(90).linhas if linha.engaged_views >= MINIMO_DE_LEITURAS]
    leituras = sum(linha.engaged_views for linha in linhas)
    atribuidas = sum(linha.atribuidas for linha in linhas)
    if not leituras or not atribuidas:
        return {}
    media = atribuidas / leituras
    return {
        linha.remote_id: min(1.0, (linha.taxa_de_conversao or 0) / media / 2) for linha in linhas
    }
