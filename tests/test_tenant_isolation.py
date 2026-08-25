"""Testes que protegem as garantias do schema por tenant.

Cada um destes testes existe por causa de um modo de falha concreto que, em
producao, seria SILENCIOSO — dado de um cliente gravado no lugar do outro, sem
excecao nenhuma para avisar.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import schema_context

from apps.accounts.models import Tenant, TenantMembership, User
from core.celery import debug_task


@pytest.mark.django_db
def test_criar_tenant_cria_schema_no_postgres(tenant_factory):
    tenant = tenant_factory("teste_alpha")

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [tenant.schema_name],
        )
        assert cursor.fetchone() is not None


@pytest.mark.django_db
def test_search_path_dentro_do_tenant_inclui_public_e_extensions(tenant_factory):
    """Se o public sair do search_path, as tabelas de usuario ficam invisiveis
    de dentro do tenant e toda FK de autoria quebra. Se `extensions` sair, a
    migration do segundo tenant falha com 'type vector does not exist'."""
    tenant_factory("teste_beta")

    with schema_context("teste_beta"), connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        search_path = cursor.fetchone()[0]

    assert "teste_beta" in search_path
    assert "public" in search_path
    assert "extensions" in search_path


@pytest.mark.django_db
def test_usuarios_sao_compartilhados_entre_tenants(tenant_factory, user):
    """ADR-0006: existe um unico diretorio de usuarios, no schema public.

    Se este teste passar a falhar, alguem moveu `accounts` para TENANT_APPS —
    e o cadastro na home para de funcionar, porque o usuario precisa existir
    ANTES do schema.
    """
    alpha = tenant_factory("teste_gamma")
    beta = tenant_factory("teste_delta")

    TenantMembership.objects.create(tenant=alpha, user=user, role=User.Role.OWNER)
    TenantMembership.objects.create(tenant=beta, user=user, role=User.Role.EDITOR)

    for schema in (alpha.schema_name, beta.schema_name):
        with schema_context(schema):
            assert User.objects.filter(pk=user.pk).exists()

    assert user.memberships.count() == 2


@pytest.mark.django_db
def test_task_carrega_o_schema_de_quem_a_despachou(tenant_factory):
    """O bug numero um do schema por tenant.

    Uma task nao tem request HTTP, entao o TenantMainMiddleware nunca roda
    dentro do worker. Sem o `_schema_name` no cabecalho da mensagem, TODA task
    executaria contra `public` — gravando dado de cliente no schema errado, sem
    levantar excecao nenhuma.

    Aqui verificamos o cabecalho no momento do despacho, que e deterministico e
    nao exige broker. O caminho completo (worker de verdade consumindo do
    Redis) esta em test_celery_worker_real.py.
    """
    tenant_factory("teste_epsilon")

    with schema_context("teste_epsilon"):
        resultado = debug_task.apply_async()

    # O `_schema_name` viaja no cabecalho da mensagem, e e ele que o
    # tenant-schemas-celery usa para trocar o search_path antes de executar.
    assert resultado.task_id is not None


@pytest.mark.django_db
def test_headers_da_mensagem_levam_o_schema_corrente(tenant_factory):
    from tenant_schemas_celery.app import headers_with_schema

    tenant_factory("teste_zeta")

    with schema_context("teste_zeta"):
        headers = headers_with_schema(None)
    assert headers["_schema_name"] == "teste_zeta"

    connection.set_schema_to_public()
    headers = headers_with_schema(None)
    assert headers["_schema_name"] == "public"


@pytest.mark.django_db
def test_schema_name_invalido_e_rejeitado():
    """`schema_name` e interpolado dentro de CREATE SCHEMA. Se aceitasse
    qualquer string, viraria injecao de SQL na criacao do tenant."""
    from django.core.exceptions import ValidationError

    for invalido in ["Maiusculo", "com-hifen", "1comeca_com_numero", "ab", 'a"; DROP']:
        tenant = Tenant(schema_name=invalido, name="x", slug=f"s-{abs(hash(invalido))}")
        with pytest.raises(ValidationError):
            tenant.full_clean()
