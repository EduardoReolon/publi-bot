"""Testes do painel de um tenant.

A pagina existia como placeholder — so o titulo e o nome do schema. Quem
entrava concluia que faltava alguma coisa, e nao havia nada indicando por onde
operar o produto enquanto a interface propria nao existe.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.accounts.models import TenantMembership, User


@pytest.fixture
def tenant_com_dono(tenant_factory):
    tenant = tenant_factory("painel_teste")
    usuario = User.objects.create_user(
        email="dono@painel.com", password="uma-senha-longa-de-teste", full_name="Dono"
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)
    return tenant, usuario


@pytest.mark.django_db
def test_painel_mostra_contagens_e_aponta_para_o_admin(client, tenant_com_dono):
    tenant, usuario = tenant_com_dono
    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"

    corpo = client.get("/").content.decode()

    assert "Documentos na base" in corpo
    assert "/admin/knowledge/document/" in corpo
    assert "/admin/content/article/" in corpo
    # O que NAO existe precisa estar dito: uma tela de zeros sem explicacao
    # parece defeito.
    assert "interface propria" in corpo


@pytest.mark.django_db
def test_painel_conta_dentro_do_schema_do_tenant(client, tenant_com_dono):
    """As contagens tem de vir do schema do proprio tenant.

    Se viessem do public — ou de outro tenant — o painel viraria um vazamento
    de dados entre clientes, que e exatamente o que a arquitetura evita.
    """
    from django_tenants.utils import schema_context

    from apps.knowledge.models import Document, DocumentCategory

    tenant, usuario = tenant_com_dono

    with schema_context(tenant.schema_name):
        categoria = DocumentCategory.objects.create(name="Nutricao", slug="nutricao")
        Document.objects.create(
            category=categoria,
            title="Um artigo cientifico",
            file_sha256="a" * 64,
            license="cc-by",
        )

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"
    corpo = client.get("/").content.decode()

    posicao = corpo.index("Documentos na base")
    assert ">1<" in corpo[posicao : posicao + 300]


@pytest.mark.django_db
def test_painel_exige_vinculo(client, tenant_com_dono):
    """Quem nao tem vinculo nao ve as contagens de um tenant alheio."""
    tenant, _ = tenant_com_dono
    intruso = User.objects.create_user(
        email="intruso@exemplo.com", password="uma-senha-longa-de-teste", full_name="Intruso"
    )

    client.force_login(intruso)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"
    resposta = client.get("/")

    assert resposta.status_code == 302
    assert settings.ROOT_DOMAIN in resposta["Location"]
    assert f"{tenant.slug}." not in resposta["Location"]
