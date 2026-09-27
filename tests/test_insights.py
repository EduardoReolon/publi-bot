"""Leitura e conversoes vindas do site (recurso `insights`).

* a coleta grava por cima a leitura do dia e nao duplica conversao;
* leitura com menos de 10 s nao entra na jornada; artigo repetido conta uma vez;
* atribuicao: ultimo artigo, participacao e divisao igual;
* versao nova herda o id remoto, e o numero vai para a versao no ar;
* no radar, tema vizinho de artigo que converte acima da media sobe.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Article
from apps.integrations.insights import coletar, gravar
from apps.integrations.models import ConversaoDoSite, LeituraDoDia
from tests.test_integrations import site, tenant_integracoes  # noqa: F401
from tests.test_interface import ambiente  # noqa: F401

HOJE = timezone.localdate()


def _leitura(remote_id, **campos):
    return {"remote_id": remote_id, "date": HOJE.isoformat(), **campos}


def _conversao(ident, jornada, **campos):
    return {
        "id": ident,
        "date": HOJE.isoformat(),
        "kind": "whatsapp",
        "journey": [{"remote_id": r, "engaged_seconds": s} for r, s in jornada],
        **campos,
    }


@pytest.mark.django_db
def test_gravar_substitui_a_leitura_e_nao_duplica_conversao(site):  # noqa: F811
    gravar(site, {"reading": [_leitura("a", views=10, engaged_views=4)]})
    gravar(
        site,
        {
            "reading": [_leitura("a", views=12, engaged_views=5), {"remote_id": "", "date": "x"}],
            "conversions": [_conversao("c1", [("a", 30)], via_cta=True), {"id": "sem-data"}],
        },
    )
    gravar(site, {"conversions": [_conversao("c1", [("a", 30)])]})

    linha = LeituraDoDia.objects.get()
    assert (linha.views, linha.engaged_views) == (12, 5)
    conversao = ConversaoDoSite.objects.get()
    assert conversao.via_cta and conversao.jornada == [{"remote_id": "a", "engaged_seconds": 30}]


@pytest.mark.django_db
def test_coleta_pagina_e_refaz_os_ultimos_dias(site, monkeypatch):  # noqa: F811
    from apps.integrations.client import SiteClient

    pedidos = []
    paginas = {
        "": {"reading": [_leitura("a", views=1)], "next_cursor": "p2"},
        "p2": {"reading": [_leitura("b", views=2)], "next_cursor": None},
    }

    def insights(self, *, desde, cursor=""):
        pedidos.append((desde, cursor))
        return paginas[cursor]

    monkeypatch.setattr(SiteClient, "insights", insights)
    assert coletar(site) == (2, 0)
    assert pedidos[0] == (HOJE - timedelta(days=90), "")
    assert pedidos[1][1] == "p2"

    pedidos.clear()
    coletar(site)
    assert pedidos[0][0] == HOJE - timedelta(days=3)


@pytest.mark.django_db
def test_atribuicao_ignora_leitura_rapida_e_divide_entre_os_lidos(site):  # noqa: F811
    from apps.content.desempenho import painel

    antiga = Article.objects.create(title="v1", remote_id="a", status=Article.Status.SUPERSEDED)
    no_ar = Article.objects.create(
        title="v2", remote_id="a", status=Article.Status.PUBLISHED, previous_version=antiga
    )
    gravar(
        site,
        {
            "reading": [_leitura("a", views=100, engaged_views=60, engaged_seconds=6000)],
            "conversions": [
                _conversao("c1", [("a", 30), ("b", 5), ("a", 20), ("c", 40)]),
                _conversao("c2", [("b", 3)]),
                _conversao("c3", [("c", 15)], via_cta=True),
            ],
        },
    )

    resultado = painel(28)

    assert resultado.conversoes == 3 and resultado.sem_artigo == 1
    assert resultado.pela_chamada == 1
    por_id = {d.remote_id: d for d in resultado.linhas}
    assert "b" not in por_id
    assert (por_id["a"].ultimo, por_id["a"].participou, por_id["a"].atribuidas) == (0, 1, 0.5)
    assert (por_id["c"].ultimo, por_id["c"].participou, por_id["c"].atribuidas) == (2, 2, 1.5)
    assert resultado.linhas[0].remote_id == "c"
    assert por_id["a"].artigo == no_ar
    assert por_id["a"].tempo_medio == 100


@pytest.mark.django_db
def test_tela_de_desempenho(ambiente):  # noqa: F811
    from apps.integrations.models import Site

    _, _, client = ambiente
    site_ = Site.objects.first()
    if site_ is not None:
        gravar(site_, {"reading": [_leitura("x", views=3, engaged_views=2)]})
    pagina = client.get(reverse("content:desempenho", urlconf="core.urls_tenants") + "?dias=90")
    assert pagina.status_code == 200
    assert "Desempenho no site" in pagina.content.decode()


def test_conversao_da_vizinhanca():
    from apps.radar.agrupamento import _conversao_da_vizinhanca

    perto = np.asarray([1.0, 0.0], dtype=np.float32)
    longe = np.asarray([0.0, 1.0], dtype=np.float32)
    centroide = np.asarray([1.0, 0.05], dtype=np.float32)

    assert _conversao_da_vizinhanca(centroide, None) is None
    # So um vizinho longe: sem dado, nem ganha nem perde.
    assert _conversao_da_vizinhanca(centroide, ([(longe, 0.10)], 0.02)) is None
    # Vizinho com o dobro da media: nota cheia; na media: meio.
    assert _conversao_da_vizinhanca(centroide, ([(perto, 0.04)], 0.02)) == 1.0
    assert _conversao_da_vizinhanca(centroide, ([(perto, 0.02)], 0.02)) == pytest.approx(0.5)
    assert _conversao_da_vizinhanca(centroide, ([(perto, 0.0)], 0.02)) == 0.0
