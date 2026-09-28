"""Precisa de backlinks? Dificuldade das buscas e diagnostico pelo Search Console.

* dificuldade: sites que dominam varias buscas pesam; forum e video no topo
  sao brecha; sem buscas suficientes, nao ha medida;
* diagnostico: artigo maduro parado entre a 6a e a 30a posicao e falta de
  autoridade; quantos links depende da dificuldade das buscas dele;
* a pauta para imprensa pede o veredito antes de escrever.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.radar.autoridade import classificar, diagnosticar
from apps.radar.dificuldade import do_grupo, mapa
from apps.radar.models import ColetaDoConsole, LinhaDoConsole, ResultadoOrganico
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401


def _serp(consulta, dominios):
    for posicao, dominio in enumerate(dominios, start=1):
        caminho = "r/crm/comments/1/" if dominio == "reddit.com" else f"p{posicao}"
        ResultadoOrganico.objects.create(
            consulta=consulta, url=f"https://{dominio}/{caminho}", posicao=posicao
        )


def _buscas():
    for i in range(5):
        _serp(f"tema {i}", ["portal.com", "gigante.com", f"pequeno{i}.com.br"])
    _serp("como fazer rfm no excel", ["reddit.com", "youtube.com", "blogzinho.com.br"])


@pytest.mark.django_db
def test_dominantes_pesam_e_forum_no_topo_e_brecha(radar):  # noqa: F811
    assert mapa() == {}  # sem buscas suficientes, nao mede
    _buscas()
    dificuldades = mapa()
    dificil = dificuldades["tema 0"]
    assert dificil.rotulo == "dificil" and dificil.dominantes == ["portal.com", "gigante.com"]
    assert "dezenas" in dificil.links
    brecha = dificuldades["como fazer rfm no excel"]
    assert brecha.rotulo == "brecha" and brecha.nota == 0
    assert brecha.brechas == ["reddit.com", "youtube.com"]


@pytest.mark.django_db
def test_tema_pega_a_dificuldade_do_rotulo(radar):  # noqa: F811
    from apps.radar.models import GrupoDeDemanda

    _buscas()
    grupo = GrupoDeDemanda.objects.create(rotulo="Como fazer RFM no Excel")
    assert do_grupo(grupo, mapa()).rotulo == "brecha"


def _artigo(titulo, *, dias, posicao, impressoes, coleta):
    from apps.content.models import Article

    url = f"https://meusite.com.br/blog/{uuid.uuid4().hex[:6]}/"
    artigo = Article.objects.create(
        title=titulo,
        slug=url[-7:-1],
        status=Article.Status.PUBLISHED,
        published_at=timezone.now() - datetime.timedelta(days=dias),
        published_url=url,
        focus_keyword=titulo.lower(),
    )
    LinhaDoConsole.objects.create(
        coleta=coleta, consulta=titulo.lower(), pagina=url, impressoes=impressoes, posicao=posicao
    )
    return artigo


def _coleta():
    hoje = timezone.localdate()
    return ColetaDoConsole.objects.create(propriedade="sc-domain:x", inicio=hoje, fim=hoje)


@pytest.mark.django_db
def test_sem_console_nao_ha_diagnostico(radar):  # noqa: F811
    assert diagnosticar().sem_console


@pytest.mark.django_db
def test_parados_entre_6_e_30_pedem_poucos_links_em_tema_medio(radar):  # noqa: F811
    coleta = _coleta()
    for i in range(3):
        _artigo(f"Tema {i}", dias=90, posicao=12, impressoes=400, coleta=coleta)
    _artigo("Recente", dias=10, posicao=40, impressoes=5, coleta=coleta)

    diagnostico = diagnosticar()
    assert diagnostico.veredito == "links ajudariam"
    assert diagnostico.links.startswith("poucos")  # sem dificuldade medida: nao presume
    assert diagnostico.contagem["cedo"] == 1

    _buscas()  # agora "tema 0..2" sao buscas dominadas
    assert diagnosticar().links.startswith("muitos")


@pytest.mark.django_db
def test_topo_nao_pede_links_e_invisivel_nao_e_caso_de_link(radar):  # noqa: F811
    coleta = _coleta()
    for i in range(3):
        _artigo(f"Bom {i}", dias=90, posicao=3, impressoes=900, coleta=coleta)
    assert diagnosticar().veredito == "links nao sao prioridade"

    ColetaDoConsole.objects.all().delete()
    coleta = _coleta()
    for i in range(3):
        _artigo(f"Sumido {i}", dias=90, posicao=55, impressoes=4, coleta=coleta)
    assert diagnosticar().veredito == "o gargalo nao e link"


def test_classificacao():
    class A:
        published_at = timezone.now() - datetime.timedelta(days=90)

    def linha(posicao, impressoes):
        return {"artigo": A, "posicao": posicao, "impressoes": impressoes}

    assert classificar(linha(3, 500)) == "funcionando"
    assert classificar(linha(15, 500)) == "falta_autoridade"
    assert classificar(linha(45, 500)) == "longe"
    assert classificar(linha(15, 5)) == "sem_impressoes"


@pytest.mark.django_db
def test_telas_mostram_o_diagnostico_e_o_pedido_de_imprensa(ambiente, embedding_falso):  # noqa: F811
    from apps.content.models import Topic

    _, _, client = ambiente
    radar_html = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    assert "Precisa de backlinks?" in radar_html and "Dificuldade" in radar_html

    pauta = Topic.objects.create(title="Varejo perde 40% dos clientes", status="approved")
    url = reverse("content:imprensa_da_pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    pagina = client.get(url).content.decode()
    assert "VEREDITO: SIM ou NAO" in pagina and "sponsored" in pagina
