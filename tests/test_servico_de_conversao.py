"""O servico de conversao do `worker-gpu`, pelo HTTP.

Ele nao tinha teste nenhum ate aqui — e foi alterado sem que ninguem pudesse
executa-lo, quando os handlers sairam do event loop. Um servico que recebe
credencial, confere digest e decide 503 merece mais que revisao de leitura.

O Docling em si nao entra: `_obter_conversor` e substituido. O que sobra e o
contrato HTTP, que e onde os erros deste arquivo doem — do outro lado esta a
VM da nuvem, e uma falha aqui aparece como PDF que "nao converteu".
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi so entra por requirements-dev.txt")
pytest.importorskip("multipart", reason="python-multipart so entra por requirements-dev.txt")

from fastapi.testclient import TestClient

SEGREDO = "segredo-de-teste"
PDF = b"%PDF-1.4 fingindo ser um artigo"


class ConversorFalso:
    """Devolve Markdown fixo, com a mesma forma do objeto do Docling."""

    def __init__(self, demora: float = 0.0):
        self.demora = demora

    def convert(self, caminho):
        import time

        if self.demora:
            time.sleep(self.demora)

        class Documento:
            @staticmethod
            def export_to_markdown():
                return "# Titulo\n\nUm paragrafo."

        class Resultado:
            document = Documento()

        return Resultado()


@pytest.fixture
def servico(monkeypatch):
    monkeypatch.setenv("WORKER_SHARED_SECRET", SEGREDO)
    monkeypatch.setenv("DOCLING_DEVICE", "cpu")
    monkeypatch.setenv("DOCLING_OCR", "false")

    raiz = str(Path(__file__).resolve().parent.parent / "worker-gpu")
    if raiz not in sys.path:
        sys.path.insert(0, raiz)

    modulo = importlib.reload(importlib.import_module("docling_api"))
    monkeypatch.setattr(modulo, "_obter_conversor", ConversorFalso)
    return modulo


@pytest.fixture
def cliente(servico):
    return TestClient(servico.app)


def _enviar(cliente, *, segredo: str | None = SEGREDO, digest: str | None = None, conteudo=PDF):
    cabecalhos = {}
    if segredo is not None:
        cabecalhos["X-Worker-Secret"] = segredo
    if digest is not None:
        cabecalhos["X-Expected-Sha256"] = digest
    return cliente.post(
        "/parse/", files={"file": ("artigo.pdf", conteudo, "application/pdf")}, headers=cabecalhos
    )


def test_converte_e_devolve_markdown(cliente):
    resposta = _enviar(cliente)

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert corpo["markdown"].startswith("# Titulo")
    assert corpo["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert corpo["bytes"] == len(PDF)


def test_sem_credencial_recusa(cliente):
    assert _enviar(cliente, segredo=None).status_code == 401


def test_credencial_errada_recusa(cliente):
    assert _enviar(cliente, segredo="outro").status_code == 401


def test_digest_divergente_e_recusado(cliente):
    """Um arquivo truncado no caminho converteria em silencio, e o Markdown de
    um documento que ninguem pediu entraria no acervo."""
    resposta = _enviar(cliente, digest="0" * 64)

    assert resposta.status_code == 422


def test_digest_conferido_passa(cliente):
    resposta = _enviar(cliente, digest=hashlib.sha256(PDF).hexdigest())

    assert resposta.status_code == 200


def test_arquivo_grande_demais_e_recusado(servico, monkeypatch):
    monkeypatch.setattr(servico, "TAMANHO_MAXIMO", 10)

    assert _enviar(TestClient(servico.app)).status_code == 413


def test_a_segunda_conversao_recebe_503(servico):
    """O PubliBot ja tem fila e sabe tentar de novo; uma segunda fila aqui
    dentro seria invisivel para ele."""
    cliente = TestClient(servico.app)
    servico._uma_por_vez.acquire()

    try:
        resposta = _enviar(cliente)
    finally:
        servico._uma_por_vez.release()

    assert resposta.status_code == 503
    assert resposta.headers["Retry-After"] == "60"


def test_health_responde_durante_uma_conversao(servico, monkeypatch):
    """O mesmo defeito que travou o servico de imagem, guardado aqui tambem.

    Um handler `async def` roda no event loop; uma conversao de dezenas de
    segundos dentro dele congela o processo, e o `configurar_conversao
    --testar` passa a dar "timed out" exatamente quando se precisa dele.

    O `with` importa: so dentro dele o TestClient usa UM event loop para todas
    as requisicoes, como o uvicorn faz.
    """
    import threading
    import time

    comecou = threading.Event()

    class Lenta(ConversorFalso):
        def convert(self, caminho):
            comecou.set()
            return super().convert(caminho)

    monkeypatch.setattr(servico, "_obter_conversor", lambda: Lenta(demora=2.0))

    with TestClient(servico.app) as cliente:
        conversao = threading.Thread(target=lambda: _enviar(cliente), daemon=True)
        conversao.start()
        assert comecou.wait(timeout=10), "a conversao nem comecou"

        inicio = time.perf_counter()
        resposta = cliente.get("/health/")
        decorrido = time.perf_counter() - inicio

        conversao.join(timeout=20)

    assert resposta.status_code == 200
    assert decorrido < 1.5, f"/health/ esperou a conversao terminar ({decorrido:.1f}s)"
    assert resposta.json()["busy"] is True


def test_health_diz_o_dispositivo(cliente):
    """O `configurar_conversao --testar` le isto para avisar quando o worker
    caiu para CPU sem ninguem perceber."""
    corpo = cliente.get("/health/").json()

    assert corpo["device"] == "cpu"
    assert corpo["ocr"] is False
