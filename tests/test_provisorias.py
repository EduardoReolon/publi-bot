"""Fontes provisorias: no indice antes da curadoria, curadas quando usadas."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.content.models import Topic
from apps.knowledge import provisorias
from apps.knowledge.models import CandidatoDeFonte, Document
from tests.test_interface import ambiente  # noqa: F401
from tests.test_vetorizacao_no_worker import _worker

RESUMO = (
    "Customer satisfaction after service failure depends on the recovery. "
    "This study measures how recovery performance changes satisfaction. " * 4
)


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar carregaria o modelo real."""


@pytest.fixture
def com_worker(monkeypatch):
    """Worker que vetoriza: os vetores saem do cliente falso, sem rede."""
    from apps.knowledge import embeddings

    _worker()
    monkeypatch.setattr(
        embeddings,
        "_no_worker",
        lambda conexao, textos, dono: embeddings.get_embedding_client().embed_passage(textos),
    )


def _artigo(n=1, **extra):
    return CandidatoDeFonte.objects.create(
        url=f"https://doi.org/10.1/{n}",
        tipo=CandidatoDeFonte.Tipo.ARTIGO,
        titulo=f"Service recovery study {n}",
        trecho=RESUMO,
        **extra,
    )


def test_sem_worker_nada_muda(ambiente):  # noqa: F811
    assert not provisorias.ligado()
    candidato = _artigo()
    provisorias.acolher_se_ligado(candidato)
    assert candidato.documento is None


def test_resumo_do_artigo_entra_no_indice_sem_curadoria(ambiente, com_worker):  # noqa: F811
    candidato = _artigo()
    assert provisorias.acolher(candidato)
    documento = candidato.documento
    assert documento.extraction_method == Document.ExtractionMethod.RESUMO
    assert documento.status == Document.Status.PENDING_CURATION
    assert documento.chunks.exists() and not documento.indexacao_pedida

    # Chegou o PDF: o resumo sai.
    from apps.knowledge.provisorias import descartar_resumo

    descartar_resumo(candidato)
    assert not Document.objects.filter(pk=documento.pk).exists()


def test_geracao_para_e_lista_o_que_curar(ambiente, com_worker):  # noqa: F811
    from apps.content.services import FontesPorCurar, fontes_da_pauta
    from apps.knowledge.services import marcar_curado

    candidato = _artigo()
    provisorias.acolher(candidato)
    documento = candidato.documento
    pauta = Topic.objects.create(title=RESUMO[:120], target_keyword="service recovery")

    with pytest.raises(FontesPorCurar) as erro:
        fontes_da_pauta(pauta)
    assert documento in erro.value.documentos
    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.WAITING_SOURCES
    assert pauta.busca_de_fontes["acervo"]["por_curar"][0]["id"] == str(documento.pk)

    _, _, client = ambiente
    html = client.get(reverse("content:pautas", urlconf="core.urls_tenants")).content.decode()
    assert "Para gerar, cure estas fontes" in html

    marcar_curado(document=documento, revisado_por=None)
    from apps.knowledge.referencias import conferir_as_que_esperam

    assert conferir_as_que_esperam() == 1
    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.APPROVED
    assert fontes_da_pauta(pauta)


def test_recusar_a_fonte_tira_do_indice(ambiente, com_worker):  # noqa: F811
    _, _, client = ambiente
    candidato = _artigo()
    provisorias.acolher(candidato)
    documento = candidato.documento
    client.post(
        reverse("knowledge:recusar_fonte", args=[documento.pk], urlconf="core.urls_tenants")
    )
    documento.refresh_from_db()
    candidato.refresh_from_db()
    assert documento.status == Document.Status.REJECTED and not documento.chunks.exists()
    assert candidato.situacao == CandidatoDeFonte.Situacao.RECUSADO


def test_espaco_e_apagar_os_antigos(ambiente, com_worker):  # noqa: F811
    import datetime

    from django.utils import timezone

    _, _, client = ambiente
    candidato = _artigo()
    provisorias.acolher(candidato)
    Document.objects.filter(pk=candidato.documento.pk).update(
        created_at=timezone.now() - datetime.timedelta(days=200)
    )
    dados = provisorias.espaco(3)
    assert dados["nao_curados"] >= 1 and dados["antigos"] == 1 and dados["bytes_antigos"] > 0

    lista = reverse("knowledge:documentos", urlconf="core.urls_tenants")
    html = client.get(lista + "?meses=3").content.decode()
    assert "nao curado" in html and "Apagar" in html
    client.post(
        reverse("knowledge:apagar_nao_curados", urlconf="core.urls_tenants"),
        {"meses": 3, "confirmo": "1"},
    )
    assert not Document.objects.filter(pk=candidato.documento.pk).exists()


def test_comando_aplica_ao_que_ja_existe(ambiente, com_worker, capsys):  # noqa: F811
    aprovado_sem_pdf = _artigo(2, situacao=CandidatoDeFonte.Situacao.AGUARDANDO_PDF)
    call_command("vetorizar_fontes_provisorias", seco=True)
    saida = capsys.readouterr().out
    assert "Artigos sem PDF, com resumo: 1" in saida and "ja na fila" in saida
    call_command("vetorizar_fontes_provisorias")
    aprovado_sem_pdf.refresh_from_db()
    assert aprovado_sem_pdf.documento.chunks.exists()
