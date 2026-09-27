"""Links internos: o payload leva ate 3 artigos publicados do mesmo assunto.

Bag of words como embedding: titulos com palavras em comum ficam perto.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.content.models import Article
from tests.test_radar import radar  # noqa: F401


def _publicado(titulo, remote_id):
    return Article.objects.create(
        title=titulo,
        focus_keyword=titulo,
        remote_id=remote_id,
        status=Article.Status.PUBLISHED,
        published_at=timezone.now(),
        published_url=f"https://exemplo.com.br/{remote_id}/",
    )


@pytest.mark.django_db
def test_relacionados_sao_os_vizinhos_publicados(radar):  # noqa: F811
    from apps.content.relacionados import relacionados

    _publicado("preco do cimento na obra", "a")
    _publicado("preco do bloco ceramico na obra", "b")
    _publicado("receita de bolo de cenoura", "c")
    novo = Article.objects.create(title="preco do cimento hoje", focus_keyword="preco cimento")

    lista = relacionados(novo)

    assert [item["remote_id"] for item in lista][:1] == ["a"]
    assert "c" not in {item["remote_id"] for item in lista}
    assert lista[0] == {
        "remote_id": "a",
        "title": "preco do cimento na obra",
        "url": "https://exemplo.com.br/a/",
    }


@pytest.mark.django_db
def test_payload_leva_os_relacionados_e_nunca_a_propria_pagina(radar):  # noqa: F811
    from apps.integrations.models import Site
    from apps.integrations.publishing import montar_payload_de_artigo

    site = Site.objects.create(name="S", slug="s", base_url="https://exemplo.com.br")
    v1 = _publicado("preco do cimento na obra", "a")
    _publicado("preco do cimento em sacos", "b")
    v2 = Article.objects.create(
        title=v1.title, focus_keyword=v1.title, remote_id="a", previous_version=v1
    )

    payload = montar_payload_de_artigo(v2, site)

    assert [item["remote_id"] for item in payload["related_articles"]] == ["b"]


@pytest.mark.django_db
def test_sem_vizinho_o_campo_nao_vai(radar):  # noqa: F811
    from apps.integrations.models import Site
    from apps.integrations.publishing import montar_payload_de_artigo

    site = Site.objects.create(name="S", slug="s", base_url="https://exemplo.com.br")
    payload = montar_payload_de_artigo(Article.objects.create(title="tema solto"), site)
    assert "related_articles" not in payload


@pytest.mark.django_db
def test_custo_por_clique_entra_na_nota_so_quando_existe(radar):  # noqa: F811
    from apps.radar.agrupamento import agrupar, pontuar
    from apps.radar.models import SinalDeDemanda

    sem = SinalDeDemanda.objects.create(
        texto="quanto custa reboco", fonte="relacionada", volume=500
    )
    com = SinalDeDemanda.objects.create(
        texto="orcamento de telhado",
        fonte="relacionada",
        volume=500,
        extra={"metricas": {"2076": {"volume": 500, "cpc": 3.0}}},
    )
    agrupar([sem, com])
    sem.refresh_from_db()
    com.refresh_from_db()

    pontuar(sem.grupo)
    pontuar(com.grupo)
    sem.grupo.refresh_from_db()
    com.grupo.refresh_from_db()

    assert "comercial" not in sem.grupo.parcelas
    assert com.grupo.parcelas["comercial"] == 1.0
    assert com.grupo.nota > sem.grupo.nota
