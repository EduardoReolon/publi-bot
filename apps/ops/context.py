"""Contexto compartilhado pelas telas de dentro de um tenant."""

from __future__ import annotations

from django.db import connection


def pendencias(request):
    """Numeros do menu, disponiveis em todo template.

    Sai calado fora de um tenant e para quem nao esta autenticado: no dominio
    raiz as tabelas destes models nem existem, e consultar ali levantaria
    ProgrammingError em toda pagina publica.
    """
    from django_tenants.utils import get_public_schema_name

    if connection.schema_name == get_public_schema_name():
        return {}

    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}

    from apps.ops.painel import contagens_de_pendencia

    return {"pendencias": contagens_de_pendencia()}
