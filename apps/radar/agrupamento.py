"""Agrupar sinais parecidos e dar nota a cada grupo.

**Agrupamento.** Mil comentarios e perguntas viram quinze temas. Cada sinal
novo e comparado com o centroide dos grupos existentes; perto o bastante, entra
no grupo, senao abre um novo. Incremental de proposito: reagrupar tudo a cada
rodada mudaria grupos que ja viraram pauta. Nenhum LLM aqui — so embedding, que
roda na CPU e custa nada.

**Nota.** De 0 a 100, com as parcelas gravadas para a tela explicar:

* demanda — volume de busca somado (escala logaritmica); sem volume, o tamanho
  do grupo;
* diversidade — quantas fontes diferentes apontam o tema (Google, visitantes,
  YouTube...): o mesmo tema vindo de lugares diferentes e sinal mais forte;
* aderencia — quao perto o tema esta do negocio (nicho do site e sementes);
* canibalizacao — quao perto esta do que ja foi escrito (quanto MAIS perto,
  PIOR);
* cobertura — se o acervo ja tem fonte para ele. Tema sem fonte nao e
  descartado: vira pauta que espera fonte;
* comercial — so quando ha custo por clique (DataForSEO): quanto anunciantes
  pagam por clique naquele tema. Tema caro no anuncio e tema que, ganho no
  organico, substitui anuncio;
* conversao — so quando o site envia leitura e conversoes (recurso
  `insights`): tema vizinho de artigo que converte acima da media do site
  sobe; vizinho de artigo que ninguem converte, desce. Sem esse dado, a nota
  e a de sempre.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from django.conf import settings

from apps.radar.models import GrupoDeDemanda, SinalDeDemanda

logger = logging.getLogger("publibot.radar")

PESOS = {
    "demanda": 0.35,
    "diversidade": 0.15,
    "aderencia": 0.20,
    "canibalizacao": 0.15,
    "cobertura": 0.15,
}


# Quanto a conversao pesa quando existe; e ate onde um artigo e "vizinho".
PESO_DA_CONVERSAO = 0.15
# O valor comercial (custo por clique no Google Ads), quando a DataForSEO o
# trouxe: tema pelo qual anunciantes pagam caro e tema que, ganho no organico,
# economiza anuncio. Maximo a partir de CPC_ALTO dolares.
PESO_COMERCIAL = 0.10
CPC_ALTO = 3.0
VIZINHANCA = 0.20


def _vetor(texto: str) -> np.ndarray:
    from apps.knowledge.embeddings import get_embedding_client

    return np.asarray(get_embedding_client().embed_query(texto), dtype=np.float32)


def _distancia(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if not na or not nb:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


def agrupar(sinais: list[SinalDeDemanda]) -> set:
    """Coloca cada sinal num grupo. Devolve os ids dos grupos tocados."""
    limiar = float(getattr(settings, "RADAR_DISTANCIA_DO_GRUPO", 0.10))
    grupos = [
        (g, np.asarray(g.centroide, dtype=np.float32))
        for g in GrupoDeDemanda.objects.exclude(
            situacao=GrupoDeDemanda.Situacao.DESCARTADO
        ).exclude(centroide__isnull=True)
    ]
    tocados = set()

    # Os de maior volume primeiro: eles fundam os grupos e dao o rotulo.
    for sinal in sorted(sinais, key=lambda s: -(s.volume or 0)):
        vetor = _vetor(sinal.texto)
        melhor, distancia = None, 2.0
        for grupo, centroide in grupos:
            d = _distancia(vetor, centroide)
            if d < distancia:
                melhor, distancia = (grupo, centroide), d

        if melhor is not None and distancia <= limiar:
            grupo, centroide = melhor
            n = grupo.sinais.count()
            novo = (centroide * n + vetor) / (n + 1)
            grupo.centroide = novo.tolist()
            grupo.save(update_fields=["centroide", "atualizado_em"])
            grupos = [(g, novo if g.pk == grupo.pk else c) for g, c in grupos]
        else:
            grupo = GrupoDeDemanda.objects.create(
                rotulo=sinal.texto[:500], centroide=vetor.tolist()
            )
            grupos.append((grupo, vetor))

        sinal.grupo = grupo
        sinal.save(update_fields=["grupo"])
        tocados.add(grupo.pk)

    return tocados


def _escala(valor: float, perto: float, longe: float) -> float:
    """1 em `perto` ou menos, 0 em `longe` ou mais, linear entre os dois."""
    if valor <= perto:
        return 1.0
    if valor >= longe:
        return 0.0
    return (longe - valor) / (longe - perto)


def _texto_do_negocio() -> str:
    """A referencia do "perto do tema do site": tema, oferta, frentes e sementes."""
    from apps.editorial.models import perfil_do_negocio
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = perfil_do_negocio()
    partes = [perfil.tema, perfil.oferta, *perfil.frentes.splitlines()] if perfil else []
    partes += ConfiguracaoDoRadar.carregar().lista_de_sementes
    return ". ".join(p.strip() for p in partes if p and p.strip())


def vetores_do_que_ja_foi_escrito() -> np.ndarray | None:
    """Os titulos de pautas e artigos existentes, vetorizados uma vez por rodada."""
    from apps.content.models import Article, Topic

    titulos = list(
        Topic.objects.exclude(status=Topic.Status.REJECTED)
        .order_by("-created_at")
        .values_list("title", flat=True)[:200]
    ) + list(Article.objects.order_by("-created_at").values_list("title", flat=True)[:200])
    # O que o site publicou por fora do PubliBot tambem canibaliza.
    from apps.integrations.models import SitePost

    titulos += list(SitePost.objects.order_by("-synced_at").values_list("title", flat=True)[:300])
    titulos = list(dict.fromkeys(t for t in titulos if t))
    if not titulos:
        return None
    return np.vstack([_vetor(t) for t in titulos])


def _menor_distancia(vetor: np.ndarray, matriz: np.ndarray | None) -> float:
    if matriz is None or not len(matriz):
        return 2.0
    normas = np.linalg.norm(matriz, axis=1) * (np.linalg.norm(vetor) or 1.0)
    normas[normas == 0] = 1.0
    return float(np.min(1.0 - (matriz @ vetor) / normas))


def _menor_distancia_ao_acervo(vetor: np.ndarray) -> float:
    from pgvector.django import CosineDistance

    from apps.knowledge.models import SuperChunk

    maisperto = (
        SuperChunk.objects.filter(is_active=True, embedding__isnull=False)
        .annotate(d=CosineDistance("embedding", vetor.tolist()))
        .order_by("d")
        .values_list("d", flat=True)
        .first()
    )
    return float(maisperto) if maisperto is not None else 2.0


def pontuar(
    grupo: GrupoDeDemanda,
    *,
    vetor_do_negocio: np.ndarray | None = None,
    ja_escrito: np.ndarray | None = None,
    que_convertem=None,
) -> float:
    from apps.knowledge.models import RetrievalSettings

    sinais = list(grupo.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO))
    if not sinais:
        grupo.nota = 0
        grupo.save(update_fields=["nota", "atualizado_em"])
        return 0.0

    com_volume = [s for s in sinais if s.volume is not None]
    volume = sum(s.volume for s in com_volume)
    # Rotulo: o texto de maior volume; sem volume, o mais curto (costuma ser o
    # mais proximo de como a pessoa busca).
    # A avaliacao de cliente nao da titulo a um tema, se houver outra fonte.
    rotulaveis = [s for s in sinais if s.fonte != SinalDeDemanda.Fonte.AVALIACAO] or sinais
    principal = (
        max(com_volume, key=lambda s: s.volume)
        if com_volume
        else min(rotulaveis, key=lambda s: len(s.texto))
    )

    if com_volume:
        demanda = min(1.0, math.log10(1 + volume) / 4)  # 10 mil buscas/mes = 1
    else:
        demanda = min(1.0, len(sinais) / 5) * 0.6

    diversidade = min(1.0, len({s.fonte for s in sinais}) / 3)

    centroide = np.asarray(grupo.centroide, dtype=np.float32)
    if vetor_do_negocio is not None:
        aderencia = _escala(_distancia(centroide, vetor_do_negocio), 0.12, 0.30)
    else:
        aderencia = 0.5

    # O proprio titulo do grupo, se ja virou pauta, nao conta contra ele.
    if grupo.pauta_id:
        canibalizacao = 0.0
    else:
        canibalizacao = _escala(_menor_distancia(centroide, ja_escrito), 0.06, 0.16)
    limiar = RetrievalSettings.carregar().max_cosine_distance
    cobertura = _escala(_menor_distancia_ao_acervo(centroide), limiar, limiar + 0.10)

    parcelas = {
        "demanda": round(demanda, 3),
        "diversidade": round(diversidade, 3),
        "aderencia": round(aderencia, 3),
        "canibalizacao": round(canibalizacao, 3),
        "cobertura": round(cobertura, 3),
    }
    nota = 100 * (
        PESOS["demanda"] * demanda
        + PESOS["diversidade"] * diversidade
        + PESOS["aderencia"] * aderencia
        + PESOS["canibalizacao"] * (1 - canibalizacao)
        + PESOS["cobertura"] * cobertura
    )

    cpcs = [
        float(m["cpc"])
        for s in sinais
        for m in ((s.extra or {}).get("metricas") or {}).values()
        if m.get("cpc") is not None
    ]
    if cpcs:
        comercial = min(1.0, max(cpcs) / CPC_ALTO)
        parcelas["comercial"] = round(comercial, 3)
        nota = (1 - PESO_COMERCIAL) * nota + 100 * PESO_COMERCIAL * comercial

    conversao = _conversao_da_vizinhanca(centroide, que_convertem)
    if conversao is not None:
        parcelas["conversao"] = round(conversao, 3)
        nota = (1 - PESO_DA_CONVERSAO) * nota + 100 * PESO_DA_CONVERSAO * conversao

    grupo.rotulo = principal.texto[:500]
    grupo.volume_total = volume
    grupo.nota = round(nota, 1)
    grupo.parcelas = parcelas
    grupo.save(update_fields=["rotulo", "volume_total", "nota", "parcelas", "atualizado_em"])
    return grupo.nota


def _conversao_da_vizinhanca(centroide: np.ndarray, que_convertem) -> float | None:
    """De 0 a 1: 0,5 e a media do site, 1 e o dobro dela ou mais.

    Media ponderada pela proximidade das taxas dos artigos vizinhos (ver
    `apps/content/desempenho.py::que_convertem`). Sem vizinho com dado, None:
    o tema nao ganha nem perde.
    """
    if not que_convertem:
        return None
    vizinhos, media = que_convertem
    perto = [
        (d, taxa)
        for vetor, taxa in vizinhos
        if taxa is not None and (d := _distancia(centroide, vetor)) <= VIZINHANCA
    ]
    if not perto or not media:
        return None
    pesos = [1 / (d + 0.02) for d, _taxa in perto]
    taxa = sum(p * t for p, (_d, t) in zip(pesos, perto, strict=True)) / sum(pesos)
    return min(1.0, taxa / media / 2)


def vetor_do_negocio() -> np.ndarray | None:
    texto = _texto_do_negocio()
    return _vetor(texto) if texto else None
