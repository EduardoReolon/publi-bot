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


@pytest.mark.django_db
def test_pauta_investigada_leva_o_debate_e_o_plano_por_frentes(artigo):
    pauta = artigo.topic
    pauta.debate = {
        "afirmacao": "Todo mundo precisa estudar IA.",
        "tese": "Falta especialista da area, nao quem estude IA.",
        "frentes": [
            {"nome": "Contra", "papel": "contra", "descricao": "a"},
            {"nome": "O que a midia diz", "papel": "discurso", "descricao": "b"},
            {"nome": "Especialista confere", "papel": "a_favor", "descricao": "c"},
        ],
    }
    pauta.save()
    pedido = outra_ia.pedido(artigo)
    assert "DEBATE DESTA PAUTA" in pedido and "Todo mundo precisa estudar IA." in pedido
    plano = pedido[pedido.index("Plano sugerido") :].split("\n")[0]
    # O cenario primeiro; depois a favor, contra; a conclusao por ultimo.
    assert (
        plano.index("o cenario")
        < plano.index("'Especialista confere'")
        < plano.index("'Contra'")
        < plano.index("conclusao honesta")
    )
    assert "'O que a midia diz'" not in plano
    assert "pauta investigada (frentes e debate: muito material)" in outra_ia.motivos_de_peso(pauta)


@pytest.mark.django_db
def test_refazer_com_outra_ia_usa_as_mesmas_fontes_e_arquiva_o_rascunho(tenant_com_acervo):
    """Do botao no artigo: o pedido sai das mesmas fontes, a geracao local
    pedida para ele para, e o rascunho antigo so sai de cena quando o texto
    colado e aceito."""
    from apps.content import fluxos
    from apps.ops.models import GenerationJob

    pauta = Topic.objects.create(
        title="Efeito no metabolismo", target_keyword="metabolismo", status="used"
    )
    antigo = Article.objects.create(
        title="Efeito no metabolismo", topic=pauta, status=Article.Status.PENDING_REVIEW
    )
    local = GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=str(pauta.pk),
        status=GenerationJob.Status.PENDING,
        step_payloads={"fluxo": fluxos.A, "substitui": str(antigo.pk)},
    )
    novo = outra_ia.preparar(pauta, refaz=antigo)
    local.refresh_from_db()
    assert local.status == GenerationJob.Status.FAILED and "outra IA" in local.last_error
    assert novo.thesis_json["substitui"] == str(antigo.pk)
    antigo.refresh_from_db()
    assert antigo.status == Article.Status.PENDING_REVIEW  # ainda aqui

    outra_ia.aplicar(novo, RESPOSTA)
    antigo.refresh_from_db()
    assert antigo.status == Article.Status.REJECTED
    assert antigo.thesis_json["substituido_por"] == str(novo.pk)


def test_titulos_por_rotulo_de_texto_e_um_paragrafo_por_linha():
    """SECAO:/SUBSECAO: sobrevivem a qualquer copia (o ## some quando se
    seleciona o texto na tela do chat); cada linha vira um paragrafo e a lista
    fica junta. Nada e adivinhado: frase curta continua frase."""
    colado = (
        f"CORPO DO ARTIGO:\nAbertura que comeca pela dor de quem le. {PARAGRAFO}\n"
        f"Segundo paragrafo da abertura. {PARAGRAFO}\n"
        "**SECAO:** Por que o esforco manual consome o lucro\n"
        f"Texto da secao com fonte [[FONTE_1]]. {PARAGRAFO}\n"
        "[[CHAMADA]]\n"
        "SUBSECAO: Um detalhe\n"
        "- item um\n- item dois\n"
        "SECAO: Onde aplicar no seu negocio\n"
        "Uma frase curta\n"
        f"Fecho do artigo. {PARAGRAFO}\n"
        "FIM DO ARTIGO"
    )
    corpo = outra_ia.ler(colado)["corpo"]
    abertura, secoes = outra_ia._secoes(corpo)
    assert [t for _n, t, _x in secoes] == [
        "Por que o esforco manual consome o lucro",
        "Onde aplicar no seu negocio",
    ]
    assert "\n\n" in abertura and "[[CHAMADA]]" in secoes[0][2]
    assert "### Um detalhe" in secoes[0][2] and "- item um\n- item dois" in secoes[0][2]
    assert "## Uma frase curta" not in corpo
    assert "BLOCO DE CODIGO" in outra_ia.PEDIDO_EM_BLOCO


@pytest.mark.django_db
def test_resposta_sem_titulo_nenhum_e_recusada_com_o_que_fazer(artigo):
    sem_titulos = RESPOSTA.replace("## ", "").replace("# Metabolismo\n\n", "")
    with pytest.raises(ValueError, match="sem os titulos"):
        outra_ia.aplicar(artigo, sem_titulos)
    assert artigo.sections.count() == 0


@pytest.mark.django_db
def test_colar_de_novo_no_artigo_que_veio_da_outra_ia(artigo):
    """A mesma resposta, colada de novo no mesmo artigo (mesmas fontes), refaz
    o texto e as secoes."""
    por_rotulo = RESPOSTA.replace("## ", "SECAO: ").replace("# Metabolismo\n\n", "")
    outra_ia.aplicar(artigo, por_rotulo)
    artigo.refresh_from_db()
    assert artigo.sections.count() == 3

    pauta = artigo.topic
    assert outra_ia.para_colar_de_novo(pauta, str(artigo.pk)) == artigo
    assert outra_ia.para_colar_de_novo(pauta, "nao-e-uuid") is None
    outra_ia.aplicar(artigo, RESPOSTA)
    artigo.refresh_from_db()
    assert artigo.sections.order_by("order").first().heading == "O que o estudo mostrou"


@pytest.mark.django_db
def test_outra_ia_escreve_o_texto_das_chamadas_junto(artigo, monkeypatch):
    """Com oferta, o pedido traz os blocos CHAMADA DO MEIO e CHAMADA DO FIM, com
    as mesmas regras do modelo daqui; o texto colado vira call_to_action_copy
    (o meio so com a marca), pelos mesmos limites. Faltou uma parte: o modelo
    daqui escreve."""
    from apps.content import tasks
    from apps.editorial.models import PerfilDoNegocio

    negocio = PerfilDoNegocio.carregar()
    negocio.oferta = "Conversa pelo WhatsApp para mapear o caixa da clinica."
    negocio.save()
    artigo.call_to_action = "end"
    artigo.save(update_fields=["call_to_action"])
    pedido = outra_ia.pedido(artigo)
    assert "CHAMADA DO MEIO:" in pedido and "CHAMADA DO FIM:" in pedido
    assert "ate 70 caracteres" in pedido and "So prometa o que esta na oferta" in pedido

    blocos = (
        "CHAMADA DO MEIO:\n**title:** O caixa some no fim do mes?\n"
        "text: A gente mapeia para onde ele vai, com os seus numeros.\nbutton: Quero entender\n"
        "CHAMADA DO FIM:\ntitle: Crescer sem apertar o caixa\n"
        "text: Uma conversa curta mostra por onde comecar.\nbutton: Conversar agora\n"
    )
    pedidas = []
    monkeypatch.setattr(tasks.escrever_chamada, "delay", pedidas.append)
    outra_ia.aplicar(artigo, RESPOSTA.replace("CORPO DO ARTIGO:", blocos + "CORPO DO ARTIGO:"))
    artigo.refresh_from_db()
    assert artigo.call_to_action == "inline"  # a marca [[CHAMADA]] esta no texto
    assert artigo.call_to_action_copy["inline"]["title"] == "O caixa some no fim do mes?"
    assert artigo.call_to_action_copy["end"]["button"] == "Conversar agora"
    assert pedidas == []

    # Sem os blocos: o modelo daqui escreve depois.
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda f: f())
    outra_ia.aplicar(artigo, RESPOSTA)
    assert pedidas == [str(artigo.pk)]


@pytest.mark.django_db
def test_texto_colado_ja_pede_as_capas_como_no_fluxo_local(
    artigo, django_capture_on_commit_callbacks
):
    """Sem isso o artigo da outra IA chegava a revisao sem nenhuma opcao de
    imagem, e alguem tinha de lembrar de clicar em "gerar capas"."""
    from unittest import mock

    from apps.inference.models import InferenceConnection
    from apps.ops.models import GenerationJob

    InferenceConnection.objects.create(
        name="Geracao de imagem",
        kind=InferenceConnection.Kind.IMAGE,
        base_url="http://127.0.0.1:8101",
        workloads=[InferenceConnection.Workload.IMAGE],
        default_model="sdxl",
        is_active=True,
    )
    capas = GenerationJob.objects.filter(
        kind=GenerationJob.Kind.ARTICLE_COVER, target_object_id=str(artigo.pk)
    )

    with mock.patch("apps.ops.tasks.advance_generation_job.delay") as disparo:
        with django_capture_on_commit_callbacks(execute=True):
            outra_ia.aplicar(artigo, RESPOSTA)
        assert capas.count() == 1
        assert disparo.call_args.args == (str(capas.get().pk),)

        outra_ia.aplicar(artigo, RESPOSTA)  # colar de novo nao pede outro lote
    assert capas.count() == 1


@pytest.mark.django_db
def test_sem_gerador_de_imagem_o_texto_colado_nao_pede_capa(artigo):
    from apps.ops.models import GenerationJob

    outra_ia.aplicar(artigo, RESPOSTA)
    assert not GenerationJob.objects.filter(kind=GenerationJob.Kind.ARTICLE_COVER).exists()
