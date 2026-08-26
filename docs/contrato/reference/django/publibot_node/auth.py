"""Verificacao da assinatura das requisicoes recebidas.

Cada decisao aqui corresponde a uma regra normativa do contrato. Os comentarios
explicam o porque, porque quem copiar este arquivo precisa entender o que nao
pode simplificar.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

JANELA_DE_TEMPO_SEGUNDOS = 300
RETENCAO_DE_NONCE_SEGUNDOS = 600

# Resposta unica para chave ausente, malformada ou incorreta. Mensagens
# diferentes vazariam a mesma informacao por outro canal — quem tenta descobre
# se a chave existe pelo texto do erro.
RESPOSTA_DE_NEGACAO = {
    "error": {
        "code": "invalid_api_key",
        "message": "Credencial invalida.",
        "details": {},
    }
}


def negar() -> JsonResponse:
    return JsonResponse(RESPOSTA_DE_NEGACAO, status=401)


def _chaves_aceitas() -> list[str]:
    """A chave atual e, durante uma rotacao, a anterior.

    Sem a janela dupla, rotacionar exigiria trocar os dois lados no mesmo
    instante — o que na pratica significa uma janela de indisponibilidade.
    """
    chaves = [getattr(settings, "PUBLIBOT_API_KEY", "")]
    anterior = getattr(settings, "PUBLIBOT_API_KEY_PREVIOUS", "")
    if anterior:
        chaves.append(anterior)
    return [c for c in chaves if c]


def conferir_assinatura(request) -> JsonResponse | None:
    """Devolve None quando a requisicao e valida, ou a resposta de negacao."""
    recebida = request.headers.get("X-API-KEY", "")
    timestamp = request.headers.get("X-Timestamp", "")
    nonce = request.headers.get("X-Nonce", "")
    assinatura = request.headers.get("X-Signature", "")

    # `compare_digest` sobre cada candidata. A escrita natural
    # (`if recebida not in chaves`) e curto-circuitada byte a byte: o tempo de
    # resposta revela quantos bytes iniciais estao corretos.
    if not any(hmac.compare_digest(recebida, valida) for valida in _chaves_aceitas()):
        return negar()

    if not _conferir_frescor(timestamp, nonce):
        return negar()

    segredo = getattr(settings, "PUBLIBOT_SIGNING_SECRET", "")
    if not segredo:
        return negar()

    # `request.body` e o corpo BRUTO, antes de qualquer interpretacao.
    # Reserializar o JSON antes de conferir produziria digests diferentes para
    # o mesmo conteudo, e a assinatura falharia de forma aparentemente
    # aleatoria.
    digest_do_corpo = hashlib.sha256(request.body or b"").hexdigest()
    base = f"{timestamp}.{nonce}.{digest_do_corpo}"
    esperada = "v1=" + hmac.new(segredo.encode(), base.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(esperada, assinatura):
        return negar()

    return None


def _conferir_frescor(timestamp: str, nonce: str) -> bool:
    """Instante dentro da janela, e nonce nunca visto antes."""
    try:
        idade = abs(time.time() - float(timestamp))
    except (TypeError, ValueError):
        return False

    if idade > JANELA_DE_TEMPO_SEGUNDOS:
        return False

    if not nonce:
        return False

    # `add` so grava se a chave ainda nao existe, e devolve False caso
    # contrario. Ler-e-depois-gravar teria uma janela de corrida entre as duas
    # operacoes, e duas requisicoes simultaneas com o mesmo nonce passariam.
    return cache.add(f"publibot:nonce:{nonce}", "1", RETENCAO_DE_NONCE_SEGUNDOS)
