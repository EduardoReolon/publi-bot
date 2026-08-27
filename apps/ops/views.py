"""Sondas de saude.

Duas, e nao uma, porque respondem perguntas diferentes:

* `healthz` — o processo esta vivo? Nao toca em nada externo. Se dependesse do
  banco, uma indisponibilidade momentaria do banco faria o orquestrador matar e
  recriar contêineres saudaveis, transformando um problema em dois.
* `readyz` — o processo consegue atender? Verifica as dependencias. E esta que
  o balanceador consulta antes de mandar trafego.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """Vivo. Deliberadamente nao consulta banco nem broker."""
    return JsonResponse({"status": "ok"})


@require_GET
def readyz(request: HttpRequest) -> JsonResponse:
    """Pronto para atender. Verifica as dependencias de verdade."""
    verificacoes: dict[str, str] = {}
    tudo_bem = True

    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        verificacoes["postgres"] = "ok"
    except Exception as exc:
        verificacoes["postgres"] = f"falhou: {exc}"
        tudo_bem = False

    try:
        from core.celery import app

        app.connection().ensure_connection(max_retries=1, timeout=3)
        verificacoes["broker"] = "ok"
    except Exception as exc:
        verificacoes["broker"] = f"falhou: {exc}"
        tudo_bem = False

    return JsonResponse(
        {"status": "ok" if tudo_bem else "degradado", "checks": verificacoes},
        status=200 if tudo_bem else 503,
    )
