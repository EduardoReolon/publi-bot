"""Testes do comando `provision_tenant`.

Este comando existe porque o `create_tenant` nativo do django-tenants nao
funciona sozinho neste projeto: ele so chama `tenant.save()`, e a criacao do
schema fisico so acontece dentro do `save()` quando `auto_create_schema=True`
— flag que este projeto desliga de proposito (ADR-0001), para o
provisionamento nao travar a request HTTP de cadastro. Descoberto testando o
comando nativo manualmente: ele criava o registro e deixava o schema
inexistente, sem nenhum aviso.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection

from apps.accounts.models import Domain, Tenant


@pytest.mark.django_db
def test_provision_tenant_cria_registro_schema_e_migrations(public_tenant):
    call_command("provision_tenant", "prov_um", "--name=Provisionado", verbosity_schema=0)

    tenant = Tenant.objects.get(schema_name="prov_um")
    assert tenant.status == Tenant.Status.ACTIVE
    assert tenant.provisioned_at is not None
    assert Domain.objects.filter(tenant=tenant, is_primary=True).exists()

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [tenant.schema_name],
        )
        assert cursor.fetchone() is not None

    # O TenantSyncRouter precisa restringir o schema do tenant aos TENANT_APPS.
    # auth_user pertence ao public (SHARED_APPS); se aparecesse aqui dentro,
    # o isolamento estaria furado.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            [tenant.schema_name],
        )
        tabelas = {row[0] for row in cursor.fetchall()}

    assert "auth_user" not in tabelas
    assert "django_migrations" in tabelas

    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema_name}" CASCADE')


@pytest.mark.django_db
def test_provision_tenant_num_tenant_pronto_nao_altera_nada(public_tenant, tenant_factory, capsys):
    """Rodar de novo num tenant pronto e um no-op, nao um erro.

    O comando ja recusou esse caso, e a recusa se mostrou errada: o mesmo
    "ja existe" barrava tambem o registro pela metade — cadastro feito sem
    worker, linha gravada e schema nunca criado — que e exatamente o que
    precisa ser retomado pelo terminal. O que nao pode acontecer e o comando
    sobrescrever um tenant existente; e isso que este teste protege.
    """
    pronto = tenant_factory("prov_dois")
    nome_antes = pronto.name
    dominios_antes = set(Domain.objects.filter(tenant=pronto).values_list("domain", flat=True))

    call_command("provision_tenant", "prov_dois", "--name=Outro Nome Qualquer")

    assert "Nada a fazer" in capsys.readouterr().out
    pronto.refresh_from_db()
    assert pronto.name == nome_antes
    assert set(Domain.objects.filter(tenant=pronto).values_list("domain", flat=True)) == (
        dominios_antes
    )
    assert Tenant.objects.filter(schema_name="prov_dois").count() == 1


@pytest.mark.django_db
def test_provision_tenant_domain_default_usa_root_domain(public_tenant, settings):
    settings.ROOT_DOMAIN = "publibot.test"
    call_command("provision_tenant", "prov_tres", verbosity_schema=0)

    tenant = Tenant.objects.get(schema_name="prov_tres")
    domain = Domain.objects.get(tenant=tenant, is_primary=True)
    assert domain.domain == "prov-tres.publibot.test"

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema_name}" CASCADE')
