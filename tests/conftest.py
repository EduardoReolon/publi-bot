"""Configuracao compartilhada dos testes."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection

from apps.accounts.models import Domain, Tenant, User


@pytest.fixture
def public_tenant(db) -> Tenant:
    tenant, _ = Tenant.objects.get_or_create(
        schema_name="public",
        defaults={"name": "PubliBot", "slug": "public", "status": Tenant.Status.ACTIVE},
    )
    # Derivado do settings, nunca uma constante repetida: o dominio de
    # desenvolvimento precisa ter dois rotulos para o cookie de sessao
    # atravessar subdominios, e fixar "localhost" aqui faria os testes
    # divergirem silenciosamente da configuracao real.
    Domain.objects.get_or_create(
        domain=settings.ROOT_DOMAIN, tenant=tenant, defaults={"is_primary": True}
    )
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
        Domain.objects.create(
            domain=f"{tenant.slug}.{settings.ROOT_DOMAIN}", tenant=tenant, is_primary=True
        )
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


@pytest.fixture(scope="session")
def _exige_pgvector(django_db_setup, django_db_blocker):
    """Falha cedo e com mensagem util se o banco de teste nao tiver o pgvector.

    O banco de teste e criado do zero pelo pytest-django e herda do
    `template1`. Como `vector` nao e uma extensao "trusted", cria-la exige
    superusuario — e o usuario da aplicacao nao e (nem deveria ser). Por isso
    `scripts/setup-db.sh` instala a extensao no `template1`.

    Sem esta verificacao, a ausencia se manifestaria como
    "current transaction is aborted" em cascata, varios testes adiante, sem
    apontar para a causa.
    """
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.nspname
              FROM pg_extension e
              JOIN pg_namespace n ON n.oid = e.extnamespace
             WHERE e.extname = 'vector'
            """
        )
        row = cursor.fetchone()

    if row is None:
        pytest.fail(
            "A extensao 'vector' nao existe no banco de teste.\n"
            "Rode: ./scripts/setup-db.sh  (ele instala no template1, de onde "
            "os bancos de teste herdam)",
            pytrace=False,
        )
    if row[0] != "extensions":
        pytest.fail(
            f"A extensao 'vector' esta no schema '{row[0]}', deveria estar em "
            f"'extensions'. No public ela nao fica alcancavel para o segundo "
            f"tenant.",
            pytrace=False,
        )
