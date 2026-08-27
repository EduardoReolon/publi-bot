"""Testes do tenant que existe como registro mas nao como schema.

Essa janela e consequencia direta do ADR-0001: `auto_create_schema = False`, o
cadastro grava a linha e deixa o schema para uma task. Entre uma coisa e outra
— ou para sempre, se o worker nunca subir — existe um Tenant sem schema.

Tres coisas quebravam por causa disso, e as tres foram relatadas na mesma
sessao:

1. `migrate_schemas` estourava para TODOS os tenants por causa de um so.
2. `provision_tenant` recusava retomar o registro, entao nao havia saida pelo
   terminal a nao ser apagar a linha.
3. A home listava o ambiente sem dizer em que endereco ele responde.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from django_tenants.utils import schema_exists

from apps.accounts.enderecos import url_do_tenant
from apps.accounts.migration_executors import IgnoraSchemaAusente
from apps.accounts.models import Domain, Tenant


@pytest.fixture
def tenant_sem_schema(public_tenant) -> Tenant:
    """Exatamente o estado em que um cadastro sem worker deixa o banco."""
    tenant = Tenant.objects.create(
        schema_name="incompleto",
        name="Incompleto",
        slug="incompleto",
        status=Tenant.Status.PROVISIONING,
    )
    Domain.objects.create(
        domain=f"incompleto.{settings.ROOT_DOMAIN}", tenant=tenant, is_primary=True
    )
    assert not schema_exists("incompleto")
    return tenant


# ---------------------------------------------------------------------------
# migrate_schemas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_executor_ignora_tenant_sem_schema(tenant_sem_schema, tenant_factory, capsys):
    """Um registro sem schema nao pode derrubar a migracao dos outros.

    O `migrate_schemas` do django-tenants confere `schema_exists` apenas no
    caminho `--schema=<nome>`; migrando todos, ele nao confere nada.
    """
    real = tenant_factory("de_verdade")
    executados: list[str] = []

    class ExecutorFalso:
        codename = "falso"

        def __init__(self, args, options):
            pass

        def run_migrations(self, tenants=None):
            executados.extend(tenants or [])

    classe = type("Filtrado", (IgnoraSchemaAusente, ExecutorFalso), {})
    classe(None, {}).run_migrations([tenant_sem_schema.schema_name, real.schema_name])

    assert executados == [real.schema_name]
    assert "incompleto" in capsys.readouterr().err


@pytest.mark.django_db
def test_o_que_foi_ignorado_aparece_na_saida(tenant_sem_schema, capsys):
    """Pular em silencio seria pior que estourar.

    Quem le "migrations aplicadas" concluiria que os tenants estao todos em
    dia — e um deles nem existe no banco.
    """

    class ExecutorFalso:
        codename = "falso"

        def __init__(self, args, options):
            pass

        def run_migrations(self, tenants=None):
            pass

    classe = type("Filtrado", (IgnoraSchemaAusente, ExecutorFalso), {})
    classe(None, {}).run_migrations([tenant_sem_schema.schema_name])

    erro = capsys.readouterr().err
    assert "incompleto" in erro
    assert "provision_tenant" in erro


# ---------------------------------------------------------------------------
# provision_tenant
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_provision_tenant_retoma_registro_existente(tenant_sem_schema):
    """O caso que o comando recusava e justamente o que ele precisa resolver."""
    call_command("provision_tenant", "incompleto", verbosity_schema=0)

    tenant_sem_schema.refresh_from_db()
    assert tenant_sem_schema.status == Tenant.Status.ACTIVE
    assert tenant_sem_schema.provisioned_at is not None
    assert schema_exists("incompleto")

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
            ["incompleto"],
        )
        assert cursor.fetchone()[0] > 0


@pytest.mark.django_db
def test_retomada_limpa_o_erro_anterior(tenant_sem_schema):
    """Um erro antigo gravado mantem a tela de espera mostrando falha resolvida."""
    Tenant.objects.filter(pk=tenant_sem_schema.pk).update(
        status=Tenant.Status.FAILED, provisioning_error="broker fora do ar"
    )

    call_command("provision_tenant", "incompleto", verbosity_schema=0)

    tenant_sem_schema.refresh_from_db()
    assert tenant_sem_schema.status == Tenant.Status.ACTIVE
    assert tenant_sem_schema.provisioning_error == ""


@pytest.mark.django_db
def test_provisionar_de_novo_um_tenant_pronto_nao_faz_nada(tenant_factory, capsys):
    pronto = tenant_factory("ja_pronto")

    call_command("provision_tenant", pronto.schema_name, verbosity_schema=0)

    assert "Nada a fazer" in capsys.readouterr().out


@pytest.mark.django_db
def test_retomada_recria_o_dominio_que_faltava(tenant_sem_schema):
    """Se o cadastro morreu no meio, pode nao haver dominio nenhum."""
    Domain.objects.filter(tenant=tenant_sem_schema).delete()

    call_command("provision_tenant", "incompleto", verbosity_schema=0)

    assert Domain.objects.filter(
        tenant=tenant_sem_schema, domain=f"incompleto.{settings.ROOT_DOMAIN}"
    ).exists()


# ---------------------------------------------------------------------------
# Endereco do tenant
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_url_do_tenant_usa_o_dominio_do_banco_e_a_porta_do_cliente(tenant_sem_schema):
    """O subdominio nao e adivinhavel a partir do slug.

    `incompleto` responde em `incompleto.publibot.localhost`, nao em
    `incompleto.localhost` — foi exatamente esse o engano relatado.
    """
    request = RequestFactory().get("/", HTTP_HOST=f"{settings.ROOT_DOMAIN}:8000")

    url = url_do_tenant(request, tenant_sem_schema)

    assert url == f"http://incompleto.{settings.ROOT_DOMAIN}:8000/"


@pytest.mark.django_db
def test_url_do_tenant_sem_dominio_e_none(tenant_sem_schema):
    Domain.objects.filter(tenant=tenant_sem_schema).delete()
    tenant_sem_schema.refresh_from_db()
    request = RequestFactory().get("/", HTTP_HOST=settings.ROOT_DOMAIN)

    assert url_do_tenant(request, tenant_sem_schema) is None


@pytest.mark.django_db
def test_home_mostra_o_endereco_de_cada_ambiente(client, tenant_factory):
    """A lista sem endereco nao servia para chegar a lugar nenhum."""
    from apps.accounts.models import TenantMembership, User

    tenant = tenant_factory("com_link")
    usuario = User.objects.create_user(
        email="dono@exemplo.com", password="uma-senha-longa-de-teste", full_name="Dono"
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{settings.ROOT_DOMAIN}:8000"
    corpo = client.get("/").content.decode()

    assert f"http://com-link.{settings.ROOT_DOMAIN}:8000/" in corpo

    # Um `{# #}` de varias linhas nao e comentario no Django: ele so vale para
    # uma linha, e o texto acaba impresso na pagina. Aconteceu neste template
    # e so apareceu ao abrir a home no navegador.
    assert "{#" not in corpo
    assert "subdominio do dominio raiz" not in corpo
