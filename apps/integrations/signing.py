"""Assinatura das requisicoes enviadas aos sites.

Um token fixo no cabecalho prova apenas que quem chama conhece o token. Nao
prova que o corpo chegou intacto, nem que a requisicao e recente. Duas
consequencias praticas:

* Uma requisicao capturada e **reexecutavel indefinidamente**. Uma gravacao de
  hoje continua valida daqui a um ano.
* Um intermediario que altere o corpo troca o conteudo publicado sem que nada
  detecte.

A assinatura resolve as duas coisas: cobre o corpo (integridade) e inclui um
instante e um valor unico (frescor).
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import httpx

# Tolerancia de relogio entre os dois lados.
JANELA_DE_TEMPO_SEGUNDOS = 300

VERSAO_DA_ASSINATURA = "v1"


def montar_base_da_assinatura(timestamp: str, nonce: str, corpo: bytes) -> str:
    """Monta a string assinada.

    O corpo entra como digest do conteudo BRUTO, antes de qualquer
    interpretacao. Assinar o corpo ja processado permitiria que diferencas de
    serializacao entre os dois lados produzissem assinaturas diferentes para o
    mesmo conteudo.
    """
    digest_do_corpo = hashlib.sha256(corpo).hexdigest()
    return f"{timestamp}.{nonce}.{digest_do_corpo}"


def assinar(segredo: str, timestamp: str, nonce: str, corpo: bytes) -> str:
    base = montar_base_da_assinatura(timestamp, nonce, corpo)
    assinatura = hmac.new(segredo.encode(), base.encode(), hashlib.sha256).hexdigest()
    return f"{VERSAO_DA_ASSINATURA}={assinatura}"


def conferir(
    segredo: str,
    timestamp: str,
    nonce: str,
    corpo: bytes,
    assinatura_recebida: str,
    *,
    agora: float | None = None,
) -> bool:
    """Confere a assinatura e o frescor.

    A comparacao usa `compare_digest`. A escrita natural (`a != b`) e
    curto-circuitada byte a byte: o tempo de resposta revela quantos bytes
    iniciais estao corretos, e sem limite de tentativas a extracao byte a byte
    do valor esperado e viavel.
    """
    agora = agora if agora is not None else time.time()

    try:
        if abs(agora - float(timestamp)) > JANELA_DE_TEMPO_SEGUNDOS:
            return False
    except (TypeError, ValueError):
        return False

    esperada = assinar(segredo, timestamp, nonce, corpo)
    return hmac.compare_digest(esperada, assinatura_recebida or "")


def impressao_da_chave(chave: str) -> str:
    """SHA-256 da chave, para localizar o registro sem decifrar nada."""
    return hashlib.sha256(chave.encode()).hexdigest()


class AssinaturaHttpx(httpx.Auth):
    """Assina automaticamente toda requisicao de um cliente httpx.

    Implementado como `httpx.Auth`, e nao como funcao chamada em cada lugar,
    justamente para que nao exista requisicao esquecida sem assinatura.
    """

    requires_request_body = True

    def __init__(self, *, api_key: str, signing_secret: str):
        self.api_key = api_key
        self.signing_secret = signing_secret

    def auth_flow(self, request: httpx.Request):
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        corpo = request.content or b""

        request.headers["X-API-KEY"] = self.api_key
        request.headers["X-Timestamp"] = timestamp
        request.headers["X-Nonce"] = nonce
        request.headers["X-Signature"] = assinar(self.signing_secret, timestamp, nonce, corpo)
        yield request
