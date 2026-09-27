"""Sugestoes de atualizacao de artigos publicados.

* tema com demanda barrado pela canibalizacao aponta o artigo que deveria
  responder aquelas perguntas;
* "quase la" e perda de posicao saem do Search Console, com a posicao media
  ponderada pelas impressoes;
* feita ou dispensada, a mesma sugestao nao volta por 60 dias;
* publicacao feita por fora do PubliBot tambem recebe sugestao.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.radar.models import (
    ColetaDoConsole,
    GrupoDeDemanda,
    LinhaDoConsole,
    SinalDeDemanda,
    SugestaoDeAtualizacao,
)
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import EmbeddingPorPalavras, radar  # noqa: F401

URL = "https://site.exemplo.com.br/como-calcular-bdi"


def _artigo(titulo="Como calcular o BDI de obra", url=URL):
    from apps.content.models import Article

    return Article.objects.create(
        title=titulo, published_url=url, published_at=timezone.now(), status="published"
    )


def _coleta(dias_atras, linhas):
    coleta = ColetaDoConsole.objects.create(
        propriedade="sc-domain:exemplo.com.br",
        inicio=timezone.localdate(),
        fim=timezone.localdate(),
        coletada_em=timezone.now() - timezone.timedelta(days=dias_atras),
    )
    for consulta, posicao, impressoes in linhas:
        LinhaDoConsole.objects.create(
            coleta=coleta, consulta=consulta, pagina=URL, posicao=posicao, impressoes=impressoes
        )
    return coleta


@pytest.mark.django_db
def test_demanda_barrada_pela_canibalizacao_aponta_o_artigo(radar):  # noqa: F811
    from apps.radar.atualizacoes import atualizar_sugestoes

    artigo = _artigo()
    _artigo(titulo="Planilha de orcamento", url="https://site.exemplo.com.br/planilha")
    grupo = GrupoDeDemanda.objects.create(
        rotulo="calcular bdi obra publica",
        centroide=EmbeddingPorPalavras().embed_query("calcular BDI obra"),
        nota=60,
        volume_total=900,
        parcelas={"canibalizacao": 0.95, "demanda": 0.7},
    )
    SinalDeDemanda.objects.create(
        texto="Qual BDI usar em obra publica?", fonte="paa", grupo=grupo, volume=900
    )

    assert atualizar_sugestoes() == 1
    sugestao = SugestaoDeAtualizacao.objects.get()
    assert sugestao.tipo == "acrescentar"
    assert sugestao.artigo == artigo
    assert sugestao.evidencia["sinais"][0]["texto"] == "Qual BDI usar em obra publica?"
    # Rodar de novo atualiza a evidencia, sem duplicar.
    assert atualizar_sugestoes() == 0
    assert SugestaoDeAtualizacao.objects.count() == 1


@pytest.mark.django_db
def test_quase_la_e_perda_de_posicao_pelo_search_console(radar):  # noqa: F811
    from apps.radar.atualizacoes import atualizar_sugestoes

    _artigo()
    # Retrato anterior: posicao media ponderada 5. Atual: ~11,6 — caiu.
    _coleta(30, [("bdi", 5.0, 900), ("bdi tcu", 5.0, 100)])
    _coleta(0, [("bdi", 12.0, 900), ("bdi tcu", 8.0, 100), ("bdi planilha", 15.0, 3)])

    assert atualizar_sugestoes() == 2
    quase_la = SugestaoDeAtualizacao.objects.get(tipo="quase_la")
    assert [c["consulta"] for c in quase_la.evidencia["consultas"]] == ["bdi", "bdi tcu"]
    perdeu = SugestaoDeAtualizacao.objects.get(tipo="perdeu")
    assert perdeu.evidencia["posicao_anterior"] == 5.0
    assert perdeu.evidencia["posicao"] == pytest.approx(11.6, abs=0.1)


@pytest.mark.django_db
def test_feita_nao_volta_por_sessenta_dias(radar):  # noqa: F811
    from apps.radar.atualizacoes import atualizar_sugestoes

    _artigo()
    _coleta(0, [("bdi", 12.0, 900)])
    atualizar_sugestoes()
    sugestao = SugestaoDeAtualizacao.objects.get()
    sugestao.situacao = SugestaoDeAtualizacao.Situacao.FEITA
    sugestao.decidida_em = timezone.now()
    sugestao.save()

    assert atualizar_sugestoes() == 0
    SugestaoDeAtualizacao.objects.update(decidida_em=timezone.now() - timezone.timedelta(days=61))
    assert atualizar_sugestoes() == 1


@pytest.mark.django_db
def test_publicacao_de_fora_do_publibot_tambem_entra(radar):  # noqa: F811
    from apps.integrations.models import Site, SitePost
    from apps.radar.atualizacoes import atualizar_sugestoes

    site = Site.objects.create(name="S", slug="s", base_url="https://site.exemplo.com.br")
    SitePost.objects.create(site=site, remote_id="9", title="BDI na pratica", url=URL + "/")
    _coleta(0, [("bdi", 11.0, 400)])

    assert atualizar_sugestoes() == 1
    sugestao = SugestaoDeAtualizacao.objects.get()
    assert sugestao.artigo is None
    assert sugestao.titulo == "BDI na pratica"


@pytest.mark.django_db
def test_tela_menu_e_painel(ambiente):  # noqa: F811
    _, _, client = ambiente
    sugestao = SugestaoDeAtualizacao.objects.create(
        url=URL,
        titulo="Como calcular o BDI",
        tipo="quase_la",
        evidencia={
            "consultas": [{"consulta": "bdi", "posicao": 12, "impressoes": 900}],
            "impressoes": 900,
        },
    )
    url = reverse("radar:atualizacoes", urlconf="core.urls_tenants")
    pagina = client.get(url).content.decode()
    assert "Como calcular o BDI" in pagina
    assert 'class="subabas"' in pagina
    painel = client.get(reverse("accounts:painel", urlconf="core.urls_tenants")).content.decode()
    assert "Artigos para atualizar" in painel

    client.post(
        reverse("radar:decidir_atualizacao", args=[sugestao.pk], urlconf="core.urls_tenants"),
        {"decisao": "feita"},
    )
    sugestao.refresh_from_db()
    assert sugestao.situacao == "feita"
    assert sugestao.decidida_em is not None
