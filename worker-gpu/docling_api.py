"""Servico HTTP que converte PDF em Markdown.

Deliberadamente minimo: sem banco, sem fila, sem estado. Recebe um PDF,
devolve Markdown. Quem decide o que converter, quando e em que ordem e o
PubliBot — este servico so executa.

Roda uma conversao por vez. Numa placa de 8 GB, duas simultaneas estouram a
VRAM e o processamento cai para CPU sem emitir erro.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(asctime)s %(message)s")
logger = logging.getLogger("docling-api")

SEGREDO = os.environ.get("WORKER_SHARED_SECRET", "")
TAMANHO_MAXIMO = int(os.environ.get("MAX_PDF_BYTES", 100 * 1024 * 1024))

app = FastAPI(title="PubliBot — servico Docling", version="1.0.0")

# Uma conversao por vez. Nao e ajuste de desempenho: duas simultaneas estouram
# a VRAM e o resultado e 15x mais lento, sem erro que denuncie.
_uma_por_vez = threading.Semaphore(1)

_conversor = None
_trava_do_conversor = threading.Lock()


def _obter_conversor():
    """Carga preguicosa: o modelo leva dezenas de segundos para subir."""
    global _conversor
    if _conversor is None:
        with _trava_do_conversor:
            if _conversor is None:
                from docling.document_converter import DocumentConverter

                logger.info("Carregando o Docling...")
                _conversor = DocumentConverter()
                logger.info("Docling pronto.")
    return _conversor


def _conferir_segredo(recebido: str | None) -> None:
    """Compara em tempo constante.

    A escrita natural (`!=`) e curto-circuitada byte a byte: o tempo de
    resposta revela quantos bytes iniciais estao corretos.
    """
    if not SEGREDO:
        raise HTTPException(500, "WORKER_SHARED_SECRET nao configurado.")
    if not recebido or not hmac.compare_digest(recebido, SEGREDO):
        raise HTTPException(401, "Credencial invalida.")


@app.get("/health/")
async def health():
    return {
        "status": "ok",
        "service": "docling-api",
        "busy": not _uma_por_vez._value,
    }


@app.post("/parse/")
async def parse(
    request: Request,
    file: UploadFile,
    x_worker_secret: str | None = Header(default=None),
    x_expected_sha256: str | None = Header(default=None),
):
    """Converte um PDF em Markdown.

    Recusa imediatamente quando ja ha uma conversao em curso, em vez de
    enfileirar: o PubliBot ja controla a fila e sabe tentar de novo. Enfileirar
    aqui criaria uma segunda fila invisivel para ele.
    """
    _conferir_segredo(x_worker_secret)

    conteudo = await file.read()

    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(413, f"Arquivo excede {TAMANHO_MAXIMO} bytes.")

    digest = hashlib.sha256(conteudo).hexdigest()
    if x_expected_sha256 and not hmac.compare_digest(digest, x_expected_sha256):
        # O arquivo chegou corrompido ou nao e o esperado. Converter assim
        # produziria Markdown de um documento que ninguem pediu.
        raise HTTPException(422, "sha256 nao confere com o esperado.")

    if not _uma_por_vez.acquire(blocking=False):
        return JSONResponse(
            {"error": {"code": "busy", "message": "Ja ha uma conversao em curso."}},
            status_code=503,
            headers={"Retry-After": "60"},
        )

    inicio = time.perf_counter()
    try:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / (file.filename or "documento.pdf")
            caminho.write_bytes(conteudo)

            resultado = _obter_conversor().convert(str(caminho))
            markdown = resultado.document.export_to_markdown()

        duracao = int((time.perf_counter() - inicio) * 1000)
        logger.info("Convertido %s em %sms (%s bytes)", file.filename, duracao, len(conteudo))

        return {
            "markdown": markdown,
            "sha256": digest,
            "bytes": len(conteudo),
            "duration_ms": duracao,
        }
    except Exception as exc:
        logger.exception("Falha ao converter %s", file.filename)
        raise HTTPException(500, f"Falha na conversao: {exc}") from exc
    finally:
        _uma_por_vez.release()
