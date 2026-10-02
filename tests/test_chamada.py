"""Chamada para a oferta do site: se cabe, onde, e como chega ao site.

* sem modelo de linguagem: proximidade (embedding) entre a oferta do Guia
  editorial e o tema e as secoes;
* a marca [[CHAMADA]] vira <aside data-publibot="chamada"> so no fim da
  conversao; um aside escrito no texto nao passa;
* editar o texto a mao manda: mover ou apagar a marca muda o modo;
* no payload, o campo call_to_action; fora do modo inline, sem a marca;
* tema longe da oferta: o fecho nao recebe convite.
"""

from __future__ import annotations

import numpy as np
import pytest
from django.urls import reverse

from apps.content import chamada
from apps.content.models import Article, ArticleSection, Topic
from apps.content.rendering import markdown_para_html
from tests.test_integrations import site, tenant_integracoes  # noqa: F401
from tests.test_interface import ambiente  # noqa: F401

OFERTA = "Mande as notas de material de construcao e um engenheiro diz se voce pagou caro"

# Vetores controlados: a primeira coordenada e "custo de obra", a segunda
# "conceito teorico". A oferta e puro custo.
VETORES = {
    OFERTA: [1.0, 0.0],
    "Como saber se o orcamento da obra esta caro": [1.0, 0.1],
    "Conferir o preco de cada item da nota": [1.0, 0.05],
    "O que e BDI": [0.0, 1.0],
    "Historia do BDI": [0.0, 1.0],
}


@pytest.fixture
def vetores(monkeypatch):
    def _vetor(texto):
        for chave, vetor in VETORES.items():
            if texto.startswith(chave):
                return np.asarray(vetor, dtype=np.float32)
        return np.asarray([0.0, 1.0], dtype=np.float32)

    monkeypatch.setattr(chamada, "_vetor", _vetor)


def _perfil(oferta=OFERTA):
    from apps.editorial.models import EditorialProfile, PerfilDoNegocio

    negocio = PerfilDoNegocio.carregar()
    negocio.oferta = oferta
    negocio.save()
    perfil = EditorialProfile.carregar()
    perfil.convite = "Convide para mandar a nota pelo WhatsApp."
    perfil.save()
    return perfil


def _artigo(titulo, secoes, **campos):
    artigo = Article.objects.create(title=titulo, **campos)
    for ordem, (titulo_da_secao, texto) in enumerate(secoes, start=1):
        ArticleSection.objects.create(
            article=artigo, order=ordem, heading=titulo_da_secao, body_markdown=texto
        )
    return artigo


@pytest.mark.django_db
def test_secao_perto_da_oferta_recebe_a_chamada_no_meio(site, vetores):  # noqa: F811
    _perfil()
    artigo = _artigo(
        "Como saber se o orcamento da obra esta caro",
        [
            ("O que e BDI", "Texto."),
            ("Conferir o preco de cada item da nota", "Texto."),
            ("Historia do BDI", "Texto."),
        ],
    )

    decisao = chamada.aplicar_decisao(artigo)

    assert decisao["modo"] == "inline" and decisao["secao"] == 2
    artigo.refresh_from_db()
    assert artigo.call_to_action == "inline" and artigo.call_to_action_after == 2

    from apps.content.services import montar_markdown_das_secoes

    markdown = montar_markdown_das_secoes(artigo)
    assert markdown.index("Conferir o preco") < markdown.index(chamada.MARCA)
    assert markdown.index(chamada.MARCA) < markdown.index("Historia do BDI")


@pytest.mark.django_db
def test_primeira_secao_nunca_recebe_e_tema_longe_nao_leva_chamada(site, vetores):  # noqa: F811
    _perfil()
    longe = _artigo(
        "O que e BDI", [("Conferir o preco de cada item da nota", "x"), ("Historia do BDI", "x")]
    )
    assert chamada.decidir(longe)["modo"] == "none"

    perto = _artigo(
        "Como saber se o orcamento da obra esta caro",
        [("Conferir o preco de cada item da nota", "x"), ("Historia do BDI", "x")],
    )
    assert chamada.decidir(perto)["modo"] == "end"


@pytest.mark.django_db
def test_pauta_escolhe_e_sem_oferta_fica_no_fim(site, vetores):  # noqa: F811
    _perfil(oferta="")
    from apps.editorial.models import EditorialProfile

    EditorialProfile.objects.update(convite="")
    sem_oferta = _artigo("O que e BDI", [("A", "x"), ("B", "x")])
    assert chamada.decidir(sem_oferta)["modo"] == "end"

    _perfil()
    pauta = Topic.objects.create(title="O que e BDI", call_to_action="inline")
    forcado = _artigo("O que e BDI", [("A", "x"), ("B", "x"), ("C", "x")], topic=pauta)
    decisao = chamada.decidir(forcado)
    assert decisao["modo"] == "inline" and decisao["secao"] == 2
    assert decisao["motivo"] == "escolhida na pauta"


def test_a_marca_vira_o_elemento_so_depois_da_sanitizacao():
    html = markdown_para_html(
        "Texto.\n\n[[CHAMADA]]\n\n## Depois\n\n"
        'Mais.<aside data-publibot="chamada" onclick="x()">falso</aside>\n\n[[CHAMADA]]'
    )

    assert html.count('<aside data-publibot="chamada"></aside>') == 1
    assert "onclick" not in html and "falso</aside>" not in html
    assert "[[CHAMADA]]" not in html


def test_inserir_e_achar_a_marca():
    texto = "Abertura.\n\n## Um\n\nA.\n\n## Dois\n\nB.\n\n## Tres\n\nC.\n\nFecho."
    com_marca = chamada.inserir_marca(texto, 2)
    assert chamada.posicao_da_marca(com_marca) == 2
    assert com_marca.index("B.") < com_marca.index("[[CHAMADA]]") < com_marca.index("## Tres")
    # Reinserir nao duplica.
    assert chamada.inserir_marca(com_marca, 1).count("[[CHAMADA]]") == 1
    assert chamada.inserir_marca(texto, 9).endswith("[[CHAMADA]]")


@pytest.mark.django_db
def test_editar_a_mao_move_ou_tira_a_chamada(ambiente):  # noqa: F811
    from apps.content.services import aplicar_edicao_humana

    artigo = _artigo("Tema", [("Um", "A."), ("Dois", "B."), ("Tres", "C.")])
    artigo.body_markdown = "## Um\n\nA.\n\n## Dois\n\nB.\n\n## Tres\n\nC."
    artigo.save()

    aplicar_edicao_humana(artigo, "## Um\n\nA.\n\n[[CHAMADA]]\n\n## Dois\n\nB.", editor=None)
    artigo.refresh_from_db()
    assert artigo.call_to_action == "inline" and artigo.call_to_action_after == 1
    assert 'data-publibot="chamada"' in artigo.body_html

    aplicar_edicao_humana(artigo, "## Um\n\nA.\n\n## Dois\n\nB.", editor=None)
    artigo.refresh_from_db()
    assert artigo.call_to_action == "end" and artigo.call_to_action_after is None


@pytest.mark.django_db
def test_mudar_na_revisao_leva_o_texto_junto(ambiente):  # noqa: F811
    _, _, client = ambiente
    artigo = _artigo("Tema", [("Um", "A."), ("Dois", "B."), ("Tres", "C.")])
    artigo.body_markdown = "## Um\n\nA.\n\n## Dois\n\nB.\n\n## Tres\n\nC."
    artigo.status = Article.Status.PENDING_REVIEW
    artigo.save()
    url = reverse("content:mudar_chamada", args=[artigo.pk], urlconf="core.urls_tenants")

    client.post(url, {"modo": "inline", "secao": "2"})
    artigo.refresh_from_db()
    assert artigo.call_to_action_after == 2
    assert artigo.body_markdown.index("B.") < artigo.body_markdown.index("[[CHAMADA]]")

    client.post(url, {"modo": "none"})
    artigo.refresh_from_db()
    assert artigo.call_to_action == "none" and "[[CHAMADA]]" not in artigo.body_markdown

    pagina = client.get(
        reverse("content:revisar", args=[artigo.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Chamada para a oferta" in pagina


@pytest.mark.django_db
def test_payload_leva_o_modo_e_so_leva_a_marca_no_meio(site):  # noqa: F811
    from apps.integrations.publishing import montar_payload_de_artigo

    artigo = Article.objects.create(
        title="Tema",
        body_html='<p>A.</p><aside data-publibot="chamada"></aside><p>B.</p>',
        call_to_action="inline",
    )
    payload = montar_payload_de_artigo(artigo, site)
    assert payload["call_to_action"] == "inline"
    assert 'data-publibot="chamada"' in payload["html_content"]

    artigo.call_to_action = "end"
    payload = montar_payload_de_artigo(artigo, site)
    assert payload["call_to_action"] == "end"
    assert "aside" not in payload["html_content"]


@pytest.mark.django_db
def test_sem_chamada_o_fecho_nao_convida(site):  # noqa: F811
    from apps.editorial.services import texto_do_guia

    perfil = _perfil()
    com = texto_do_guia(perfil, chave="article_framing")
    sem = texto_do_guia(perfil, chave="article_framing", com_convite=False)
    assert "Convite final" in com and "Convite final" not in sem


@pytest.mark.django_db
def test_sem_modelo_de_embedding_fica_no_fim_sem_derrubar(site, monkeypatch):  # noqa: F811
    _perfil()

    def quebrado(_texto):
        raise RuntimeError("modelo indisponivel")

    monkeypatch.setattr(chamada, "_vetor", quebrado)
    artigo = _artigo("Tema", [("A", "x"), ("B", "x")])

    decisao = chamada.decidir(artigo)

    assert decisao["modo"] == "end"
    assert "modelo indisponivel" in decisao["motivo"]


@pytest.mark.django_db
def test_marca_segue_o_titulo_mesmo_com_secao_vazia(ambiente):  # noqa: F811
    from apps.content.services import aplicar_edicao_humana

    # A secao 1 nao tem texto: nao aparece no corpo.
    artigo = _artigo("Tema", [("Um", ""), ("Dois", "B."), ("Tres", "C.")])
    artigo.body_markdown = "## Dois\n\nB.\n\n## Tres\n\nC."
    artigo.save()

    aplicar_edicao_humana(artigo, "## Dois\n\nB.\n\n[[CHAMADA]]\n\n## Tres\n\nC.", editor=None)
    artigo.refresh_from_db()
    assert artigo.call_to_action_after == 2

    chamada.mudar(artigo, "inline", 3, editor=None)
    artigo.refresh_from_db()
    assert artigo.body_markdown.rstrip().endswith("[[CHAMADA]]")
    assert artigo.call_to_action_after == 3


def _modelo_da_chamada(monkeypatch, pedidos):
    import json

    from apps.content import inference

    def executar(**kwargs):
        pedidos.append(kwargs)
        texto = {
            "meio": {
                "title": "Pagou caro <b>na obra</b>?",
                "text": "Mande a nota.",
                "button": "Quero",
            },
            "fim": {
                "title": "Confira sua nota",
                "text": "  Um engenheiro\n confere. ",
                "button": "",
            },
        }
        return type("R", (), {"texto": json.dumps(texto)})()

    monkeypatch.setattr(inference, "executar_prompt", executar)


@pytest.mark.django_db
def test_texto_da_chamada_parte_do_trecho_e_so_o_que_o_modo_usa(site, monkeypatch):  # noqa: F811
    _perfil()
    pedidos = []
    _modelo_da_chamada(monkeypatch, pedidos)
    artigo = _artigo(
        "Tema",
        [("Um", "A."), ("Conferir o preco", "Compare a nota."), ("Fim", "Fecho.")],
        call_to_action="inline",
        call_to_action_after=2,
    )

    texto = chamada.escrever_texto(artigo, site=site)

    variaveis = pedidos[0]["variaveis"]
    assert "Compare a nota." in variaveis["trecho_do_meio"]
    assert "Fecho." in variaveis["fecho"]
    assert texto["inline"] == {
        "title": "Pagou caro na obra?",
        "text": "Mande a nota.",
        "button": "Quero",
    }
    assert texto["end"]["text"] == "Um engenheiro confere."
    artigo.refresh_from_db()
    assert artigo.call_to_action_copy == texto

    artigo.call_to_action = "end"
    assert set(chamada.escrever_texto(artigo, site=site)) == {"end"}

    artigo.call_to_action = "none"
    assert chamada.escrever_texto(artigo, site=site) == {}
    assert len(pedidos) == 2


@pytest.mark.django_db
def test_payload_leva_o_texto_da_chamada_do_modo(site):  # noqa: F811
    from apps.integrations.publishing import montar_payload_de_artigo

    meio = {"title": "M", "text": "m", "button": "b"}
    fim = {"title": "F", "text": "f", "button": "b"}
    artigo = Article.objects.create(
        title="Tema", call_to_action="end", call_to_action_copy={"inline": meio, "end": fim}
    )
    assert montar_payload_de_artigo(artigo, site)["call_to_action_copy"] == {"end": fim}

    artigo.call_to_action = "inline"
    assert montar_payload_de_artigo(artigo, site)["call_to_action_copy"] == {
        "inline": meio,
        "end": fim,
    }

    artigo.call_to_action = "none"
    assert "call_to_action_copy" not in montar_payload_de_artigo(artigo, site)


@pytest.mark.django_db
def test_editar_o_texto_da_chamada_na_revisao(ambiente):  # noqa: F811
    _, _, client = ambiente
    artigo = _artigo("Tema", [("Um", "A."), ("Dois", "B.")], call_to_action="inline")
    artigo.call_to_action_after = 2
    artigo.save()
    pagina = client.get(
        reverse("content:revisar", args=[artigo.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert 'name="inline_title"' in pagina and 'name="end_title"' in pagina

    url = reverse("content:texto_da_chamada", args=[artigo.pk], urlconf="core.urls_tenants")
    client.post(
        url,
        {
            "end_title": "Fim",
            "end_text": "Texto do fim",
            "end_button": "Ir",
            "inline_title": "So titulo",
        },
    )
    artigo.refresh_from_db()
    assert artigo.call_to_action_copy == {
        "end": {"title": "Fim", "text": "Texto do fim", "button": "Ir"}
    }
