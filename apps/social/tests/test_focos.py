"""Foco de cada post (alcance, engajamento, site): escolhido pelo que falta na
mistura da fase, escrito com a instrucao do foco, medido contra os do mesmo
foco — e o que foi bem no foco vira recomendacao de impulso."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.social import estrategia, focos, fontes
from apps.social.models import Destino, Post


def _publicado(destino, foco, metricas, **campos):
    return Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao="publicado",
        publicado_em=campos.pop("publicado_em", timezone.now() - timedelta(days=20)),
        metricas=metricas,
        extras={"foco": foco},
        **campos,
    )


@pytest.mark.django_db
def test_foco_da_vez_segue_a_mistura_da_fase(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")  # comeco
    assert focos.foco_da_vez(destino) == "alcance"
    for _ in range(2):
        Post.objects.create(destino=destino, motivo="novo", extras={"foco": "alcance"})
    # 2 de alcance: o que mais falta agora e engajamento (35%).
    assert focos.foco_da_vez(destino) == "engajamento"

    post = Post.objects.create(destino=destino, motivo="novo", extras={"foco": "site"})
    assert "FOCO DO POST: SITE" in focos.instrucao(post)
    assert focos.instrucao(Post(destino=destino, extras={"foco": "xyz"})) == ""


@pytest.mark.django_db
def test_desempenho_no_foco_e_recomendacao_de_aproveitar(ambiente, monkeypatch):
    monkeypatch.setattr(fontes, "negocio", lambda: {"regioes": ["Curitiba"]})
    destino = Destino.objects.create(rede="instagram", nome="Insta", seguidores=500)
    for alcance in (100, 200, 300, 400):
        _publicado(destino, "alcance", {"alcance": alcance, "salvos": 20})
    # Engajamento e medido a parte: nao entra na comparacao do alcance.
    _publicado(destino, "engajamento", {"alcance": 5000, "comentarios": 1})
    # O post abaixo vai mal na taxa geral (nao e "amplificar"), mas bem no seu foco.

    bom = _publicado(
        destino,
        "alcance",
        {"alcance": 900},
        artigo_titulo="Sono e cansaco",
        publicado_em=timezone.now() - timedelta(hours=60),
    )
    d = focos.desempenho(bom)
    assert d == {"foco": "alcance", "posicao": 1.0, "comparados": 4}
    assert bom.no_foco["posicao"] == 1.0

    recs = [r for r in estrategia.impulsos(destino) if r.tipo == "aproveitar"]
    assert [r.posts for r in recs] == [[bom]]
    assert "25% melhores" in recs[0].por_que and "Alcance" in recs[0].objetivo

    # Poucos para comparar: sem posicao (nao inventa).
    so = Destino.objects.create(rede="linkedin", nome="Lk")
    sozinho = _publicado(so, "site", {"alcance": 100}, cliques=5)
    assert focos.desempenho(sozinho)["posicao"] is None
