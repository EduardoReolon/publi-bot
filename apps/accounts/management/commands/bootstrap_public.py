"""Registra o tenant `public` e aponta o dominio raiz para ele.

Por que este comando existe: `migrate_schemas --shared` cria as TABELAS do
schema `public`, mas nao cria a LINHA em `accounts_tenant` que o django-tenants
consulta para resolver um host. Sao coisas diferentes, e a diferenca so aparece
na primeira requisicao, como um 404 cru:

    No tenant for hostname "publibot.localhost"

Sem este comando o passo ficava documentado no README como um `shell -c`
colado a mao — facil de pular, e o sintoma nao aponta para a causa.

E idempotente: rodar de novo apenas confirma o que ja existe.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import ProgrammingError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name

from apps.accounts.models import Domain, Tenant


class Command(BaseCommand):
    help = "Cria o registro do tenant `public` e o dominio raiz que resolve para ele."

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            help=f"Dominio raiz. Default: ROOT_DOMAIN ({settings.ROOT_DOMAIN!r}).",
        )
        parser.add_argument(
            "--name",
            default="PubliBot",
            help="Nome de exibicao do tenant publico.",
        )

    def handle(self, *args, **options):
        schema_name = get_public_schema_name()
        domain_name = options["domain"] or settings.ROOT_DOMAIN

        try:
            existente = Tenant.objects.filter(schema_name=schema_name).first()
        except ProgrammingError as exc:
            # A tabela nao existe: as migrations compartilhadas nao rodaram.
            raise CommandError(
                "As tabelas do schema public nao existem. Rode antes:\n"
                "  python manage.py migrate_schemas --shared"
            ) from exc

        with transaction.atomic():
            if existente is None:
                tenant = Tenant.objects.create(
                    schema_name=schema_name,
                    name=options["name"],
                    slug=schema_name,
                    status=Tenant.Status.ACTIVE,
                    provisioned_at=timezone.now(),
                )
                self.stdout.write(f"Tenant '{schema_name}' registrado.")
            else:
                tenant = existente
                self.stdout.write(f"Tenant '{schema_name}' ja existia.")

            dominio = Domain.objects.filter(domain=domain_name).first()
            if dominio is None:
                Domain.objects.create(domain=domain_name, tenant=tenant, is_primary=True)
                self.stdout.write(f"Dominio '{domain_name}' apontado para '{schema_name}'.")
            elif dominio.tenant_id != tenant.pk:
                # Nao sequestra o dominio de outro tenant em silencio.
                raise CommandError(
                    f"O dominio {domain_name!r} ja aponta para o tenant "
                    f"{dominio.tenant.schema_name!r}. Remova-o antes, ou use --domain."
                )
            else:
                self.stdout.write(f"Dominio '{domain_name}' ja apontava para '{schema_name}'.")

        porta = getattr(settings, "DEV_SERVER_PORT", "")
        sufixo = f":{porta}" if porta and settings.DEBUG else ""
        self.stdout.write(
            self.style.SUCCESS(f"Pronto. A home responde em http://{domain_name}{sufixo}/")
        )
