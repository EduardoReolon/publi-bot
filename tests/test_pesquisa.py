"""Pesquisa da pauta: hipoteses, busca semantica, angulos, sinteses e os dois fluxos."""

from __future__ import annotations

import json

import httpx
import pytest
from django.urls import reverse

from apps.content.models import Topic
from apps.knowledge import pesquisa
from apps.knowledge.models import CandidatoDeFonte, Document
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar e ordenar carregariam o modelo real."""


def _resumo(texto: str) -> dict:
    """O indice invertido do OpenAlex: cada palavra com TODAS as posicoes."""
    indice: dict = {}
    for posicao, palavra in enumerate(texto.split()):
        indice.setdefault(palavra, []).append(posicao)
    return indice


def _trabalho(n: int, *, titulo: str, relacionados=()) -> dict:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/10.1/{n}",
        "display_name": titulo,
        "publication_year": 2015 + n % 8,
        "cited_by_count": 10 * n,
        "language": "en",
        "type": "article",
        "abstract_inverted_index": _resumo(
            f"{titulo} study with customers of small services and their loyalty " * 4
        ),
        "citation_normalized_percentile": {"value": 0.5 + n / 100},
        "primary_topic": {
            "display_name": "Customer Service Quality and Loyalty",
            "field": {"display_name": "Business"},
        },
        "related_works": list(relacionados),
        "relevance_score": 1.0,
    }


@pytest.fixture
def openalex(monkeypatch):
    """Cada busca semantica devolve trabalhos diferentes; a de ids, os relacionados."""
    chamadas = []

    def get(url, params, timeout):
        chamadas.append(params)
        if "search.semantic" in params:
            base = 100 * len([c for c in chamadas if "search.semantic" in c])
            itens = [
                _trabalho(
                    base + i,
                    titulo=f"Service recovery angle {base} paper {i}",
                    relacionados=[f"https://openalex.org/W{900 + i}"],
                )
                for i in range(4)
            ]
        else:
            itens = [_trabalho(900, titulo="Related justice in service recovery")]
        return httpx.Response(200, json={"results": itens}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(pesquisa.time, "sleep", lambda s: None)
    return chamadas


@pytest.fixture
def modelo(monkeypatch):
    """O modelo responde as hipoteses (refutavel) e as sinteses (com um pedido)."""
    from apps.content import inference

    pedidos = []

    def executar(*, key, variaveis, **kw):
        pedidos.append(key)
        if key == "research_hypotheses":
            texto = {
                "angulos": [
                    {"angulo": "mecanismo", "paragrafo": "Why service failures hurt loyalty."},
                    {"angulo": "solucao", "paragrafo": "Recovery strategies restore trust."},
                    {"angulo": "outro", "paragrafo": "Employee empowerment and apology."},
                ],
                "refutavel": True,
                "contrarias": [
                    {"angulo": "contra 1", "paragrafo": "Recovery does not restore loyalty."},
                    {"angulo": "contra 2", "paragrafo": "Paradox is a myth."},
                ],
            }
        else:
            texto = {
                "sintese": "Os estudos [1] mostram algo.",
                "pedidos": [{"artigo": 1, "o_que": "a amostra"}],
            }
        return type("R", (), {"texto": json.dumps(texto)})()

    monkeypatch.setattr(inference, "executar_prompt", executar)
    return pedidos


def test_pesquisa_completa(ambiente, openalex, modelo):  # noqa: F811
    pauta = Topic.objects.create(title="Recuperar o cliente depois de uma falha", briefing="Base.")
    resultado = pesquisa.pesquisar(pauta)

    semanticas = [c for c in openalex if "search.semantic" in c]
    assert len(semanticas) == 5  # 3 angulos + 2 contrarias
    assert any("openalex:" in c.get("filter", "") for c in openalex)  # bola de neve
    assert resultado["relacionados"] == 1
    assert 0 < len(resultado["artigos"]) <= pesquisa.ESCOLHIDOS
    assert any(a["contraponto"] for a in resultado["angulos"])
    assert all(a.get("sintese") for a in resultado["angulos"])
    # O pedido so vale no angulo que tem o artigo 1.
    pedidos = [p for a in resultado["angulos"] for p in a.get("pedidos", [])]
    assert [p["o_que"] for p in pedidos] == ["a amostra"]

    # As fontes: candidatos aprovados com o resumo, curado automaticamente.
    candidatos = CandidatoDeFonte.objects.filter(pk__in=resultado["candidatos"])
    assert candidatos.count() == len(resultado["artigos"])
    assert all(c.situacao == CandidatoDeFonte.Situacao.APROVADO for c in candidatos)
    documentos = Document.objects.filter(pk__in=pesquisa.documentos_da_pesquisa(pauta))
    assert documentos.count() == len(resultado["artigos"])
    assert all(d.status == Document.Status.CURATED for d in documentos)

    pauta.refresh_from_db()
    # A orientacao da pauta nao muda (o fluxo A tambem a le); os angulos vao so ao B.
    assert pauta.briefing == "Base."
    assert "Contraponto" in pesquisa.orientacao_dos_angulos(pauta)
    assert pauta.busca_de_fontes["pesquisa"]["situacao"] == "pronta"

    # O fluxo da pesquisa usa so esses documentos.
    from apps.knowledge.referencias import trechos_da_pauta

    usados = {t.chunk.document_id for t in trechos_da_pauta(pauta, fluxo="pesquisa")}
    assert usados and usados <= set(documentos.values_list("pk", flat=True))
    assert pesquisa.pronta_para_gerar(pauta) == ""


def test_sem_modelo_uma_busca_so(ambiente, openalex):  # noqa: F811
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    assert len([c for c in openalex if "search.semantic" in c]) == 1
    assert resultado["hipoteses"]["do_modelo"] is False
    assert not any(a["contraponto"] for a in resultado["angulos"])


def test_tela_mostra_os_dois_fluxos_e_a_config_esconde(ambiente, openalex, modelo):  # noqa: F811
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    url = reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "A · Fontes curadas" in html and "B · Pesquisa cientifica" in html
    assert "Gerar A e B" in html

    pesquisa.pesquisar(pauta)
    html = client.get(url).content.decode()
    assert "Artigo com outra IA (B)" in html and "Contraponto" in html and "a amostra" in html

    config = ConfiguracaoDoRadar.carregar()
    config.fluxo_do_acervo = False
    config.save()
    html = client.get(url).content.decode()
    assert "A · Fontes curadas" not in html and "B · Pesquisa cientifica" in html


def test_pedir_o_pdf_sem_pdf_aberto(ambiente, openalex, modelo):  # noqa: F811
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    candidato = resultado["candidatos"][0]
    client.post(
        reverse("content:pdf_da_pesquisa", args=[pauta.pk, candidato], urlconf="core.urls_tenants")
    )
    assert (
        CandidatoDeFonte.objects.get(pk=candidato).situacao
        == CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    )


def test_outra_ia_no_fluxo_da_pesquisa_pede_e_le_os_pedidos(ambiente, openalex, modelo):  # noqa: F811
    from apps.content import outra_ia

    pauta = Topic.objects.create(title="Recuperar o cliente")
    pesquisa.pesquisar(pauta)
    artigo = outra_ia.preparar(pauta, "pesquisa")
    assert artigo.fluxo == "pesquisa"
    texto = outra_ia.pedido(artigo)
    assert "RESUMOS de artigos cientificos" in texto and "PEDIDOS:" in texto

    lido = outra_ia.ler(
        "TITULO SUGERIDO: x\nPEDIDOS:\n- fonte 2: a amostra do estudo\n"
        "CORPO DO ARTIGO:\nTexto.\nFIM DO ARTIGO"
    )
    assert lido["pedidos"] == ["fonte 2: a amostra do estudo"]


@pytest.fixture
def na_hora(monkeypatch):
    """Fila e on_commit na hora, para seguir a cadeia inteira."""
    from django.db import transaction

    from apps.content import tasks as tarefas_de_conteudo
    from apps.knowledge import tasks as tarefas_do_acervo

    monkeypatch.setattr(transaction, "on_commit", lambda funcao: funcao())
    monkeypatch.setattr(
        tarefas_do_acervo.pesquisar_pauta, "delay", lambda pk: tarefas_do_acervo.pesquisar_pauta(pk)
    )
    monkeypatch.setattr(
        tarefas_de_conteudo.gerar_b_quando_pronta,
        "delay",
        lambda pk: tarefas_de_conteudo.gerar_b_quando_pronta(pk),
    )
    monkeypatch.setattr(
        "apps.content.tasks.advance_generation_job.delay", lambda pk: None, raising=False
    )


def test_gerar_a_e_b_e_b_gera_sozinho_depois(ambiente, openalex, modelo, na_hora, monkeypatch):  # noqa: F811
    from apps.content import fluxos
    from apps.ops.models import GenerationJob

    monkeypatch.setattr("apps.knowledge.referencias.videos_antes_de_gerar", lambda p: 0)
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente depois de uma falha")
    gerar = reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants")

    client.post(gerar, {"fluxo": "ligados"})
    trabalhos = GenerationJob.objects.filter(target_object_id=str(pauta.pk))
    assert sorted(t.step_payloads.get("fluxo") for t in trabalhos) == ["", "pesquisa"]
    pauta.refresh_from_db()
    assert pauta.busca_de_fontes["pesquisa"]["situacao"] == "pronta"

    # Clicar de novo nao duplica nenhum dos dois.
    client.post(gerar, {"fluxo": "ligados"})
    assert trabalhos.count() == 2
    assert fluxos.em_andamento(pauta, fluxos.A) and fluxos.em_andamento(pauta, fluxos.B)


def test_aprovar_um_arquiva_o_outro(ambiente):  # noqa: F811
    from apps.content.fluxos import arquivar_o_outro, irmaos
    from apps.content.models import Article

    pauta = Topic.objects.create(title="Recuperar o cliente")
    a = Article.objects.create(topic=pauta, title="A", slug="a", status="pending_review")
    b = Article.objects.create(
        topic=pauta, title="B", slug="b", status="pending_review", fluxo="pesquisa"
    )
    assert irmaos(a) == [b]
    assert arquivar_o_outro(a) == 1
    b.refresh_from_db()
    assert b.status == Article.Status.REJECTED and b.thesis_json["arquivado_por"] == str(a.pk)

    _, _, client = ambiente
    html = client.get(
        reverse("content:revisar", args=[a.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "A · Fontes curadas" in html


PDF = """# Service recovery study

## Method

We surveyed a sample of 612 retail bank customers in the United Kingdom who had
experienced a service failure in the previous six months, using a structured
questionnaire administered online by an independent research agency.

## Results

Customers who received an apology and a fast solution reported loyalty scores
twenty percent higher than those who received only compensation, and the effect
held after controlling for the severity of the failure in every bank studied.

## References

Smith J. (1999). Some other paper about service quality and loyalty measures.
"""


def test_pdf_da_pesquisa_mantem_o_resumo_e_le_so_o_pedido(ambiente, openalex, modelo):  # noqa: F811
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.knowledge import academicos
    from apps.knowledge.models import DocumentCategory

    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    candidato = CandidatoDeFonte.objects.get(pk=resultado["candidatos"][0])
    resumo = candidato.documento
    assert pesquisa.em_pesquisa(candidato)

    categoria = DocumentCategory.objects.first() or DocumentCategory.objects.create(
        name="c", slug="c"
    )
    arquivo = SimpleUploadedFile("estudo.pdf", b"%PDF-1.4 x", content_type="application/pdf")
    documento = academicos.receber_pdf(candidato, arquivo, categoria=categoria)
    candidato.refresh_from_db()
    # O resumo continua sendo a fonte; o PDF fica a parte.
    assert candidato.documento == resumo and candidato.documento_completo == documento

    # A conversao terminou: so os trechos pedidos entram no resumo.
    documento.markdown_full = PDF
    documento.save()
    assert pesquisa.extrair_do_pdf(documento) >= 1
    resumo.refresh_from_db()
    assert "Do texto completo" in resumo.markdown_full and "612" in resumo.markdown_full
    assert "Smith J." not in resumo.markdown_full  # referencias ficam de fora
    pauta.refresh_from_db()
    pedidos = [
        p for a in pauta.busca_de_fontes["pesquisa"]["angulos"] for p in a.get("pedidos", [])
    ]
    assert all(p.get("atendido") == "pdf" for p in pedidos)


def test_nao_achei_o_pdf_segue_com_o_resumo(ambiente, openalex, modelo):  # noqa: F811
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    candidato = CandidatoDeFonte.objects.get(pk=resultado["candidatos"][0])
    candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    candidato.save()
    client.post(
        reverse("knowledge:seguir_com_o_resumo", args=[candidato.pk], urlconf="core.urls_tenants")
    )
    candidato.refresh_from_db()
    assert candidato.situacao == CandidatoDeFonte.Situacao.APROVADO
    assert candidato.documento.extraction_method == Document.ExtractionMethod.RESUMO
    pauta.refresh_from_db()
    pedidos = [
        p for a in pauta.busca_de_fontes["pesquisa"]["angulos"] for p in a.get("pedidos", [])
    ]
    assert all(p.get("atendido") == "sem_pdf" for p in pedidos if p["artigo"] == 1)


def test_pesquisa_parada_recomeca_e_a_tela_avisa(
    ambiente,  # noqa: F811
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """O servidor reiniciou no meio da pesquisa: a varredura recomeca, mantendo
    o pedido de gerar ao terminar."""
    import datetime

    from django.utils import timezone

    from apps.content import fluxos

    despachadas = []
    monkeypatch.setattr(
        "apps.knowledge.tasks.pesquisar_pauta.delay", lambda pk: despachadas.append(pk)
    )
    antiga = (timezone.now() - datetime.timedelta(hours=2)).isoformat()
    parada = Topic.objects.create(
        title="Parada",
        busca_de_fontes={
            "pesquisa": {"situacao": "pesquisando", "em": antiga, "gerar_depois": True}
        },
    )
    Topic.objects.create(
        title="Recente",
        busca_de_fontes={"pesquisa": {"situacao": "pesquisando", "em": timezone.now().isoformat()}},
    )

    _, _, client = ambiente
    url = reverse("content:pauta", args=[parada.pk], urlconf="core.urls_tenants")
    assert "pesquisa parada" in client.get(url).content.decode()

    with django_capture_on_commit_callbacks(execute=True):
        assert fluxos.pesquisas_paradas() == 1
    assert despachadas == [str(parada.pk)]
    parada.refresh_from_db()
    assert parada.busca_de_fontes["pesquisa"]["situacao"] == "na_fila"
    assert parada.busca_de_fontes["pesquisa"]["gerar_depois"] is True


def test_geracao_esperando_a_maquina_aparece_e_pode_ser_encerrada(ambiente):  # noqa: F811
    from apps.content import fluxos
    from apps.ops.models import GenerationJob

    pauta = Topic.objects.create(title="Esperando a placa", status="approved")
    trabalho = GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=pauta.pk,
        status=GenerationJob.Status.WAITING_CAPACITY,
        step_payloads={"fluxo": fluxos.B},
        last_error="maquina desligada",
        total_steps=8,
    )
    _, _, client = ambiente
    url = reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "esperando a maquina do modelo" in html and "maquina desligada" in html
    assert "Gerar so B" not in html
    lista = client.get(reverse("content:pautas", urlconf="core.urls_tenants")).content.decode()
    assert "esperando a maquina do modelo" in lista

    resposta = client.post(
        reverse(
            "content:desistir_da_geracao",
            args=[pauta.pk, trabalho.pk],
            urlconf="core.urls_tenants",
        )
    )
    assert resposta.status_code == 302 and resposta.url.endswith(f"/pautas/{pauta.pk}/")
    trabalho.refresh_from_db()
    assert trabalho.status == GenerationJob.Status.FAILED
    assert "Gerar so B" in client.get(url).content.decode()


def test_pagina_dos_pdfs_mostra_o_pedido_e_recebe_o_arquivo(ambiente, openalex, modelo):  # noqa: F811
    from django.core.files.uploadedfile import SimpleUploadedFile

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    itens = pesquisa.pdfs_pedidos(pauta)
    assert itens and itens[0]["estado"] == "pendente" and itens[0]["pedidos"]

    pagina_da_pauta = client.get(
        reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Conferir o" in pagina_da_pauta and "Nao achei o PDF" not in pagina_da_pauta

    url = reverse("content:pdfs_da_pesquisa", args=[pauta.pk], urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "Enviar o PDF" in html and itens[0]["pedidos"][0]["o_que"] in html

    candidato = itens[0]["candidato"]
    resposta = client.post(
        reverse(
            "content:enviar_pdf_da_pesquisa",
            args=[pauta.pk, candidato.pk],
            urlconf="core.urls_tenants",
        ),
        {"pdf": SimpleUploadedFile("estudo.pdf", b"%PDF-1.4 x", content_type="application/pdf")},
    )
    assert resposta.status_code == 302 and resposta.url.endswith("/pdfs/")
    candidato.refresh_from_db()
    assert candidato.documento_completo is not None
    assert pesquisa.pdfs_pedidos(pauta)[0]["estado"] == "lendo"
    assert str(candidato.pk) in resultado["candidatos"]


def test_fontes_sugeridas_da_pauta_avisa_e_deixa_de_fora_a_pesquisa(ambiente, openalex, modelo):  # noqa: F811
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    da_pesquisa = CandidatoDeFonte.objects.get(pk=resultado["candidatos"][0])
    da_pesquisa.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    da_pesquisa.save()

    url = reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants")
    html = client.get(f"{url}?pauta={pauta.pk}").content.decode()
    assert "Mostrando so as fontes da pauta" in html and "Voltar para a pauta" in html
    assert da_pesquisa.titulo not in html
    assert da_pesquisa.titulo in client.get(url).content.decode()


def test_rascunho_que_a_geracao_largou_pode_ser_descartado(ambiente):  # noqa: F811
    from apps.content import fluxos
    from apps.content.models import Article
    from apps.ops.models import GenerationJob

    pauta = Topic.objects.create(title="Falhou no consenso", status="used")
    GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=pauta.pk,
        status=GenerationJob.Status.FAILED,
        step_payloads={"fluxo": fluxos.B},
        current_step=1,
        last_error="passo 1 (filtrar consenso): " + "detalhe " * 40,
    )
    rascunho = Article.objects.create(
        topic=pauta, title="x", fluxo=fluxos.B, status=Article.Status.DRAFTING
    )
    _, _, client = ambiente
    url = reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "a ultima geracao falhou" in html and "Descartar e gerar de novo" in html
    assert "Gerar so B" not in html

    client.post(
        reverse("content:descartar_rascunho", args=[rascunho.pk], urlconf="core.urls_tenants")
    )
    rascunho.refresh_from_db()
    assert rascunho.status == Article.Status.REJECTED
    assert "Gerar so B" in client.get(url).content.decode()
