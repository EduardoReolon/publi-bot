"""Vigia dos artigos publicados: demanda nova perto do que o artigo cobre.

* a referencia e a distancia entre o artigo e o tema que o originou;
* tema novo, com volume, tao perto quanto a referencia vira sugestao de
  ampliar; longe, ou sem volume, nao;
* artigo novo demais nao e vigiado (o Google ainda esta avaliando);
* versao nova herda a referencia e a idade da pagina.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.content.models import Article, Topic
from apps.radar.models import (
    ConfiguracaoDoRadar,
    GrupoDeDemanda,
    SinalDeDemanda,
    SugestaoDeAtualizacao,
    VigiaDeArtigo,
)
from tests.test_radar import EmbeddingPorPalavras, radar  # noqa: F401

EMB = EmbeddingPorPalavras()


def _grupo(rotulo, *, volume=500, pauta=None):
    grupo = GrupoDeDemanda.objects.create(
        rotulo=rotulo,
        centroide=EMB.embed_query(rotulo),
        volume_total=volume,
        nota=60,
        pauta=pauta,
        situacao="pauta" if pauta else "novo",
    )
    SinalDeDemanda.objects.create(texto=rotulo, fonte="paa", grupo=grupo, volume=volume)
    return grupo


def _artigo(titulo, *, dias=90, topic=None):
    return Article.objects.create(
        title=titulo,
        topic=topic,
        status=Article.Status.PUBLISHED,
        published_url=f"https://s.com/{abs(hash(titulo))}",
        published_at=timezone.now() - timezone.timedelta(days=dias),
        remote_id="1",
    )


@pytest.mark.django_db
def test_tema_novo_tao_perto_quanto_a_referencia_vira_ampliacao(radar):  # noqa: F811
    from apps.radar.atualizacoes import atualizar_sugestoes

    pauta = Topic.objects.create(title="calcular ltv cliente")
    origem = _grupo("calcular ltv cliente", pauta=pauta)
    # O embedding de teste conta palavras: as distancias sao maiores que as
    # do modelo real, e o titulo precisa dividir mais palavras com o tema.
    artigo = _artigo("calcular ltv cliente saas varejo", topic=pauta)
    _grupo("calcular ltv cliente varejo", volume=300)  # perto
    _grupo("reduzir churn assinatura", volume=900)  # longe
    _grupo("calcular ltv cliente mensal", volume=0)  # perto, sem volume

    atualizar_sugestoes()

    vigia = VigiaDeArtigo.objects.get(artigo=artigo)
    assert vigia.grupo_de_referencia == origem
    assert vigia.distancia_de_referencia is not None
    sugestao = SugestaoDeAtualizacao.objects.get(tipo="acrescentar")
    assert sugestao.artigo == artigo
    assert [t["tema"] for t in sugestao.evidencia["temas"]] == ["calcular ltv cliente varejo"]


@pytest.mark.django_db
def test_artigo_novo_demais_nao_e_vigiado(radar):  # noqa: F811
    from apps.radar.atualizacoes import atualizar_sugestoes

    config = ConfiguracaoDoRadar.carregar()
    config.idade_para_vigiar = 45
    config.save()
    _artigo("calcular ltv cliente", dias=10)
    _grupo("calcular ltv cliente varejo")

    atualizar_sugestoes()

    assert not SugestaoDeAtualizacao.objects.exists()


@pytest.mark.django_db
def test_versao_nova_herda_referencia_e_idade(radar):  # noqa: F811
    from apps.content.versoes import criar_nova_versao
    from apps.radar.atualizacoes import _primeira_publicacao, vigia_de

    pauta = Topic.objects.create(title="calcular ltv cliente")
    _grupo("calcular ltv cliente", pauta=pauta)
    v1 = _artigo("calcular ltv cliente saas", topic=pauta, dias=200)
    ref = vigia_de(v1)
    v2 = criar_nova_versao(v1)
    v2.status = Article.Status.PUBLISHED
    v2.published_at = timezone.now()
    v2.save()

    assert vigia_de(v2).distancia_de_referencia == ref.distancia_de_referencia
    assert _primeira_publicacao(v2) == v1.published_at
