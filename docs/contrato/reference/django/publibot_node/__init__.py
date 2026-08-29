"""Implementacao de referencia do contrato PubliBot /api/v1 para Django."""

VERSAO = "1.0.0"
VERSOES_DO_CONTRATO = ["v1"]

RECURSOS = [
    "idempotency",
    "hmac_signature",
    "cursor_pagination",
    "image_by_url",
    "author_photo",
    "qa",
    "reconciliation",
]
