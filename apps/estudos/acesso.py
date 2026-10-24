"""Quem ve os estudos: superusuario, no tenant marcado."""

from __future__ import annotations

from functools import wraps

from django.db import connection
from django.http import Http404


def pode_ver(request) -> bool:
    usuario = getattr(request, "user", None)
    tenant = getattr(connection, "tenant", None)
    return bool(
        usuario is not None
        and usuario.is_authenticated
        and usuario.is_superuser
        and getattr(tenant, "estudos", False)
    )


def exige(view):
    """404 para quem nao pode ver (a tela nem existe para o cliente)."""

    @wraps(view)
    def envolta(request, *args, **kwargs):
        if not pode_ver(request):
            raise Http404
        return view(request, *args, **kwargs)

    return envolta
