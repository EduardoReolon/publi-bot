"""Privacidade: as paginas publicas que as redes exigem e a exclusao de dados.

Meta, LinkedIn e Google pedem, para liberar um app: politica de privacidade,
termos de servico e um jeito de a pessoa pedir a exclusao dos dados. Ficam no
dominio raiz (um endereco so para todos os clientes).

A exclusao pela Meta chega como `signed_request` (POST), assinado com a chave
secreta do app (HMAC-SHA256). O pedido e conferido, cada cliente apaga o que
for daquela pessoa (pelos modulos, via `extensoes.excluir_dados`) e a Meta
recebe um endereco de acompanhamento e um codigo de confirmacao.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets

from django.conf import settings

logger = logging.getLogger("publibot.accounts")


class PedidoInvalido(ValueError):
    pass


def _b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def ler_signed_request(signed_request: str, segredo: str) -> dict:
    """O conteudo do `signed_request` da Meta, conferida a assinatura."""
    if not segredo:
        raise PedidoInvalido("app da Meta nao configurado (SOCIAL_META_APP_SECRET).")
    try:
        assinatura, carga = (signed_request or "").split(".", 1)
        dados = json.loads(_b64(carga))
        recebida = _b64(assinatura)
    except (ValueError, json.JSONDecodeError) as exc:
        raise PedidoInvalido("pedido malformado.") from exc
    esperada = hmac.new(segredo.encode(), carga.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(recebida, esperada):
        raise PedidoInvalido("assinatura nao confere.")
    if str(dados.get("algorithm", "")).upper() != "HMAC-SHA256" or not dados.get("user_id"):
        raise PedidoInvalido("pedido sem usuario.")
    return dados


def excluir_em_todos_os_clientes(rede: str, usuario: str, *, apagar: bool) -> int:
    """Roda a exclusao (ou so a desconexao) em cada cliente ativo."""
    from django_tenants.utils import schema_context

    from apps.accounts.varredura import schemas_ativos
    from apps.ops import extensoes

    total = 0
    for schema in schemas_ativos():
        with schema_context(schema):
            total += extensoes.excluir_dados(rede, usuario, apagar=apagar)
    return total


def registrar_exclusao(rede: str, usuario: str) -> object:
    from apps.accounts.models import PedidoDeExclusao

    apagados = excluir_em_todos_os_clientes(rede, usuario, apagar=True)
    return PedidoDeExclusao.objects.create(
        codigo=secrets.token_hex(8),
        rede=rede,
        usuario_remoto=usuario[:120],
        situacao=PedidoDeExclusao.Situacao.FEITO if apagados else PedidoDeExclusao.Situacao.NADA,
        apagados=apagados,
    )


def contato() -> dict:
    """Quem responde pelo tratamento dos dados (configuravel no .env)."""
    return {
        "operador": getattr(settings, "OPERADOR_NOME", "") or "PubliBot",
        "documento": getattr(settings, "OPERADOR_DOCUMENTO", ""),
        "email": getattr(settings, "PRIVACIDADE_EMAIL", "")
        or f"privacidade@{settings.ROOT_DOMAIN}",
        "dominio": settings.ROOT_DOMAIN,
    }
