"""Fonte vencida ou substituida pede atualizacao do artigo que a cita.

* a fonte nova, ao concluir a curadoria, aposenta a anterior (sai da busca);
* o artigo publicado que citava a anterior vira sugestao, com a substituta;
* atualizar pelo PubliBot troca as citacoes da versao para a fonte nova;
* decidida, a mesma combinacao de fontes nao volta.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Article, ArticleCitation
from apps.radar.models import SugestaoDeAtualizacao
from tests.test_fontes import _categoria, _documento_curado
from tests.test_interface import ambiente  # noqa: F401


def _artigo_citando(documento) -> Article:
    artigo = Article.objects.create(
        title="Preco do cimento em Curitiba",
        body_markdown="Segundo [[FONTE_1]], o saco custa R$ 38.",
        status=Article.Status.PUBLISHED,
        published_at=timezone.now(),
        published_url="https://exemplo.com.br/preco-cimento/",
        remote_id="r1",
        primary_source=documento,
    )
    trecho = documento.chunks.first()
    ArticleCitation.objects.create(
        article=artigo,
        super_chunk=trecho,
        rank=1,
        distance=0.1,
        used_as_primary=True,
        source_title=trecho.source_title,
        source_label=trecho.source_label,
        citation_mode=trecho.citation_mode,
    )
    return artigo


@pytest.mark.django_db
def test_fonte_nova_aposenta_a_anterior_e_avisa_o_artigo(ambiente, embedding_falso):  # noqa: F811
    categoria = _categoria("empresa")
    agosto = _documento_curado(
        categoria, "Cimento CP II a R$ 38 o saco.", source_label="Precos ago/2026", title="Ago"
    )
    artigo = _artigo_citando(agosto)
    assert not agosto.vencida

    setembro = _documento_curado(
        categoria,
        "Cimento CP II a R$ 41 o saco.",
        source_label="Precos set/2026",
        title="Set",
        replaces=agosto,
    )

    agosto.refresh_from_db()
    assert agosto.vencida
    sugestao = SugestaoDeAtualizacao.objects.get(tipo="fonte")
    assert sugestao.artigo == artigo
    assert sugestao.evidencia["fontes"][0]["substituta"] == "Precos set/2026"
    assert sugestao.prioridade == 70

    # Rodar de novo nao duplica.
    from apps.radar.atualizacoes import pelas_fontes

    assert pelas_fontes() == 0
    assert SugestaoDeAtualizacao.objects.filter(tipo="fonte").count() == 1

    # Atualizar no PubliBot: a versao nova cita a fonte de setembro.
    _, _, client = ambiente
    resposta = client.post(
        reverse("radar:decidir_atualizacao", args=[sugestao.pk], urlconf="core.urls_tenants"),
        {"decisao": "versao"},
    )
    assert resposta.status_code == 302
    v2 = Article.objects.get(previous_version=artigo)
    citacao = v2.citations.get()
    assert citacao.super_chunk.document == setembro
    assert citacao.source_label == "Precos set/2026"
    assert v2.primary_source == setembro
    assert "Precos set/2026" in v2.update_notes
    # A v1 continua citando agosto: e o historico do que foi ao ar.
    assert artigo.citations.get().super_chunk.document == agosto

    # Decidida, a mesma combinacao nao volta.
    assert pelas_fontes() == 0


@pytest.mark.django_db
def test_fonte_so_vencida_sugere_sem_substituta(ambiente, embedding_falso):  # noqa: F811
    from datetime import timedelta

    from apps.radar.atualizacoes import pelas_fontes

    documento = _documento_curado(_categoria("empresa"), "Tabela.", source_label="Tabela jul")
    documento.valid_until = timezone.localdate() - timedelta(days=3)
    documento.save()
    _artigo_citando(documento)

    assert pelas_fontes() == 1
    sugestao = SugestaoDeAtualizacao.objects.get()
    assert sugestao.prioridade == 50
    assert sugestao.evidencia["fontes"][0]["substituta"] is None

    _, _, client = ambiente
    pagina = client.get(reverse("radar:atualizacoes", urlconf="core.urls_tenants")).content
    assert "sem versao nova no acervo" in pagina.decode()


@pytest.mark.django_db
def test_curadoria_oferece_a_fonte_anterior(ambiente, embedding_falso):  # noqa: F811
    from apps.knowledge.forms import CuradoriaDeDocumento

    categoria = _categoria("empresa")
    antiga = _documento_curado(categoria, "Texto antigo.", source_label="Antiga")
    nova = _documento_curado(categoria, "Texto novo.", source_label="Nova")

    opcoes = list(CuradoriaDeDocumento(instance=nova).fields["replaces"].queryset)
    assert antiga in opcoes and nova not in opcoes
