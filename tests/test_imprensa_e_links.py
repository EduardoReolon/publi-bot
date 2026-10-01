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
    assert "não existe mais" in links_quebrados.email(link)
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

    pauta = Topic.objects.get()
    assert pauta.title == "precificacao de servicos" and pauta.origin == "link"
    link.refresh_from_db()
    assert link.situacao == "pauta" and link.pauta == pauta
    # Virou pauta: o botao some e a tela mostra a situacao da pauta.
    html = client.get(url).content.decode()
    assert "Virar pauta" not in html and pauta.get_status_display() in html
    # Clicar de novo nao cria outra pauta.
    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "pauta"},
    )
    assert Topic.objects.count() == 1


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


def test_contato_so_de_mailto_e_whatsapp_do_proprio_site():
    html = """<html><body>
    <a href="mailto:contato@blog.com.br?subject=oi">fale</a>
    <a href="mailto:joao@gmail.com">outro</a>
    <a href="https://wa.me/5541999998888">zap</a>
    <p>escreva para naoconta@blog.com.br</p>
    </body></html>"""
    email, whatsapp = links_quebrados.contato_no_html(html, "blog.com.br")
    assert email == "contato@blog.com.br"
    assert whatsapp == "5541999998888"


def test_email_curto_com_a_troca_e_a_fonte(ambiente):  # noqa: F811
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        pagina_titulo="Guia de varejo",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="plataforma EAD",
        contexto="Para treinar a equipe, use uma plataforma EAD com trilhas.",
        status_http=404,
        artigo=_artigo("Plataforma EAD", "plataforma ead"),
    )
    texto = links_quebrados.email(link)
    assert "Guia de varejo" in texto and "https://morto.com.br/lms" in texto
    assert link.artigo.published_url in texto
    assert links_quebrados.ESTUDO_DO_PEW in texto


def test_marcar_enviado_com_data_e_conferir_a_conquista(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    artigo = _artigo("Plataforma EAD", "plataforma ead")
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="plataforma EAD",
        status_http=404,
        artigo=artigo,
    )
    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "contatado", "contatado_em": "2026-01-10"},
    )
    link.refresh_from_db()
    assert link.situacao == "contatado" and str(link.contatado_em) == "2026-01-10"

    pagina = (
        f"<html><body><article><p>{PARAGRAFO}"
        f'<a href="{artigo.published_url}">ead</a></p></article></body></html>'
    )
    monkeypatch.setattr(
        "apps.knowledge.web.baixar", lambda url: (pagina.encode(), url, "text/html")
    )
    links_quebrados.conferir_conquistas()
    link.refresh_from_db()
    assert link.situacao == "conquistado" and link.conquistado_em


def test_reprocessar_pela_tela(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="x",
        status_http=404,
    )
    chamados = []
    monkeypatch.setattr(links_quebrados, "reprocessar", lambda item: chamados.append(item.pk))
    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "reprocessar"},
    )
    link.refresh_from_db()
    assert chamados == [link.pk] and link.situacao == "novo"


def test_revisao_avisa_o_titulo_e_a_cobertura(ambiente):  # noqa: F811
    from apps.content.models import Article, Topic

    _, _, client = ambiente
    pauta = Topic.objects.create(title="O que e uma plataforma LMS", origin="link")
    LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="plataforma EAD",
        arquivo_titulo="O que e uma plataforma LMS",
        arquivo_trecho="Uma plataforma LMS organiza cursos e trilhas.",
        status_http=404,
        situacao="pauta",
        pauta=pauta,
    )
    artigo = Article.objects.create(
        title="Como escolher um LMS",
        slug="lms",
        topic=pauta,
        body_markdown="Uma plataforma LMS organiza cursos.",
    )
    cobertura = links_quebrados.cobertura_do_artigo(artigo)
    assert len(cobertura) == 1 and cobertura[0]["titulo_diferente"]
    html = client.get(
        reverse("content:revisar", args=[artigo.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Esta pauta nasceu de um link quebrado" in html


def _vetores(monkeypatch, mapa):
    """Embedding controlado: cada texto vira o vetor da primeira chave que contem."""
    import numpy as np

    def vetor(texto):
        for chave, valor in mapa.items():
            if chave in texto:
                return np.asarray(valor, dtype=np.float32)
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float32)

    monkeypatch.setattr("apps.radar.agrupamento._vetor", vetor)


def test_link_ganha_artigo_parecido_e_pode_usar(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    _vetores(monkeypatch, {"LMS": [1.0, 0.0, 0.0], "treinamento": [0.6, 0.8, 0.0]})
    monkeypatch.setattr("apps.radar.concorrentes.aderencia_da_consulta", lambda texto: 0.4)
    artigo = _artigo("Guia de treinamento de equipes", "treinamento")
    link = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        dominio="blog.com.br",
        link_url="https://morto.com.br/lms",
        texto="plataforma EAD",
        arquivo_titulo="O que e uma plataforma LMS",
        status_http=404,
    )
    # Distancia 0.4: entre PERTO e QUASE, vira sugestao.
    assert links_quebrados.comparar_com_o_publicado(artigo) == 1
    link.refresh_from_db()
    assert link.artigo is None and link.artigo_parecido == artigo
    assert link.parecido_proximidade == 0.6

    url = reverse("radar:imprensa", urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "Artigo parecido" in html and "Usar este artigo" in html

    client.post(
        reverse("radar:decidir_link_quebrado", args=[link.pk], urlconf="core.urls_tenants"),
        {"decisao": "usar_artigo"},
    )
    link.refresh_from_db()
    assert link.artigo == artigo and link.artigo_parecido is None


def test_avisa_artigo_ja_oferecido_em_outro_link(ambiente):  # noqa: F811
    _, _, client = ambiente
    artigo = _artigo("Plataforma LMS", "lms")
    for n, situacao in enumerate(["conquistado", "novo"]):
        LinkQuebrado.objects.create(
            pagina_url=f"https://blog{n}.com.br/guia",
            dominio=f"blog{n}.com.br",
            link_url="https://morto.com.br/lms",
            texto="plataforma lms",
            status_http=404,
            artigo=artigo,
            situacao=situacao,
        )
    html = client.get(reverse("radar:imprensa", urlconf="core.urls_tenants")).content.decode()
    assert "ja oferecido em 1 outro link" in html


def test_publicar_avisa_a_pauta_aberta_parecida(ambiente, monkeypatch):  # noqa: F811
    from apps.content.models import Topic
    from apps.radar import publicados

    _vetores(monkeypatch, {"LMS": [1.0, 0.0, 0.0]})
    parecida = Topic.objects.create(title="Como escolher um LMS")
    outra = Topic.objects.create(title="Receita de bolo")
    artigo = _artigo("Plataforma LMS: guia", "lms")

    assert publicados.rever_pautas(artigo) == 1
    parecida.refresh_from_db()
    outra.refresh_from_db()
    assert parecida.artigo_parecido == artigo and parecida.cannibalization_score == 1.0
    assert outra.artigo_parecido is None

    _, _, client = ambiente
    html = client.get(reverse("content:pautas", urlconf="core.urls_tenants")).content.decode()
    assert "ja ha artigo parecido" in html


def test_publicar_refaz_a_nota_dos_temas(radar, monkeypatch):  # noqa: F811
    from apps.radar import publicados
    from apps.radar.models import GrupoDeDemanda

    vistos = []
    monkeypatch.setattr(
        "apps.radar.agrupamento.pontuar", lambda grupo, **kw: vistos.append(grupo.pk)
    )
    monkeypatch.setattr("apps.radar.agrupamento.vetores_do_que_ja_foi_escrito", lambda: None)
    monkeypatch.setattr("apps.radar.agrupamento.vetor_do_negocio", lambda: None)
    vivo = GrupoDeDemanda.objects.create(rotulo="lms")
    GrupoDeDemanda.objects.create(rotulo="x", situacao="descartado")
    assert publicados.rever_temas() == 1 and vistos == [vivo.pk]


def test_publicacao_despacha_a_revisao_do_radar(monkeypatch):
    from apps.integrations import publishing

    chamados = []
    # O conftest troca _rever_o_radar; aqui vale a funcao de verdade.
    monkeypatch.undo()
    monkeypatch.setattr("apps.radar.tasks.depois_de_publicar.delay", lambda pk: chamados.append(pk))
    publishing._rever_o_radar("abc")
    assert chamados == ["abc"]


def test_link_que_voltou_a_funcionar_sai_da_lista(ambiente, monkeypatch):  # noqa: F811
    import datetime

    from django.utils import timezone

    antigo = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia",
        dominio="blog.com.br",
        link_url="https://vivo.com.br/x",
        texto="x",
        status_http=404,
        encontrado_em=timezone.now() - datetime.timedelta(days=2),
    )
    novo = LinkQuebrado.objects.create(
        pagina_url="https://blog.com.br/guia2",
        dominio="blog.com.br",
        link_url="https://vivo.com.br/y",
        texto="y",
        status_http=404,
    )
    monkeypatch.setattr(links_quebrados, "situacao_do_link", lambda url: None)
    assert links_quebrados.rechecar_links() == 1
    antigo.refresh_from_db()
    novo.refresh_from_db()
    assert antigo.situacao == "descartado" and antigo.rechecado_em
    assert novo.situacao == "novo"  # achado ha menos de um dia: ainda nao rechecado
