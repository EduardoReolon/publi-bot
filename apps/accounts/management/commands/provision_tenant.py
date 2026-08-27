"""Cria um tenant completo: registro, schema fisico e migrations.

Por que este comando existe e o `create_tenant` nativo do django-tenants nao
basta aqui: aquele comando so chama `tenant.save()`. A criacao do schema e a
aplicacao das migrations so acontecem dentro do `save()` quando
`auto_create_schema = True` no model. Este projeto desliga essa flag de
proposito (ADR-0001): criar schema e rodar ~25 migrations leva de segundos a
mais de um minuto, e isso nao pode acontecer dentro da request HTTP de
cadastro — por isso a Entrega 2 faz esse trabalho numa task assincrona.

Este comando e o equivalente sincrono dessa mesma rotina, para uso em
desenvolvimento, scripts e no terminal — onde nao ha request para travar.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_exists

from apps.accounts.models import Domain, Tenant


class Command(BaseCommand):
    help = "Cria um tenant, o schema fisico no Postgres e roda as migrations dentro dele."

    def add_arguments(self, parser):
        parser.add_argument("schema_name", help="Nome do schema, ex.: acme")
        parser.add_argument("--name", help="Nome de exibicao. Default: o schema_name.")
        parser.add_argument(
            "--domain", help="Dominio completo. Default: <schema_name>.<ROOT_DOMAIN>."
        )
        parser.add_argument(
            "--verbosity-schema",
            type=int,
            default=1,
            help="Verbosidade da aplicacao das migrations (0-3).",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        schema_name = options["schema_name"]
        name = options["name"] or schema_name.replace("_", " ").title()
        domain_name = options["domain"] or f"{schema_name.replace('_', '-')}.{settings.ROOT_DOMAIN}"

        # Retomar um registro existente e o caso comum, nao a excecao. O
        # cadastro web grava o Tenant e deixa o schema para uma task; se o
        # worker nao estava rodando, ou falhou, sobra exatamente isto: a linha
        # sem o schema. Recusar aqui — o que este comando fazia — deixava a
        # pessoa sem nenhuma saida pelo terminal, tendo de apagar o registro
        # para tentar de novo.
        tenant = Tenant.objects.filter(schema_name=schema_name).first()

        if tenant is not None:
            if schema_exists(schema_name):
                if tenant.status == Tenant.Status.ACTIVE:
                    self.stdout.write(
                        self.style.SUCCESS(f"Tenant '{schema_name}' ja esta pronto. Nada a fazer.")
                    )
                    return
                # Schema no lugar, registro desatualizado: so o estado ficou
                # para tras (o worker morreu entre criar o schema e gravar).
                self.stdout.write(f"Schema '{schema_name}' ja existe; corrigindo o registro.")
            else:
                self.stdout.write(
                    f"Registro de '{schema_name}' existe sem schema "
                    f"(situacao: {tenant.get_status_display()}). Retomando..."
                )
            # O dominio pode faltar se o cadastro morreu no meio.
            Domain.objects.get_or_create(
                domain=domain_name, defaults={"tenant": tenant, "is_primary": True}
            )
        else:
            with transaction.atomic():
                tenant = Tenant.objects.create(
                    schema_name=schema_name,
                    name=name,
                    slug=schema_name.replace("_", "-"),
                    status=Tenant.Status.PROVISIONING,
                )
                Domain.objects.create(domain=domain_name, tenant=tenant, is_primary=True)
            self.stdout.write(f"Registro criado. Criando schema '{schema_name}'...")
        try:
            tenant.create_schema(check_if_exists=True, verbosity=options["verbosity_schema"])
        except Exception as exc:
            tenant.status = Tenant.Status.FAILED
            tenant.provisioning_error = str(exc)
            tenant.save(update_fields=["status", "provisioning_error"])
            raise CommandError(f"Falha ao criar o schema: {exc}") from exc

        tenant.status = Tenant.Status.ACTIVE
        tenant.provisioned_at = timezone.now()
        # Limpar o erro faz parte de retomar: deixa-lo gravado mantem a tela de
        # espera mostrando uma falha que ja foi resolvida.
        tenant.provisioning_error = ""
        tenant.save(update_fields=["status", "provisioned_at", "provisioning_error"])

        self.stdout.write(
            self.style.SUCCESS(f"Tenant '{schema_name}' pronto. Dominio: http://{domain_name}")
        )
