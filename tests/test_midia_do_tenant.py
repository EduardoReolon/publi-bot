"""A rota do MEDIA_URL: arquivos do tenant, so para quem e dele."""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context


@pytest.fixture
def ambiente(tenant_factory, client, settings):
    from apps.accounts.models import TenantMembership, User
    from apps.content.models import Author

    tenant = tenant_factory("midia")
    usuario = User.objects.create_user(email="m@x.com", password="uma-senha-longa-de-teste")
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"
    with schema_context(tenant.schema_name):
        autor = Author.objects.create(name="Ana")
        autor.photo.save("ana.jpg", ContentFile(b"\xff\xd8foto"), save=True)
        yield tenant, usuario, client, autor


@pytest.mark.django_db
def test_a_foto_do_autor_abre_pelo_endereco_que_a_tela_usa(ambiente):
    _, usuario, client, autor = ambiente
    client.force_login(usuario)

    resposta = client.get(autor.photo.url)

    assert resposta.status_code == 200
    assert b"".join(resposta.streaming_content) == b"\xff\xd8foto"
    assert resposta["Content-Type"] == "image/jpeg"


@pytest.mark.django_db
def test_com_nginx_quem_entrega_e_o_x_accel(ambiente, settings):
    tenant, usuario, client, autor = ambiente
    settings.USAR_X_ACCEL = True
    client.force_login(usuario)

    resposta = client.get(autor.photo.url)

    assert resposta["X-Accel-Redirect"].startswith(f"/protected-media/{tenant.schema_name}/")


@pytest.mark.django_db
def test_sem_login_nao_abre(ambiente):
    *_, client, autor = ambiente
    assert client.get(autor.photo.url).status_code == 302


@pytest.mark.django_db
def test_outro_schema_e_fuga_de_pasta_dao_404(ambiente):
    tenant, usuario, client, _ = ambiente
    client.force_login(usuario)

    assert client.get("/media/outro_tenant/autores/x.jpg").status_code == 404
    assert client.get(f"/media/{tenant.schema_name}/../../etc/passwd").status_code == 404
