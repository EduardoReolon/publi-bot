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
from django_tenants.middleware.main import TenantMainMiddleware


def url_do_dominio_raiz(request: HttpRequest, caminho: str = "/") -> str:
    """Monta a URL absoluta do dominio raiz preservando a porta que o cliente usou.

    A porta sai do cabecalho `Host`, via `request.get_host()`, e nao de
    `request.get_port()`. A diferenca importa em producao: `get_port()` le
    `SERVER_PORT`, que atras do nginx e a porta INTERNA do gunicorn, nao a que
    o navegador acessou. Sem `USE_X_FORWARDED_PORT` — que este projeto nao
    define, porque o nginx repassa o `Host` original — quem fosse
    redirecionado daqui cairia em `https://dominio:8000/`, uma porta que nao
    esta publicada. `get_host()` devolve o que o cliente realmente pediu.
    """
    _, _, porta = request.get_host().partition(":")
    sufixo = f":{porta}" if porta and porta not in {"80", "443"} else ""
    esquema = "https" if request.is_secure() else "http"
    return f"{esquema}://{settings.ROOT_DOMAIN}{sufixo}{caminho}"


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

        return redirect(url_do_dominio_raiz(request))


class TenantResolutionMiddleware(TenantMainMiddleware):
    """Resolve o schema pelo host, com duas mensagens no lugar do 404 cru.

    O `TenantMainMiddleware` do django-tenants responde a qualquer host
    desconhecido com o mesmo 404:

        No tenant for hostname "localhost"

    Isso e correto para producao — nao revela quais tenants existem — mas em
    desenvolvimento esconde as duas causas que respondem por quase todo caso:

    1. **Host errado.** O dominio de dev tem dois rotulos de proposito
       (`publibot.localhost`), pelo motivo de cookie descrito em `dev.py`.
       Abrir `localhost:8000` por reflexo devolve o 404 acima. Aqui isso vira
       um redirect para o `ROOT_DOMAIN`, preservando porta, caminho e query.

    2. **Tenant `public` nao registrado.** `migrate_schemas --shared` cria as
       tabelas do schema public, nao a linha em `accounts_tenant` que resolve
       o dominio raiz. Sao passos distintos, e o 404 nao diz qual falta.

    Fora do DEBUG o comportamento e o original, sem excecao.
    """

    def no_tenant_found(self, request: HttpRequest, hostname: str):
        from django.core.exceptions import ImproperlyConfigured

        raiz = settings.ROOT_DOMAIN

        if settings.DEBUG and hostname != raiz and not hostname.endswith(f".{raiz}"):
            # Host fora do dominio do projeto: quase sempre `localhost` ou
            # `127.0.0.1` digitado por reflexo. Manda para o lugar certo.
            return redirect(url_do_dominio_raiz(request, request.get_full_path()))

        if settings.DEBUG and hostname == raiz:
            # O host esta certo; o que falta e o registro do tenant publico.
            raise ImproperlyConfigured(
                f"O dominio raiz {raiz!r} nao esta registrado como tenant. Rode:\n"
                f"    python manage.py migrate_schemas --shared\n"
                f"    python manage.py bootstrap_public"
            )

        return super().no_tenant_found(request, hostname)
