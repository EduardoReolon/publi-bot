"""Artigos publicados que vale atualizar.

Tres motivos, cada um com evidencia na tela:

* **acrescentar**: demanda nova que o artigo deveria responder. Duas vias:
  o artigo com idade minima vigia os temas novos (com volume, tao perto dele
  quanto o tema que o originou — a distancia de referencia); e um tema que a
  canibalizacao barrou de virar pauta aponta a pagina que ja o cobre;
* **quase la**: a pagina aparece entre a 8a e a 20a posicao para buscas com
  impressoes (Search Console). E o empurrao mais barato que existe;
* **perdeu posicao**: a posicao media piorou 3 ou mais de um retrato do
  Search Console para o outro;
* **fonte vencida**: o artigo cita uma fonte que venceu (validade da
  categoria) ou que ganhou versao nova no acervo. Para pagina de preco e o
  motivo principal: o endereco fica, o dado muda.

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
    # So a versao que esta no ar: as substituidas tem o mesmo endereco.
    for artigo in Article.objects.filter(status=Article.Status.PUBLISHED).exclude(published_url=""):
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


# A referencia nunca fica mais exigente que isto (um artigo que encaixou
# perfeito no tema de origem nao pode exigir perfeicao dos temas novos) nem mais
# frouxa que a canibalizacao (ai ja seria outro assunto).
LIMIAR_MINIMO = 0.10
LIMIAR_MAXIMO = 0.16
VOLUME_PARA_AMPLIAR = 30


def _texto_do_artigo(artigo) -> str:
    partes = [artigo.title, artigo.focus_keyword, *(artigo.secondary_keywords or [])]
    partes += list(artigo.sections.values_list("heading", flat=True)[:20])
    return ". ".join(p for p in partes if p)


def _primeira_publicacao(artigo):
    """A data em que a PAGINA foi ao ar: a da versao 1, nao a da ultima."""
    atual = artigo
    while atual.previous_version_id:
        atual = atual.previous_version
    return atual.published_at


def vigia_de(artigo):
    """A vigia do artigo, criada na primeira vez com a distancia de referencia.

    Referencia: a distancia entre o artigo e o tema que o originou (o grupo do
    radar que virou a pauta dele). Versao nova herda a referencia da anterior:
    o tema de origem e o mesmo.
    """
    from apps.radar.agrupamento import _distancia, _vetor
    from apps.radar.models import VigiaDeArtigo

    vigia = VigiaDeArtigo.objects.filter(artigo=artigo).first()
    if vigia is not None:
        return vigia
    vetor = _vetor(_texto_do_artigo(artigo))
    grupo, distancia = None, None
    anterior = (
        getattr(artigo.previous_version, "vigia", None) if artigo.previous_version_id else None
    )
    if anterior is not None:
        grupo, distancia = anterior.grupo_de_referencia, anterior.distancia_de_referencia
    elif artigo.topic_id:
        grupo = (
            GrupoDeDemanda.objects.filter(pauta_id=artigo.topic_id)
            .exclude(centroide__isnull=True)
            .first()
        )
        if grupo is not None:
            distancia = _distancia(vetor, np.asarray(grupo.centroide, dtype=np.float32))
    return VigiaDeArtigo.objects.create(
        artigo=artigo,
        vetor=vetor.tolist(),
        grupo_de_referencia=grupo,
        distancia_de_referencia=distancia,
    )


def pela_proximidade(paginas: list[dict]) -> set[str]:
    """Artigos com idade minima: temas novos, com volume, perto como a referencia.

    Devolve as URLs que ganharam sugestao, para a canibalizacao nao repetir.
    """
    from apps.radar.agrupamento import _distancia
    from apps.radar.models import ConfiguracaoDoRadar, SinalDeDemanda

    idade = timezone.timedelta(days=ConfiguracaoDoRadar.carregar().idade_para_vigiar)
    limite_de_data = timezone.now() - idade
    grupos = [
        (g, np.asarray(g.centroide, dtype=np.float32))
        for g in GrupoDeDemanda.objects.filter(
            situacao=GrupoDeDemanda.Situacao.NOVO, volume_total__gte=VOLUME_PARA_AMPLIAR
        ).exclude(centroide__isnull=True)
    ]
    atendidas: set[str] = set()
    if not grupos:
        return atendidas
    for pagina in paginas:
        artigo = pagina["artigo"]
        if artigo is None:
            continue
        publicado = _primeira_publicacao(artigo)
        if publicado is None or publicado > limite_de_data:
            continue
        vigia = vigia_de(artigo)
        vetor = np.asarray(vigia.vetor, dtype=np.float32)
        referencia = vigia.distancia_de_referencia
        limiar = min(LIMIAR_MAXIMO, max(LIMIAR_MINIMO, referencia if referencia is not None else 0))
        perto = sorted(
            (
                (d, g)
                for g, c in grupos
                if g.pk != vigia.grupo_de_referencia_id and (d := _distancia(vetor, c)) <= limiar
            ),
            key=lambda x: (-(x[1].volume_total or 0), x[0]),
        )[:3]
        if not perto:
            continue
        temas, sinais = [], []
        for distancia, grupo in perto:
            temas.append(
                {
                    "tema": grupo.rotulo,
                    "volume": grupo.volume_total,
                    "distancia": round(distancia, 3),
                }
            )
            sinais += list(
                grupo.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO)
                .order_by("-volume")
                .values("texto", "volume", "fonte")[:5]
            )
        evidencia = {
            "tema": temas[0]["tema"],
            "volume": sum(t["volume"] or 0 for t in temas),
            "temas": temas,
            "sinais": sinais[:10],
            "referencia": round(referencia, 3) if referencia is not None else None,
        }
        _sugerir(
            pagina,
            SugestaoDeAtualizacao.Tipo.ACRESCENTAR,
            evidencia,
            max(g.nota for _d, g in perto),
        )
        atendidas.add(_chave(pagina["url"]))
    return atendidas


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


def pelas_fontes() -> int:
    """Artigos no ar que citam fonte vencida ou substituida.

    Nao depende do radar nem do Search Console: roda na curadoria da fonte nova
    e uma vez por dia. A mesma combinacao de fontes nao volta depois de
    decidida; outra fonte vencendo no mesmo artigo volta.
    """
    from apps.content.models import Article, ArticleCitation

    hoje = timezone.localdate()
    citacoes = (
        ArticleCitation.objects.filter(
            article__status=Article.Status.PUBLISHED,
            super_chunk__document__valid_until__lt=hoje,
        )
        .exclude(article__published_url="")
        .select_related("article", "super_chunk__document")
    )
    por_artigo: dict = {}
    for citacao in citacoes:
        por_artigo.setdefault(citacao.article, {})[citacao.super_chunk.document.pk] = (
            citacao.super_chunk.document
        )

    novas = 0
    for artigo, documentos in por_artigo.items():
        fontes = []
        for doc in sorted(documentos.values(), key=lambda d: str(d.pk)):
            substituta = (
                doc.replaced_by.filter(status=doc.Status.CURATED).order_by("-created_at").first()
            )
            fontes.append(
                {
                    "id": str(doc.pk),
                    "titulo": doc.rotulo,
                    "valida_ate": doc.valid_until.isoformat(),
                    "substituta_id": str(substituta.pk) if substituta else None,
                    "substituta": substituta.rotulo if substituta else None,
                }
            )
        ids = [f["id"] for f in fontes]
        tipo = SugestaoDeAtualizacao.Tipo.FONTE_VENCIDA
        ja_decidida = any(
            sorted(s.evidencia.get("documentos") or []) == ids
            for s in SugestaoDeAtualizacao.objects.filter(
                url=artigo.published_url,
                tipo=tipo,
                situacao__in=[
                    SugestaoDeAtualizacao.Situacao.FEITA,
                    SugestaoDeAtualizacao.Situacao.DISPENSADA,
                ],
            )
        )
        if ja_decidida:
            continue
        evidencia = {"fontes": fontes, "documentos": ids}
        # Com a fonte nova ja no acervo, a atualizacao esta pronta para ser
        # feita: vem antes das que ainda esperam o dado novo.
        prioridade = 70.0 if any(f["substituta"] for f in fontes) else 50.0
        aberta = SugestaoDeAtualizacao.objects.filter(
            url=artigo.published_url, tipo=tipo, situacao=SugestaoDeAtualizacao.Situacao.ABERTA
        ).first()
        if aberta:
            aberta.evidencia, aberta.prioridade, aberta.artigo = evidencia, prioridade, artigo
            aberta.save(update_fields=["evidencia", "prioridade", "artigo", "atualizada_em"])
            continue
        SugestaoDeAtualizacao.objects.create(
            url=artigo.published_url,
            titulo=artigo.title[:300],
            artigo=artigo,
            tipo=tipo,
            evidencia=evidencia,
            prioridade=prioridade,
        )
        novas += 1
    return novas


def atualizar_sugestoes() -> int:
    antes = SugestaoDeAtualizacao.objects.count()
    paginas = paginas_publicadas()
    atendidas = pela_proximidade(paginas)
    pela_canibalizacao([p for p in paginas if _chave(p["url"]) not in atendidas])
    pelo_search_console(paginas)
    pelas_fontes()
    return SugestaoDeAtualizacao.objects.count() - antes
