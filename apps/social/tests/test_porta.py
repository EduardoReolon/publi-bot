"""A porta (fontes.py) contra o nucleo DE VERDADE, funcao por funcao.

Os outros testes do modulo simulam a porta para testar a logica das redes;
este confere que cada funcao dela le o nucleo como ele e hoje. Se o nucleo
mudar um nome de campo, e aqui que quebra — e nao no primeiro post em producao.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.social import fontes


@pytest.mark.django_db
def test_negocio_le_perfil_dores_regioes_e_idioma(ambiente):
    from apps.editorial.models import PerfilDoNegocio
    from apps.integrations.models import Site
    from apps.radar.models import ConfiguracaoDoRadar

    PerfilDoNegocio.objects.update_or_create(
        pk=1,
        defaults={"tema": "Saude do sono", "publico": "Adultos cansados", "oferta": "Consulta"},
    )
    radar = ConfiguracaoDoRadar.carregar()
    radar.dores = "acordar cansado\nronco"
    radar.regioes = [{"codigo": 1, "nome": "Curitiba,Parana,Brazil"}]
    radar.save()
    Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")

    negocio = fontes.negocio()

    assert negocio["publico"] == "Adultos cansados" and negocio["oferta"] == "Consulta"
    assert negocio["dores"] == ["acordar cansado", "ronco"]
    assert negocio["regioes"] == ["Curitiba"]
    assert negocio["idioma"]


@pytest.mark.django_db
def test_demanda_vem_dos_grupos_do_radar(ambiente):
    from apps.radar.models import GrupoDeDemanda

    GrupoDeDemanda.objects.create(
        rotulo="ronco o que fazer", volume_total=880, centroide=[0.1] * 1024
    )
    GrupoDeDemanda.objects.create(
        rotulo="descartado", volume_total=9999, centroide=[0.1] * 1024, situacao="descartado"
    )
    [grupo] = fontes.demanda()
    assert grupo["rotulo"] == "ronco o que fazer" and grupo["volume"] == 880
    assert len(grupo["vetor"]) == 1024


@pytest.mark.django_db
def test_termos_proibidos_do_guia(ambiente):
    from apps.editorial.models import EditorialProfile

    perfil = EditorialProfile.carregar()
    perfil.termos = [{"termo": "cura", "troca": "tratamento"}]
    perfil.save()
    assert fontes.termos_a_evitar("Existe cura para isso?") == ['"cura" (use "tratamento")']
    assert fontes.termos_a_evitar("Tratamento simples.") == []


@pytest.mark.django_db
def test_pergunta_e_resposta(ambiente):
    from apps.content.models import Answer, Question
    from apps.integrations.models import Site

    assert fontes.criar_pergunta("Isso vale para idosos?", "rede:x:1") is None  # sem site
    Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    pk = fontes.criar_pergunta("Isso vale para idosos?", "rede:instagram:C1")
    assert fontes.criar_pergunta("de novo", "rede:instagram:C1") == pk  # nao duplica
    pergunta = Question.objects.get(pk=pk)
    assert pergunta.question_text == "Isso vale para idosos?" and pergunta.retention_until

    assert fontes.respostas([pk]) == {}
    Answer.objects.create(
        question=pergunta,
        body_markdown="Sim, [segundo o estudo](https://x.org). Vale.",
        status="approved_scheduled",
        published_url="https://s.exemplo.org/perguntas/1/",
    )
    resposta = fontes.respostas([pk])[pk]
    assert (
        resposta["texto"].startswith("Sim, segundo o estudo") and "x.org" not in resposta["texto"]
    )
    assert resposta["url"] == "https://s.exemplo.org/perguntas/1/"


@pytest.mark.django_db
def test_conversoes_pelas_redes_e_sinais(ambiente):
    from apps.integrations.models import ConversaoDoSite, Site
    from apps.radar.models import SugestaoDeAtualizacao

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    hoje = timezone.localdate()
    for n, (entrada, final) in enumerate(
        [("social", "organic"), ("organic", "social"), ("organic", "organic")]
    ):
        ConversaoDoSite.objects.create(
            site=site,
            external_id=str(n),
            dia=hoje,
            canal_de_entrada=entrada,
            canal_final=final,
            jornada=[{"remote_id": "r1", "engaged_seconds": 30}],
        )
    assert fontes.conversoes_pelas_redes("r1", hoje - timedelta(days=1)) == 2
    assert fontes.conversoes_pelas_redes("", hoje) == 0

    artigo = fontes.ArtigoParaRedes(
        id="x",
        titulo="t",
        url="https://s.exemplo.org/a/",
        resumo="",
        palavra_chave="",
        remote_id="r1",
    )
    SugestaoDeAtualizacao.objects.create(url=artigo.url, titulo="t", tipo="quase_la")
    assert fontes.sinais(artigo) == {"quase_la": True, "conversoes": 3}


@pytest.mark.django_db
def test_endereco_publico_usa_o_dominio_do_cliente(ambiente, settings):
    tenant, _, _ = ambiente
    from django.db import connection

    connection.set_tenant(tenant)
    dominio = tenant.domains.filter(is_primary=True).first() or tenant.domains.first()
    assert fontes.endereco_publico("/redes/r/x/") == (
        f"{settings.ESQUEMA_PUBLICO}://{dominio.domain}/redes/r/x/"
    )


def test_modelo_indisponivel_sao_excecoes():
    assert all(issubclass(e, Exception) for e in fontes.modelo_indisponivel())
