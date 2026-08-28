"""Testes do painel de um tenant.

O painel comecou como placeholder, virou uma lista de contagens ligada ao
admin, e hoje e a porta das telas de operacao. O que estes testes protegem
atravessou as tres versoes: os numeros saem do schema DESTE tenant, e quem nao
tem vinculo nao chega neles.
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
def test_painel_leva_as_telas_que_resolvem_cada_pendencia(client, tenant_com_dono):
    """Contagem que nao leva a lugar nenhum e ruido.

    Cada numero do painel precisa ser um caminho para a tela onde aquilo se
    resolve — senao a pessoa le o numero e nao sabe o que fazer com ele.
    """
    tenant, usuario = tenant_com_dono
    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"

    corpo = client.get("/").content.decode()

    assert "Artigos aguardando revisao" in corpo
    assert "/artigos/?situacao=pending_review" in corpo
    assert "/documentos/?situacao=pending_curation" in corpo
    # A secao "Precisa de atencao" nao aparece quando nada quebrou — mostrar um
    # bloco de problemas vazio ensina a ignora-lo. Coberta em test_interface.py.
    assert "Precisa de atencao" not in corpo


@pytest.mark.django_db
def test_painel_conta_dentro_do_schema_do_tenant(client, tenant_com_dono):
    """As contagens tem de vir do schema do proprio tenant.

    Se viessem do public — ou de outro tenant — o painel viraria um vazamento
    de dados entre clientes, que e exatamente o que a arquitetura evita.
    """
    from django_tenants.utils import schema_context

    from apps.content.models import Article

    tenant, usuario = tenant_com_dono

    with schema_context(tenant.schema_name):
        Article.objects.create(title="Esperando revisao", status=Article.Status.PENDING_REVIEW)

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"
    corpo = client.get("/").content.decode()

    posicao = corpo.index("Artigos aguardando revisao")
    # O valor fica no cartao, logo acima do rotulo.
    assert ">1<" in corpo[max(0, posicao - 300) : posicao]


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
