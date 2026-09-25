"""Perguntas frequentes do artigo: sugeridas pelo modelo, decididas por quem revisa.

O FAQ nao existe pelo resultado rico do Google, que acabou; existe como
conteudo. Estes testes guardam as tres coisas que o fazem valer a pena:

* sai so o que a pessoa marcou, e o modelo so SUGERE;
* nao traz link — no FAQ nao ha citacao, entao nao ha destino legitimo;
* nao derruba o artigo pronto se o modelo errar o formato.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.content.models import Article, ArticleFaq, Topic
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job

# Fixtures emprestadas: o tenant com acervo e o cliente autenticado ja existem
# nos arquivos vizinhos, e uma segunda copia divergiria.
from tests.test_flows import ROTEIRO_DO_ARTIGO, ModeloFalso, _rodar_ate_o_fim
from tests.test_integrations import site, tenant_integracoes  # noqa: F401
from tests.test_interface import ambiente, artigo_para_revisar, autora  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """O que se olha aqui e o FAQ, e nao a busca."""


# ---------------------------------------------------------------------------
# A leitura do que o modelo devolve
# ---------------------------------------------------------------------------
def test_par_com_endereco_da_web_e_descartado_e_o_resto_fica():
    from apps.content.faq import interpretar

    texto = json.dumps(
        {
            "perguntas": [
                {"pergunta": "O que e?", "resposta": "E isto [[FONTE_1]]."},
                {"pergunta": "Onde leio?", "resposta": "Em https://golpe.exemplo.com."},
                {"pergunta": "o que e?", "resposta": "Repetida, com outra caixa."},
                {"pergunta": "", "resposta": "Sem pergunta."},
            ]
        }
    )

    assert interpretar(texto) == [("O que e?", "E isto .")]


def test_o_modelo_nao_consegue_empurrar_mais_que_o_teto():
    from apps.content.faq import SUGERIDAS, interpretar

    texto = json.dumps(
        {"perguntas": [{"pergunta": f"P{n}?", "resposta": f"R{n}."} for n in range(20)]}
    )

    assert len(interpretar(texto)) == SUGERIDAS


def test_json_ruim_e_erro_legivel():
    from apps.content.faq import interpretar

    with pytest.raises(ValueError, match="JSON"):
        interpretar("Aqui estao as perguntas: ...")


# ---------------------------------------------------------------------------
# No fluxo do artigo
# ---------------------------------------------------------------------------
def _gerar_artigo(monkeypatch, roteiro):
    modelo = ModeloFalso(roteiro)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)
    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))
    return Article.objects.get(topic=topic), GenerationJob.objects.get(pk=job.pk), modelo


@pytest.mark.django_db
def test_o_artigo_nasce_com_faq_e_as_mais_relevantes_marcadas(
    tenant_com_acervo, conexao, monkeypatch
):
    from apps.content.faq import MARCADAS

    artigo, job, modelo = _gerar_artigo(monkeypatch, ROTEIRO_DO_ARTIGO)

    assert job.status == GenerationJob.Status.DONE
    faq = list(artigo.faq.all())
    assert len(faq) == 5
    assert [item.is_selected for item in faq] == [True] * MARCADAS + [False] * (5 - MARCADAS)

    # O modelo ve as secoes que o artigo ja cobre, para nao repeti-las.
    assert "O que a literatura mostra" in modelo.chamadas[-1]["user"]


@pytest.mark.django_db
def test_faq_ilegivel_nao_derruba_o_artigo(tenant_com_acervo, conexao, monkeypatch):
    roteiro = [*ROTEIRO_DO_ARTIGO[:-1], "isto nao e json"]

    artigo, job, _ = _gerar_artigo(monkeypatch, roteiro)

    assert job.status == GenerationJob.Status.DONE
    assert artigo.status == Article.Status.PENDING_REVIEW
    assert artigo.faq.count() == 0
    payload = next(p for p in job.step_payloads.values() if "faq" in p)
    assert "JSON" in payload["motivo"]


# ---------------------------------------------------------------------------
# O que vai para o site
# ---------------------------------------------------------------------------
def _faq(artigo, pergunta, resposta, *, marcada=True, ordem=1):
    return ArticleFaq.objects.create(
        article=artigo, order=ordem, question=pergunta, answer=resposta, is_selected=marcada
    )


@pytest.mark.django_db
def test_so_as_marcadas_saem_e_depois_do_corpo(site):  # noqa: F811
    from apps.integrations.publishing import montar_payload_de_artigo

    artigo = Article.objects.create(title="T", body_html="<p>Corpo.</p>")
    _faq(artigo, "Sai?", "Sai sim.", ordem=1)
    _faq(artigo, "Fica de fora?", "Fica.", marcada=False, ordem=2)

    html = montar_payload_de_artigo(artigo, site)["html_content"]

    assert html.startswith("<p>Corpo.</p>")
    assert "<h2>Perguntas frequentes</h2>" in html
    assert "<h3>Sai?</h3>" in html
    assert "Fica de fora?" not in html


@pytest.mark.django_db
def test_sem_nenhuma_marcada_o_corpo_sai_intacto(site):  # noqa: F811
    from apps.integrations.publishing import montar_payload_de_artigo

    artigo = Article.objects.create(title="T", body_html="<p>Corpo.</p>")
    _faq(artigo, "Desmarcada?", "Sim.", marcada=False)

    assert montar_payload_de_artigo(artigo, site)["html_content"] == "<p>Corpo.</p>"


@pytest.mark.django_db
def test_o_faq_nao_leva_link_nem_script(site):  # noqa: F811
    """Uma resposta editada a mao passa pela mesma sanitizacao do corpo, e
    ainda perde todo link: no FAQ nao ha fonte que o justifique."""
    from apps.content.faq import montar_html

    artigo = Article.objects.create(title="T", body_html="<p>x</p>")
    _faq(artigo, "<b>Negrito?</b>", "Veja [aqui](https://spam.exemplo.com). <script>x()</script>")

    html = montar_html(artigo, "pt-BR")

    assert "&lt;b&gt;Negrito?&lt;/b&gt;" in html
    assert "spam.exemplo.com" not in html
    assert "<script" not in html
    assert "aqui" in html


@pytest.mark.django_db
def test_o_titulo_do_bloco_segue_o_idioma_do_site(site):  # noqa: F811
    from apps.content.faq import montar_html

    artigo = Article.objects.create(title="T", body_html="<p>x</p>")
    _faq(artigo, "Why?", "Because.")

    assert "<h2>Frequently asked questions</h2>" in montar_html(artigo, "en-US")


# ---------------------------------------------------------------------------
# A tela de revisao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_marcar_editar_apagar_e_acrescentar_num_envio_so(ambiente, artigo_para_revisar):  # noqa: F811
    _, _, client = ambiente
    artigo = artigo_para_revisar
    fica = _faq(artigo, "Antiga?", "Resposta antiga.", marcada=False, ordem=1)
    sai = _faq(artigo, "Apagar?", "Sim.", ordem=2)

    resposta = client.post(
        reverse("content:salvar_faq", args=[artigo.pk], urlconf="core.urls_tenants"),
        {
            f"faq_{fica.pk}_pergunta": "Antiga, revisada?",
            f"faq_{fica.pk}_resposta": "Resposta nova.",
            f"faq_{fica.pk}_incluir": "1",
            f"faq_{sai.pk}_pergunta": sai.question,
            f"faq_{sai.pk}_resposta": sai.answer,
            f"faq_{sai.pk}_apagar": "1",
            "faq_nova_pergunta": "Escrita a mao?",
            "faq_nova_resposta": "Sim, e entra marcada.",
        },
    )

    assert resposta.status_code == 302
    fica.refresh_from_db()
    assert fica.question == "Antiga, revisada?"
    assert fica.answer == "Resposta nova."
    assert fica.is_selected
    assert not ArticleFaq.objects.filter(pk=sai.pk).exists()

    nova = artigo.faq.get(question="Escrita a mao?")
    assert nova.is_selected
    assert nova.origin == ArticleFaq.Origin.HUMAN
    assert nova.order > fica.order


@pytest.mark.django_db
def test_desmarcar_vale_sem_mexer_no_texto(ambiente, artigo_para_revisar):  # noqa: F811
    _, _, client = ambiente
    item = _faq(artigo_para_revisar, "Marcada?", "Sim.")

    client.post(
        reverse("content:salvar_faq", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        {f"faq_{item.pk}_pergunta": item.question, f"faq_{item.pk}_resposta": item.answer},
    )

    item.refresh_from_db()
    assert not item.is_selected
    assert item.answer == "Sim."


@pytest.mark.django_db
def test_a_tela_mostra_o_faq_e_a_previa(ambiente, artigo_para_revisar):  # noqa: F811
    _, _, client = ambiente
    _faq(artigo_para_revisar, "Aparece na previa?", "Aparece.")

    pagina = client.get(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants")
    ).content.decode()

    assert "Salvar perguntas frequentes" in pagina
    assert "<h3>Aparece na previa?</h3>" in pagina
