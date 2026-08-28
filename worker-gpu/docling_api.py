"""Servico HTTP que converte PDF em Markdown.

Deliberadamente minimo: sem banco, sem fila, sem estado. Recebe um PDF,
devolve Markdown. Quem decide o que converter, quando e em que ordem e o
PubliBot — este servico so executa.

Roda uma conversao por vez. Numa placa de 8 GB, duas simultaneas estouram a
VRAM e o processamento cai para CPU sem emitir erro.

**Roda em CPU tambem**, e essa e a forma de comecar sem placa nenhuma. A analise
de layout — que e o que distingue este caminho do extrator local — nao depende de
GPU; a GPU muda o tempo, nao o resultado. Trocar depois e uma linha no `.env`:

    DOCLING_DEVICE=cpu    # comeco, sem placa
    DOCLING_DEVICE=cuda   # quando a placa existir
    DOCLING_DEVICE=auto   # usa a placa se houver

Nada muda no PubliBot: ele fala HTTP com este servico e nao sabe onde o modelo
roda. Por isso vale subir em CPU agora — o caminho ja fica montado, e a troca
para GPU nao mexe em codigo nem em fila.
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

# cpu | cuda | auto. Ver o cabecalho do modulo.
DISPOSITIVO = os.environ.get("DOCLING_DEVICE", "auto").lower()

# Numero de threads na CPU. Ignorado em GPU. Zero deixa o Docling decidir.
THREADS = int(os.environ.get("DOCLING_THREADS", 0))

# OCR le a imagem da pagina, para PDF digitalizado que nao tem camada de texto.
# E de longe a parte mais cara em CPU, e a maioria dos artigos cientificos nao
# precisa dela. Ligue quando encontrar um PDF escaneado.
OCR = os.environ.get("DOCLING_OCR", "false").lower() in {"1", "true", "yes", "sim"}

app = FastAPI(title="PubliBot — servico Docling", version="1.0.0")

# Uma conversao por vez. Nao e ajuste de desempenho: duas simultaneas estouram
# a VRAM e o resultado e 15x mais lento, sem erro que denuncie.
_uma_por_vez = threading.Semaphore(1)

_conversor = None
_trava_do_conversor = threading.Lock()


def _montar_conversor():
    """O conversor com o dispositivo e o OCR que o ambiente pediu."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    dispositivos = {
        "cpu": AcceleratorDevice.CPU,
        "cuda": AcceleratorDevice.CUDA,
        "auto": AcceleratorDevice.AUTO,
    }
    if DISPOSITIVO not in dispositivos:
        raise RuntimeError(f"DOCLING_DEVICE={DISPOSITIVO!r} nao existe. Use cpu, cuda ou auto.")

    acelerador = AcceleratorOptions(device=dispositivos[DISPOSITIVO])
    if THREADS:
        acelerador.num_threads = THREADS

    opcoes = PdfPipelineOptions()
    opcoes.accelerator_options = acelerador
    opcoes.do_ocr = OCR
    opcoes.do_table_structure = True

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opcoes)}
    )


def _obter_conversor():
    """Carga preguicosa: o modelo leva dezenas de segundos para subir."""
    global _conversor
    if _conversor is None:
        with _trava_do_conversor:
            if _conversor is None:
                logger.info(
                    "Carregando o Docling (dispositivo=%s, ocr=%s, threads=%s)...",
                    DISPOSITIVO,
                    OCR,
                    THREADS or "auto",
                )
                _conversor = _montar_conversor()
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
        # Util para conferir de fora se o servico esta mesmo na GPU depois de
        # trocar o .env — sem isso a troca falha em silencio e so aparece como
        # "esta demorando muito".
        "device": DISPOSITIVO,
        "ocr": OCR,
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
