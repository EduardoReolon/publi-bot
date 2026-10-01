"""Artigo escrito por um modelo grande de fora, com as travas de sempre.

* preparar separa as fontes do acervo e cria o artigo em espera;
* o pedido leva as fontes numeradas e proibe busca na web;
* o texto colado vira secoes, FAQ, meta e corpo com os links das citacoes;
* endereco escrito pelo modelo, ou fonte que nao existe, recusa o texto;
* a marca [[CHAMADA]] colada vira a chamada no meio;
* desistir devolve a pauta como estava.
"""

from __future__ import annotations

import pytest

from apps.content import outra_ia
from apps.content.models import Article, Topic
from apps.content.rendering import LinkAlucinado
from tests.test_interface import ambiente  # noqa: F401

PARAGRAFO = " ".join(f"palavra{i}" for i in range(60))

RESPOSTA = f"""Claro! Aqui esta:

**TITULO SUGERIDO:** Metabolismo: o que o estudo mostrou de fato
META DESCRIPTION: O que se sabe sobre o efeito no metabolismo, segundo o estudo.
RESUMO CURTO: O efeito existe. O estudo mostra quando.
PERGUNTAS FREQUENTES:
P: O efeito e rapido?
R: Depende do caso [[FONTE_1]].
P: Serve para todos?
R: Nao ha dado para dizer.
CORPO DO ARTIGO:
# Metabolismo

Abertura curta que entrega a resposta. {PARAGRAFO}

## O que o estudo mostrou

Segundo [[FONTE_1]], o efeito foi observado. {PARAGRAFO}

## Perguntas frequentes sobre o efeito

Nota: este titulo nao pode cortar o corpo. {PARAGRAFO}

[[CHAMADA]]

## O que fazer agora

Fecho. {PARAGRAFO}
FIM DO ARTIGO

Espero ter ajudado!
"""


@pytest.fixture
def artigo(tenant_com_acervo):
    pauta = Topic.objects.create(
        title="Efeito no metabolismo", target_keyword="metabolismo", status="approved"
    )
    return outra_ia.preparar(pauta)


@pytest.mark.django_db
def test_preparar_separa_as_fontes_e_o_pedido_fecha_a_porta(artigo):
    assert artigo.status == Article.Status.DRAFTING
    assert artigo.citations.count() == 1
    assert artigo.topic.status == Topic.Status.USED
    pedido = outra_ia.pedido(artigo)
    assert '<fonte numero="1"' in pedido and "metabolismo" in pedido
    assert "NAO pesquise na web" in pedido
    assert "CORPO DO ARTIGO:" in pedido and "FIM DO ARTIGO" in pedido
    assert outra_ia.artigo_em_espera(artigo.topic) == artigo


@pytest.mark.django_db
def test_texto_colado_vira_artigo_com_os_links_das_citacoes(artigo):
    artigo.call_to_action = "end"
    artigo.save(update_fields=["call_to_action"])

    outra_ia.aplicar(artigo, RESPOSTA)
    artigo.refresh_from_db()

    assert artigo.status == Article.Status.PENDING_REVIEW
    assert "https://revista.exemplo.org/estudo" in artigo.body_markdown
    assert "[[FONTE_1]]" not in artigo.body_markdown
    assert "Espero ter ajudado" not in artigo.body_markdown
    assert "# Metabolismo" not in artigo.body_markdown
    assert "Nota: este titulo" in artigo.body_markdown
    assert [s.heading for s in artigo.sections.all()] == [
        "O que o estudo mostrou",
        "Perguntas frequentes sobre o efeito",
        "O que fazer agora",
    ]
    assert artigo.sections.get(order=1).carries_central_idea
    assert artigo.meta_description.startswith("O que se sabe")
    assert artigo.thesis_json["titulos_sugeridos"] == [
        "Metabolismo: o que o estudo mostrou de fato"
    ]
    assert [f.question for f in artigo.faq.all()] == ["O efeito e rapido?", "Serve para todos?"]
    assert "FONTE" not in artigo.faq.first().answer
    # A marca colada depois da 2a secao pos a chamada no meio.
    assert artigo.call_to_action == "inline" and artigo.call_to_action_after == 2


@pytest.mark.django_db
def test_endereco_ou_fonte_inexistente_recusam_o_texto(artigo):
    with pytest.raises(LinkAlucinado):
        outra_ia.aplicar(artigo, RESPOSTA.replace("Fecho.", "Veja https://x.com. Fecho."))
    with pytest.raises(ValueError, match="nao existe"):
        outra_ia.aplicar(artigo, RESPOSTA.replace("Segundo [[FONTE_1]]", "Segundo [[FONTE_7]]"))
    with pytest.raises(ValueError, match="CORPO"):
        outra_ia.aplicar(artigo, "TITULO SUGERIDO: so isso")
    artigo.refresh_from_db()
    assert artigo.status == Article.Status.DRAFTING and not artigo.sections.exists()


@pytest.mark.django_db
def test_sem_chamada_a_marca_colada_sai(artigo):
    artigo.call_to_action = "none"
    artigo.save(update_fields=["call_to_action"])
    outra_ia.aplicar(artigo, RESPOSTA)
    artigo.refresh_from_db()
    assert "[[CHAMADA]]" not in artigo.body_markdown and artigo.call_to_action == "none"


@pytest.mark.django_db
def test_desistir_devolve_a_pauta(artigo):
    pauta = artigo.topic
    outra_ia.desistir(artigo)
    pauta.refresh_from_db()
    assert pauta.status == "approved" and not pauta.articles.exists()


def test_pauta_de_peso():
    leve = Topic(title="x", demand_score=40, evidence={"volume_total": 90})
    pesada = Topic(
        title="y",
        demand_score=75,
        evidence={"volume_total": 1200, "parcelas": {"comercial": 0.8}},
    )
    assert outra_ia.motivos_de_peso(leve) == []
    assert outra_ia.motivos_de_peso(pesada) == [
        "1200 buscas/mes",
        "nota 75 no radar",
        "valor comercial alto",
    ]


@pytest.mark.django_db
def test_tela_da_pauta_oferece_o_caminho(ambiente, embedding_falso):  # noqa: F811
    from django.urls import reverse

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Tema sem acervo", status="approved")
    lista = client.get(reverse("content:pautas", urlconf="core.urls_tenants")).content.decode()
    assert "Abrir a pauta" in lista
    pagina = client.get(
        reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Artigo com outra IA" in pagina

    url = reverse("content:artigo_por_outra_ia", args=[pauta.pk], urlconf="core.urls_tenants")
    assert "Separar as fontes" in client.get(url).content.decode()
    resposta = client.post(url, {"acao": "preparar"})
    assert resposta.status_code == 302  # sem acervo: volta para as pautas, com o aviso
    assert not pauta.articles.exists()


@pytest.mark.django_db
def test_so_fonte_de_contexto_para_como_no_caminho_de_sempre(tenant_com_acervo):
    """A regra e a do modelo local (fontes_da_pauta): sem trecho que possa
    sustentar a ideia central, nao ha pedido nem artigo."""
    from apps.content.services import SemEmbasamentoCentral
    from apps.knowledge.models import SuperChunk

    SuperChunk.objects.update(supports_central_idea=False)
    pauta = Topic.objects.create(title="Efeito no metabolismo", status="approved")
    with pytest.raises(SemEmbasamentoCentral):
        outra_ia.preparar(pauta)
    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.WAITING_SOURCES
    assert not pauta.articles.exists()
