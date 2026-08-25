"""Configuracao compartilhada dos testes."""

from __future__ import annotations

import pytest
from django.db import connection

from apps.accounts.models import Domain, Tenant, User


@pytest.fixture
def public_tenant(db) -> Tenant:
    tenant, _ = Tenant.objects.get_or_create(
        schema_name="public",
        defaults={"name": "PubliBot", "slug": "public", "status": Tenant.Status.ACTIVE},
    )
    Domain.objects.get_or_create(domain="localhost", tenant=tenant, defaults={"is_primary": True})
    return tenant


@pytest.fixture
def tenant_factory(db, public_tenant):
    """Cria um tenant com schema real no Postgres.

    Cria o schema de verdade (nao um mock) porque tudo o que estes testes
    protegem depende do comportamento real do search_path.
    """
    created: list[Tenant] = []

    def _make(schema_name: str) -> Tenant:
        tenant = Tenant.objects.create(
            schema_name=schema_name,
            name=schema_name.replace("_", " ").title(),
            slug=schema_name.replace("_", "-"),
            status=Tenant.Status.ACTIVE,
        )
        tenant.create_schema(check_if_exists=True, verbosity=0)
        Domain.objects.create(domain=f"{tenant.slug}.localhost", tenant=tenant, is_primary=True)
        created.append(tenant)
        return tenant

    yield _make

    connection.set_schema_to_public()
    for tenant in created:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema_name}" CASCADE')


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        email="revisor@exemplo.com",
        password="uma-senha-longa-de-teste",
        full_name="Revisor de Teste",
    )
