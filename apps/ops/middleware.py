"""Identificador de requisicao.

Sem um identificador que atravesse a requisicao, o log de um erro num worker
nao tem como ser ligado a requisicao que o originou — e diagnosticar vira
correlacionar horarios na mao.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse

_id_da_requisicao: ContextVar[str] = ContextVar("id_da_requisicao", default="")


def id_atual() -> str:
    return _id_da_requisicao.get()


class RequestIDMiddleware:
    """Atribui (ou reaproveita) um identificador por requisicao."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Reaproveita o do proxy quando existe: assim o mesmo identificador
        # atravessa Nginx, aplicacao e worker.
        identificador = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = _id_da_requisicao.set(identificador)
        request.request_id = identificador

        try:
            resposta = self.get_response(request)
        finally:
            _id_da_requisicao.reset(token)

        resposta["X-Request-ID"] = identificador
        return resposta


class FiltroDeRequestID(logging.Filter):
    """Injeta o identificador em cada linha de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = id_atual() or "-"
        return True


class HealthCheckMiddleware:
    """Responde as sondas de saude ANTES da resolucao de tenant.

    Descoberto testando: com o `TenantMainMiddleware` a frente, `/healthz/`
    devolve 404 sempre que o host nao corresponde a um tenant cadastrado. E
    exatamente o que acontece em producao — o balanceador e o orquestrador
    consultam por IP ou por um nome interno, que nunca e o dominio de um
    cliente.

    O efeito seria a infraestrutura concluir que a aplicacao esta morta quando
    ela esta perfeitamente saudavel, e ficar reiniciando conteineres bons.

    Precisa vir ANTES do TenantMainMiddleware na lista.
    """

    ROTAS = frozenset({"/healthz/", "/healthz", "/readyz/", "/readyz"})

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path not in self.ROTAS:
            return self.get_response(request)

        from apps.ops.views import healthz, readyz

        if request.path.startswith("/healthz"):
            return healthz(request)
        return readyz(request)
