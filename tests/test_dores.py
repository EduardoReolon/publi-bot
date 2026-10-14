"""O banco de dores: as falas do publico, juntas por dor, e o uso delas."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.radar import dores
from apps.radar.models import ConfiguracaoDoRadar, DorDoPublico, FalaDoPublico
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401

U = "core.urls_tenants"


def test_detector_separa_dor_de_conversa():
    D = FalaDoPublico.Tipo
    assert dores.classificar("Eu acordo cansada todo dia, nao aguento mais isso") == D.DESABAFO
    assert dores.classificar("Atendimento pessimo, esperei duas horas") == D.RECLAMACAO
    assert dores.classificar("Como faco para parar de acordar cansado?") == D.PERGUNTA
    # Pergunta neutra e demanda do Radar, nao dor; elogio e link, nada.
    assert dores.classificar("Qual o horario de funcionamento de voces?") == ""
    assert dores.classificar("Parabens pelo video, muito bom mesmo") == ""
    assert dores.classificar("veja em https://exemplo.com o cansaco") == ""
    fraca = dores.intensidade("estou cansado")
    forte = dores.intensidade("Estou MUITO cansado todo dia, nao aguento mais!!")
    assert 0 < fraca < forte <= 1


@pytest.mark.django_db
def test_falas_viram_dores_e_o_post_prefere_a_propria_rede(radar, settings):  # noqa: F811
    settings.RADAR_DISTANCIA_DA_DOR = 0.7
    settings.RADAR_DISTANCIA_PARA_USAR_DOR = 0.9
    config = ConfiguracaoDoRadar.carregar()
    config.dores = "acordar cansado todo dia"
    config.save()

    # Antes de qualquer fala: a digitada vale (o meio-tempo).
    assert dores.dores_para("cansaco") == [
        {"dor": "acordar cansado todo dia", "falas": [], "nota": 0.5}
    ]

    F = FalaDoPublico.Fonte
    dores.registrar("Eu acordo cansado todo dia mesmo dormindo oito horas", F.YOUTUBE)
    dores.registrar("Eu acordo cansado todo dia e nao aguento mais", F.INSTAGRAM)
    assert dores.registrar("Eu acordo cansado todo dia e nao aguento mais", F.YOUTUBE) is None
    dores.registrar(
        "Fui mal atendido e ninguem explicou nada", F.AVALIACAO, tipo=FalaDoPublico.Tipo.RECLAMACAO
    )
    assert dores.agrupar() == 3

    cansaco = DorDoPublico.objects.get(digitada=True)
    assert cansaco.falas_total == 2 and cansaco.por_fonte == {"youtube": 1, "instagram": 1}
    assert cansaco.nota > 0.5  # com falas, de duas fontes, vale mais que so digitada

    achadas = dores.dores_para("acordar cansado todo dia", rede="instagram", quantas=1)
    assert achadas[0]["dor"] == "acordar cansado todo dia"
    assert achadas[0]["falas"][0] == "Eu acordo cansado todo dia e nao aguento mais"
    linha = dores.em_linhas(achadas)[0]
    assert linha.startswith("acordar cansado todo dia — “Eu acordo cansado")

    # Tirada da Configuracao, a dor com falas fica (como colhida).
    config.dores = ""
    config.save()
    dores.semear_digitadas()
    cansaco.refresh_from_db()
    assert not cansaco.digitada and cansaco.falas_total == 2


@pytest.mark.django_db
def test_tela_das_dores_anota_e_descarta(ambiente, monkeypatch):  # noqa: F811
    from apps.radar import tasks

    agrupados = []
    monkeypatch.setattr(tasks.agrupar_dores, "delay", lambda: agrupados.append(1))
    _, _, client = ambiente
    url = reverse("radar:acao_nas_dores", urlconf=U)
    client.post(url, {"acao": "anotar", "texto": "Toda consulta eu saio sem entender nada"})
    fala = FalaDoPublico.objects.get()
    assert fala.fonte == FalaDoPublico.Fonte.MANUAL
    dor = DorDoPublico.objects.create(rotulo="sair da consulta sem entender")
    fala.dor = dor
    fala.save()
    dores.recalcular(dor)

    pagina = client.get(reverse("radar:dores", urlconf=U)).content.decode()
    assert "sair da consulta sem entender" in pagina and "Toda consulta eu saio" in pagina
    client.post(url, {"acao": "descartar", "dor": str(dor.pk)})
    dor.refresh_from_db()
    assert dor.descartada and dor.descartada_em
    pagina = client.get(reverse("radar:dores", urlconf=U)).content.decode()
    assert "Descartadas" in pagina and "Reativar" in pagina


@pytest.mark.django_db
def test_artigo_recebe_as_dores_da_pauta(radar, settings):  # noqa: F811
    from apps.content.flows import dores_do_artigo
    from apps.content.models import Article

    settings.RADAR_DISTANCIA_PARA_USAR_DOR = 0.9
    artigo = Article.objects.create(title="Cansaco ao acordar", focus_keyword="acordar cansado")
    assert dores_do_artigo(artigo) == "(nenhuma anotada)"
    dor = DorDoPublico.objects.create(
        rotulo="acordar cansado", centroide=dores._vetores(["acordar cansado"])[0].tolist()
    )
    FalaDoPublico.objects.create(
        texto="Eu acordo cansado sempre", chave="x", fonte="youtube", tipo="desabafo", dor=dor
    )
    dores.recalcular(dor)
    assert dores_do_artigo(artigo) == "- acordar cansado — “Eu acordo cansado sempre”"


@pytest.mark.django_db
def test_tela_mostra_as_digitadas_sem_esperar_rodada(
    ambiente,  # noqa: F811
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from apps.radar import tasks

    agrupados = []
    monkeypatch.setattr(tasks.agrupar_dores, "delay", lambda: agrupados.append(1))
    _, _, client = ambiente
    config = ConfiguracaoDoRadar.carregar()
    config.dores = "equipe desmotivada\nclientes somem depois da primeira compra"
    config.save()
    with django_capture_on_commit_callbacks(execute=True):
        pagina = client.get(reverse("radar:dores", urlconf=U)).content.decode()
    assert "equipe desmotivada" in pagina and "clientes somem" in pagina
    assert "Nenhuma dor ainda" not in pagina
    assert agrupados  # o vetor das digitadas vem em segundo plano


@pytest.mark.django_db
def test_hipotese_evidencia_e_descarte_que_nao_volta_escondido(radar, settings):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from apps.radar.models import SementeSugerida
    from apps.radar.sugestoes import _registrar

    settings.RADAR_DISTANCIA_DA_DOR = 0.3
    S = DorDoPublico.Situacao
    config = ConfiguracaoDoRadar.carregar()
    config.dores = "equipe desmotivada no trabalho\nclientes somem depois da compra"
    config.save()
    dores.semear_digitadas()
    equipe = DorDoPublico.objects.get(rotulo__startswith="equipe")
    assert equipe.situacao == S.HIPOTESE

    # Uma descoberta parecida: a tela oferece juntar, e juntar confirma.
    descoberta = DorDoPublico.objects.create(
        rotulo="ninguem quer vir trabalhar",
        centroide=dores._vetores(["equipe desmotivada no trabalho hoje"])[0].tolist(),
    )
    for i, texto in enumerate(["A equipe toda desmotivada, sofro", "Equipe desmotivada demais"]):
        FalaDoPublico.objects.create(
            texto=texto, chave=str(i), fonte="youtube", tipo="desabafo", dor=descoberta
        )
    dores.recalcular(descoberta)
    assert descoberta.situacao == S.DESCOBERTA
    assert dores.ligar_as_parecidas() == 1
    descoberta.refresh_from_db()
    assert descoberta.parecida_com == equipe
    dores.juntar(descoberta, equipe)
    equipe.refresh_from_db()
    assert equipe.falas_total == 2 and equipe.situacao == S.CONFIRMADA

    # Hipotese que ninguem disse, com banco cheio e tempo passado: sem evidencia.
    clientes = DorDoPublico.objects.get(rotulo__startswith="clientes")
    DorDoPublico.objects.filter(pk=clientes.pk).update(
        criada_em=timezone.now() - timedelta(days=dores.DIAS_SEM_EVIDENCIA + 1)
    )
    clientes.refresh_from_db()
    assert dores.situacao(clientes, falas_no_banco=5) == S.HIPOTESE  # pouca coleta ainda
    assert dores.situacao(clientes, falas_no_banco=dores.FALAS_NO_BANCO) == S.SEM_EVIDENCIA
    assert dores.peso(clientes, falas_no_banco=dores.FALAS_NO_BANCO) < 0.5

    # Descartar tira da caixa de texto, mas guarda; digitar de novo nao ressuscita,
    # e a IA nao sugere de novo.
    dores.descartar(clientes)
    config.refresh_from_db()
    assert config.lista_de_dores == ["equipe desmotivada no trabalho"]
    config.dores += "\nclientes somem depois da compra"
    config.save()
    dores.semear_digitadas()
    clientes.refresh_from_db()
    assert clientes.descartada
    assert DorDoPublico.objects.filter(rotulo__startswith="clientes").count() == 1
    assert _registrar("Clientes somem depois da compra", SementeSugerida.Tipo.DOR, "modelo") is None
    dores.reativar(clientes)
    clientes.refresh_from_db()
    assert not clientes.descartada
