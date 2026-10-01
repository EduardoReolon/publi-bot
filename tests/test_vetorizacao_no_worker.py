"""Vetorizar no worker da placa, com o servidor como alternativa.

A consulta continua no servidor. So a indexacao (passagens) vai ao worker, e
quando ele pede para esperar o trabalho volta para a fila em vez de falhar.
"""

from __future__ import annotations

import httpx
import pytest
from django.conf import settings
from django.urls import reverse

from apps.knowledge import embeddings
from apps.knowledge.embeddings import VetorizacaoAdiada, vetorizar_passagens
from tests.test_interface import _documento_curado, ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """O caminho local usa o cliente falso."""


def _worker():
    from apps.inference.models import InferenceConnection
    from apps.inference.security import guardar_chave

    conexao = InferenceConnection(
        name="Worker",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://worker:8090",
        is_active=True,
        workloads=["vision_parse", "embedding"],
    )
    guardar_chave(conexao, "segredo")
    conexao.save()
    return conexao


def _responder(monkeypatch, status: int, corpo: dict | None = None, pedidos=None):
    def post(url, **kwargs):
        if pedidos is not None:
            pedidos.append((url, kwargs))
        return httpx.Response(
            status,
            json=corpo or {},
            headers={"Retry-After": "120"} if status == 503 else {},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)


def _vetores(n):
    return {
        "model": settings.EMBEDDING_MODEL,
        "data": [
            {"index": i, "embedding": [1.0] + [0.0] * (settings.EMBEDDING_DIM - 1)}
            for i in range(n)
        ],
    }


@pytest.mark.django_db
def test_sem_worker_vetoriza_no_servidor(public_tenant):
    assert len(vetorizar_passagens(["um", "dois"], permitir_local=False)) == 2


@pytest.mark.django_db
def test_com_worker_manda_com_o_prefixo_e_normaliza(public_tenant, monkeypatch):
    _worker()
    pedidos = []
    _responder(monkeypatch, 200, _vetores(2), pedidos)
    vetores = vetorizar_passagens(["um", "dois"], permitir_local=False)
    url, kwargs = pedidos[0]
    assert url == "http://worker:8090/v1/embeddings"
    assert kwargs["json"]["input"] == ["passage: um", "passage: dois"]
    assert kwargs["headers"]["Authorization"] == "Bearer segredo"
    assert len(vetores) == 2 and abs(sum(x * x for x in vetores[0]) - 1) < 1e-6


@pytest.mark.django_db
def test_worker_carregando_o_modelo_adia_sem_falhar(public_tenant, monkeypatch):
    _worker()
    _responder(monkeypatch, 503, {"error": {"code": "modelo_carregando"}})
    with pytest.raises(VetorizacaoAdiada) as erro:
        vetorizar_passagens(["um"], permitir_local=False)
    assert erro.value.retry_after == 120
    # Com permissao, cai no servidor.
    assert len(vetorizar_passagens(["um"], permitir_local=True)) == 1


@pytest.mark.django_db
def test_worker_com_outro_modelo_e_recusado(public_tenant, monkeypatch):
    _worker()
    _responder(monkeypatch, 200, {**_vetores(1), "model": "outro-modelo"})
    with pytest.raises(RuntimeError, match="outro-modelo"):
        vetorizar_passagens(["um"], permitir_local=False)


def test_curadoria_espera_o_worker_e_oferece_o_servidor(ambiente, monkeypatch):  # noqa: F811
    from apps.knowledge import tasks
    from apps.knowledge.models import Document

    _, _, client = ambiente
    _worker()
    _responder(monkeypatch, 503, {"error": {"code": "gpu_ocupada"}})
    reagendados = []
    monkeypatch.setattr(
        tasks.indexar_documento,
        "apply_async",
        lambda args, countdown: reagendados.append(countdown),
    )
    documento = _documento_curado()
    Document.objects.filter(pk=documento.pk).update(
        status=Document.Status.PENDING_CURATION,
        markdown_full="# Estudo\n\n## Resultados\n\n" + "O efeito observado foi grande. " * 30,
    )
    documento.refresh_from_db()
    from apps.knowledge.blocos import preparar_blocos

    bloco = next(b.ordem for b in preparar_blocos(documento) if b.paragrafos)
    antes = documento.chunks.count()

    tasks.pedir_indexacao(documento, blocos={bloco}, concluir=True, por=None)
    documento.refresh_from_db()
    # Nada apagado, pedido de pe, e de volta para a fila.
    assert documento.indexacao_pedida["aguardando_worker"] and reagendados == [120]
    assert documento.chunks.count() == antes

    url = reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants")
    assert "Vetorizar agora no servidor" in client.get(url).content.decode()

    client.post(
        reverse("knowledge:vetorizar_no_servidor", args=[documento.pk], urlconf="core.urls_tenants")
    )
    documento.refresh_from_db()
    assert documento.indexacao_pedida == {} and documento.status == Document.Status.CURATED
    assert embeddings.conexao_de_vetorizacao() is not None


@pytest.mark.django_db
def test_legenda_pelo_worker_e_sem_legenda(public_tenant, monkeypatch):
    from apps.inference.models import InferenceConnection
    from apps.knowledge.videos import LegendaIndisponivel, buscar_legenda

    conexao = _worker()
    conexao.workloads = [InferenceConnection.Workload.YOUTUBE]
    conexao.save()
    pedidos = []
    _responder(
        monkeypatch,
        200,
        {"idioma": "pt", "segmentos": [{"start": 1.5, "duration": 2, "text": "Ola."}]},
        pedidos,
    )
    assert buscar_legenda("abc") == [(1.5, "Ola.")]
    assert pedidos[0][0] == "http://worker:8090/v1/youtube/legenda"

    _responder(monkeypatch, 404, {"error": {"code": "sem_legenda"}})
    with pytest.raises(LegendaIndisponivel):
        buscar_legenda("abc")
