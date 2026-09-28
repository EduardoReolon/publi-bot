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


# ---------------------------------------------------------------------------
# Parceiros vizinhos e imprensa
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_vizinho_vira_parceiro_provavel_e_nucleo_nao(radar):  # noqa: F811
    from apps.radar.models import ConcorrenteSugerido
    from apps.radar.parceiros import parceiros_provaveis

    ConcorrenteSugerido.objects.create(
        dominio="blogdevarejo.com.br",
        consultas={"estoque": 3, "vitrine": 5, "rfm": 9},
        aderencias={"estoque": 0.3, "vitrine": 0.2, "rfm": 0.9},
    )
    ConcorrenteSugerido.objects.create(
        dominio="rival.com.br",
        consultas={"rfm": 1, "churn": 2, "estoque": 4},
        aderencias={"rfm": 0.9, "churn": 0.8, "estoque": 0.3},
    )
    ConcorrenteSugerido.objects.create(
        dominio="imprensa.com.br",
        consultas={"estoque": 1, "vitrine": 1},
        aderencias={"estoque": 0.3, "vitrine": 0.2},
        imprensa=True,
    )
    assert [p.dominio for p in parceiros_provaveis()] == []  # 1 do nucleo para 2 vizinhas

    blog = ConcorrenteSugerido.objects.get(dominio="blogdevarejo.com.br")
    blog.consultas["loja"] = 2
    blog.aderencias["loja"] = 0.4
    blog.save()
    [provavel] = parceiros_provaveis()
    assert provavel.dominio == "blogdevarejo.com.br"
    assert sorted(provavel.buscas_vizinhas) == ["estoque", "loja", "vitrine"]


@pytest.mark.django_db
def test_desfazer_parceiro_e_marcar_imprensa(ambiente):  # noqa: F811
    from apps.radar.models import ConcorrenteSugerido

    _, _, client = ambiente
    site = ConcorrenteSugerido.objects.create(
        dominio="x.com.br", situacao=ConcorrenteSugerido.Situacao.PARCEIRO
    )
    url = reverse("radar:decidir_concorrente", args=[site.pk], urlconf="core.urls_tenants")
    client.post(url, {"decisao": "desfazer"})
    site.refresh_from_db()
    assert site.situacao == "sugerido"
    client.post(url, {"decisao": "imprensa"})
    site.refresh_from_db()
    assert site.imprensa


@pytest.mark.django_db
def test_principais_noticias_marcam_imprensa_e_a_pauta_ganha_angulo(radar):  # noqa: F811
    from apps.content.imprensa import pedido, veiculos
    from apps.content.models import Topic
    from apps.radar.concorrentes import registrar_aparicoes
    from apps.radar.models import ConcorrenteSugerido
    from apps.radar.provedores import ItemDeBusca, ler_serp

    tarefa = {
        "result": [
            {
                "items": [
                    {
                        "type": "top_stories",
                        "items": [
                            {"domain": "www.jornal.com.br", "url": "https://jornal.com.br/n"}
                        ],
                    }
                ]
            }
        ]
    }
    noticias = ler_serp(tarefa).noticias
    registrar_aparicoes(
        "clientes inativos",
        [ItemDeBusca(url="https://jornal.com.br/materia-antiga", titulo="Clientes somem")],
        noticias=noticias,
    )
    _serp("clientes inativos", ["jornal.com.br", "blog.com.br"])
    assert ConcorrenteSugerido.objects.get(dominio="jornal.com.br").imprensa

    ConcorrenteSugerido.objects.create(
        dominio="revista.com.br",
        imprensa=True,
        consultas={"churn": 3, "retencao": 4},
        aderencias={"churn": 0.8, "retencao": 0.7},
    )
    pauta = Topic.objects.create(title="Clientes inativos", target_keyword="clientes inativos")
    achados = veiculos(pauta)
    assert [v["dominio"] for v in achados.na_busca] == ["jornal.com.br"]
    assert [v["dominio"] for v in achados.ausentes] == ["revista.com.br"]
    texto = pedido(pauta)
    assert "jornal.com.br" in texto and "ATUALIZAR a materia" in texto
