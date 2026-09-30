"""Painel de imprensa (um e-mail por veiculo) e links quebrados do assunto."""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.radar import links_quebrados
from apps.radar.models import ConcorrenteSugerido, LinkQuebrado, PaginaVerificada, ResultadoOrganico
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401

PARAGRAFO = "Texto do guia de varejo com conteudo de verdade para o extrator. " * 25

HTML = f"""<html><head><title>Guia de varejo</title></head><body>
<nav><a href="https://menu.com.br/secao">Politica de Privacidade</a></nav>
<article><h1>Guia de varejo</h1>
<p>{PARAGRAFO} Veja a
<a href="https://morto.com.br/analise-rfm-clientes">analise rfm de clientes</a>
e <a href="/interno">interno</a> e <a href="https://vivo.com.br/x">qualquer coisa</a>.</p>
<p>{PARAGRAFO} Tambem a
<a href="https://morto.com.br/receita-de-bolo">receita de bolo de cenoura</a>,
o <a href="https://play.google.com/store/apps/details?id=x">app</a>, o
<a href="https://blog.com.brpolitica/">link torto do proprio site</a> e o
<a href="mailto:a@b.c">email</a>.</p>
</article>
<footer><a href="https://rodape.com.br/y">
<svg><style>.cls-1 {{ fill: #252959; }}</style></svg></a></footer>
</body></html>"""


def _artigo(titulo, palavra, **extra):
    from apps.content.models import Article

    return Article.objects.create(
        title=titulo,
        slug=uuid.uuid4().hex[:8],
        focus_keyword=palavra,
        status=Article.Status.PUBLISHED,
        published_at=timezone.now() - datetime.timedelta(days=30),
        published_url=f"https://meusite.com.br/blog/{uuid.uuid4().hex[:6]}/",
        **extra,
    )


def test_links_de_saida_so_do_texto_principal_e_para_outros_sites():
    """Menu, rodape e SVG escondido ficam de fora; loja de aplicativo e o
    proprio site com a barra faltando tambem."""
    titulo, links = links_quebrados.links_de_saida(HTML, "https://blog.com.br/guia")
    assert titulo == "Guia de varejo"
    assert [u for u, *_ in links] == [
        "https://morto.com.br/analise-rfm-clientes",
        "https://vivo.com.br/x",
        "https://morto.com.br/receita-de-bolo",
    ]


def test_pagina_em_latin1_nao_vira_interrogacao():
    texto = "concorrência".encode("latin-1")
    assert links_quebrados.decodificar(texto, "text/html; charset=ISO-8859-1") == "concorrência"
    assert links_quebrados.decodificar(texto, "text/html") == "concorrência"
    assert links_quebrados.decodificar("ação".encode(), "text/html") == "ação"


@pytest.mark.django_db
def test_so_guarda_o_quebrado_perto_de_um_artigo(radar, monkeypatch):  # noqa: F811
    artigo = _artigo("Analise RFM de clientes", "analise rfm clientes")
    monkeypatch.setattr("apps.knowledge.web.baixar", lambda url: (HTML.encode(), url, "text/html"))
    monkeypatch.setattr(
        links_quebrados, "situacao_do_link", lambda url: 404 if "morto" in url else None
    )
    monkeypatch.setattr("apps.radar.concorrentes.aderencia_da_consulta", lambda texto: 0.0)
    ResultadoOrganico.objects.create(consulta="rfm", url="https://blog.com.br/guia", posicao=3)

    assert links_quebrados.paginas_para_verificar() == ["https://blog.com.br/guia"]
    assert links_quebrados.verificar_um_lote() == 1
    [link] = LinkQuebrado.objects.all()
    assert link.artigo == artigo and link.texto == "analise rfm de clientes"
    assert "nao existe mais" in links_quebrados.email(link)
    assert artigo.published_url in links_quebrados.email(link)
    # A pagina nao volta tao cedo.
    assert PaginaVerificada.objects.get().links == 3
    assert links_quebrados.paginas_para_verificar() == []


@pytest.mark.django_db
def test_painel_ordena_o_que_interessa_ao_veiculo(radar):  # noqa: F811
    from apps.content.imprensa import painel, pedido_de_email
    from apps.content.models import Topic

    pauta_a = Topic.objects.create(
        title="Clientes inativos",
        target_keyword="clientes inativos",
        evidence={"volume_total": 3000},
    )
    pauta_b = Topic.objects.create(
        title="Ticket medio", target_keyword="ticket medio", evidence={"volume_total": 200}
    )
    pauta_c = Topic.objects.create(
        title="Churn no varejo", target_keyword="churn no varejo", evidence={"volume_total": 5000}
    )
    _artigo("Clientes inativos", "clientes inativos", topic=pauta_a)
    _artigo("Ticket medio", "ticket medio", topic=pauta_b)
    _artigo("Churn no varejo", "churn no varejo", topic=pauta_c)
    ConcorrenteSugerido.objects.create(
        dominio="jornal.com.br",
        imprensa=True,
        consultas={"x": 1, "y": 2},
        aderencias={"clientes inativos": 0.5, "ticket medio": 0.4},
    )
    # A materia do jornal esta em 9o em "clientes inativos" e em 1o em "ticket medio".
    ResultadoOrganico.objects.create(
        consulta="clientes inativos", url="https://jornal.com.br/a", titulo="A", posicao=9
    )
    ResultadoOrganico.objects.create(
        consulta="ticket medio", url="https://jornal.com.br/b", titulo="B", posicao=1
    )

    [veiculo] = painel()
    assert [i.artigo.title for i in veiculo.atualizar] == ["Clientes inativos", "Ticket medio"]
    assert [i.artigo.title for i in veiculo.lacunas] == ["Churn no varejo"]
    texto = pedido_de_email(veiculo)
    assert "LISTA 1" in texto and "9a posicao" in texto and "5000 buscas/mes" in texto
    assert "PubliBot" in texto


@pytest.mark.django_db
def test_tela_de_imprensa_e_links(ambiente, settings):  # noqa: F811
    settings.PUBLIBOT_DOMINIO_PUBLICO = "publibot.ekron.ia.br"
    _, _, client = ambiente
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/g",
        dominio="blog.com.br",
        link_url="https://morto.com.br/x",
        texto="precificacao de servicos",
        status_http=404,
    )
    url = reverse("radar:imprensa", urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "Links quebrados em sites do assunto" in html and "precificacao de servicos" in html

    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "pauta"},
    )
    from apps.content.models import Topic

    assert Topic.objects.get().title == "precificacao de servicos"
    link.refresh_from_db()
    assert link.situacao == "descartado"


@pytest.mark.django_db
def test_landing_com_whatsapp(client, settings, public_tenant):
    settings.CONTATO_COMERCIAL_WHATSAPP = "41920021699"
    html = client.get("/", HTTP_HOST=settings.ROOT_DOMAIN).content.decode()
    assert "https://wa.me/5541920021699" in html and "(41) 92002-1699" in html
    assert "Escrito com fonte" in html


def test_so_404_410_e_dominio_morto_contam_como_quebrado(monkeypatch):
    from apps.radar import links_quebrados

    for codigo, esperado in [
        (200, None),
        (403, None),
        (500, None),
        (None, None),
        (404, 404),
        (410, 410),
        (0, 0),
    ]:
        monkeypatch.setattr(links_quebrados, "codigo_http", lambda url, c=codigo: c)
        assert links_quebrados.situacao_do_link("https://x.com/") == esperado


def test_agente_leva_o_dominio_publico_como_contato(settings):
    from apps.knowledge import web

    settings.PUBLIBOT_DOMINIO_PUBLICO = "publibot.exemplo.com"
    assert "+https://publibot.exemplo.com/" in web._agente()


def test_guarda_o_paragrafo_em_que_o_link_esta():
    _, links = links_quebrados.links_de_saida(HTML, "https://blog.com.br/guia")
    _, texto, contexto = links[0]
    assert texto == "analise rfm de clientes"
    # Paragrafo longo: a janela em volta do link, e nao so o comeco.
    assert "analise rfm de clientes" in contexto and contexto.startswith("…")
    assert len(contexto) <= 2 * links_quebrados.JANELA_DO_TRECHO + 2


@pytest.mark.django_db
def test_pagina_que_sumiu_vem_do_internet_archive_e_vai_para_a_pauta(ambiente, monkeypatch):  # noqa: F811
    from apps.content.models import Topic

    _, _, client = ambiente
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        pagina_titulo="Guia de varejo",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="plataforma EAD",
        contexto="Para treinar a equipe, use uma plataforma EAD com trilhas.",
        status_http=404,
    )
    antiga = (
        "<html><head><title>O que e uma plataforma LMS</title></head><body><article><p>"
        + "Uma plataforma LMS organiza cursos e trilhas de treinamento. " * 20
        + "</p></article></body></html>"
    )
    monkeypatch.setattr(links_quebrados, "ultima_copia_boa", lambda url: ("20190312000000", url))
    monkeypatch.setattr(
        "apps.knowledge.web.baixar", lambda url: (antiga.encode(), url, "text/html")
    )

    links_quebrados.consultar_arquivo(link)
    link.refresh_from_db()
    assert link.arquivo_titulo == "O que e uma plataforma LMS"
    assert link.arquivo_url == "https://web.archive.org/web/20190312000000/https://morto.com.br/lms"
    assert link.arquivo_data.year == 2019 and link.arquivo_trecho.startswith("Uma plataforma LMS")

    pagina = client.get(reverse("radar:imprensa", urlconf="core.urls_tenants")).content.decode()
    assert (
        "A pagina que sumiu, pelo Internet Archive" in pagina
        and "Ver a copia de 12/03/2019" in pagina
    )

    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "pauta"},
    )
    pauta = Topic.objects.get()
    assert pauta.title == "O que e uma plataforma LMS"
    assert "ONDE O LINK ESTA" in pauta.briefing and "use uma plataforma EAD" in pauta.briefing
    assert "A PAGINA QUE SUMIU" in pauta.briefing and "web.archive.org" in pauta.briefing
