"""Controle de acesso por tenant.

Este middleware existe por causa de uma consequencia direta de duas decisoes
anteriores que, juntas, abrem um buraco:

1. Os usuarios sao compartilhados, no schema `public` (ADR-0006).
2. O cookie de sessao tem escopo `.<ROOT_DOMAIN>`, para que o login feito na
   home acompanhe a pessoa ate o subdominio do tenant dela.

O efeito combinado e que **qualquer pessoa autenticada chega autenticada em
qualquer subdominio**. Sem uma verificacao explicita de vinculo, bastaria
digitar `outro-cliente.dominio` para entrar no painel de outro cliente — o
django-tenants resolveria o schema e serviria os dados normalmente, sem erro
nenhum.

O isolamento no banco protege os *dados* de vazarem entre schemas por engano de
consulta. Ele nao decide *quem* pode abrir qual schema. Essa e a funcao daqui.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _


class TenantAccessMiddleware:
    """Impede que um usuario autenticado acesse um tenant sem vinculo."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from django_tenants.utils import get_public_schema_name

        schema = connection.schema_name

        # No dominio raiz nao ha tenant a proteger.
        if schema == get_public_schema_name():
            return self.get_response(request)

        usuario = getattr(request, "user", None)
        if usuario is None or not usuario.is_authenticated:
            return self.get_response(request)

        if not self._tem_vinculo(usuario, schema):
            return self._negar(request)

        return self.get_response(request)

    @staticmethod
    def _tem_vinculo(usuario, schema_name: str) -> bool:
        if usuario.is_superuser:
            return True

        from django_tenants.utils import get_public_schema_name, schema_context

        from apps.accounts.models import TenantMembership

        # A consulta precisa rodar no public: e la que vivem os vinculos.
        # Dentro do schema do tenant a tabela seria alcancada por heranca de
        # search_path, mas ser explicito aqui evita depender desse detalhe.
        with schema_context(get_public_schema_name()):
            return TenantMembership.objects.filter(
                user=usuario, tenant__schema_name=schema_name, is_active=True
            ).exists()

    @staticmethod
    def _negar(request: HttpRequest) -> HttpResponse:
        """Devolve ao dominio raiz em vez de revelar que o tenant existe."""
        from django.contrib import messages

        messages.error(request, _("Voce nao tem acesso a este ambiente."))

        porta = request.get_port()
        sufixo = f":{porta}" if porta not in {"80", "443"} else ""
        esquema = "https" if request.is_secure() else "http"
        return redirect(f"{esquema}://{settings.ROOT_DOMAIN}{sufixo}/")
