"""Testes do `bootstrap_public` e da resolucao de host sem tenant.

Os dois nasceram do mesmo relato: rodar `migrate_schemas --shared`, subir o
servidor, abrir `localhost:8000` e receber

    Page not found (404) — No tenant for hostname "localhost"

Havia DUAS causas empilhadas no mesmo 404: o host errado (o dominio de dev tem
dois rotulos de proposito) e a linha em `accounts_tenant` que as migrations
nao criam. Nenhuma delas era deduzivel da mensagem.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, override_settings

from apps.accounts.middleware import TenantResolutionMiddleware
from apps.accounts.models import Domain, Tenant


# --------------------------------------------------------------------------
# bootstrap_public
# --------------------------------------------------------------------------
@pytest.mark.django_db
def test_bootstrap_public_cria_tenant_e_dominio_raiz():
    assert not Tenant.objects.filter(schema_name="public").exists()

    call_command("bootstrap_public", verbosity=0)

    tenant = Tenant.objects.get(schema_name="public")
    assert tenant.status == Tenant.Status.ACTIVE
    assert tenant.provisioned_at is not None

    dominio = Domain.objects.get(domain=settings.ROOT_DOMAIN)
    assert dominio.tenant_id == tenant.pk
    assert dominio.is_primary is True


@pytest.mark.django_db
def test_bootstrap_public_e_idempotente(public_tenant):
    """Rodar de novo nao pode duplicar nem falhar.

    Importa porque o comando entra no roteiro de deploy, que roda a cada
    release — nao so na primeira instalacao.
    """
    call_command("bootstrap_public", verbosity=0)
    call_command("bootstrap_public", verbosity=0)

    assert Tenant.objects.filter(schema_name="public").count() == 1
    assert Domain.objects.filter(domain=settings.ROOT_DOMAIN).count() == 1


@pytest.mark.django_db
def test_bootstrap_public_recusa_sequestrar_dominio_de_outro_tenant(tenant_factory):
    """O dominio raiz ja apontando para um tenant e um erro, nao um remendo.

    Reapontar em silencio derrubaria o cliente que estivesse naquele dominio.
    """
    outro = tenant_factory("outro_dono")
    Domain.objects.filter(domain=settings.ROOT_DOMAIN).delete()
    Domain.objects.create(domain=settings.ROOT_DOMAIN, tenant=outro, is_primary=False)

    with pytest.raises(CommandError, match="ja aponta para o tenant"):
        call_command("bootstrap_public", verbosity=0)

    assert Domain.objects.get(domain=settings.ROOT_DOMAIN).tenant_id == outro.pk


# --------------------------------------------------------------------------
# TenantResolutionMiddleware
# --------------------------------------------------------------------------
def _middleware() -> TenantResolutionMiddleware:
    return TenantResolutionMiddleware(get_response=lambda request: None)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_host_fora_do_dominio_raiz_redireciona_em_debug(public_tenant):
    """`localhost:8000` -> `publibot.localhost:8000`, com caminho e query.

    A porta precisa vir da requisicao, nao de uma constante: fixar 8000 aqui
    mandaria quem roda em outra porta para um servidor que nao existe.
    """
    request = RequestFactory().get("/painel/?aba=fila", HTTP_HOST="localhost:8099")

    resposta = _middleware().process_request(request)

    assert resposta.status_code == 302
    assert resposta["Location"] == f"http://{settings.ROOT_DOMAIN}:8099/painel/?aba=fila"


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_dominio_raiz_sem_tenant_registrado_nomeia_o_comando_que_falta(db):
    """Host certo e registro ausente: a mensagem tem de dizer o que rodar."""
    Domain.objects.filter(domain=settings.ROOT_DOMAIN).delete()
    request = RequestFactory().get("/", HTTP_HOST=settings.ROOT_DOMAIN)

    with pytest.raises(ImproperlyConfigured, match="bootstrap_public"):
        _middleware().process_request(request)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_subdominio_inexistente_continua_404(public_tenant):
    """Um tenant que nao existe nao vira redirect: continua 404.

    Redirecionar aqui esconderia o erro de digitacao no subdominio, que e
    justamente a informacao util.
    """
    from django.http import Http404

    request = RequestFactory().get("/", HTTP_HOST=f"naoexiste.{settings.ROOT_DOMAIN}")

    with pytest.raises(Http404):
        _middleware().process_request(request)


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_fora_do_debug_o_comportamento_e_o_original(public_tenant):
    """Em producao nenhum dos dois atalhos vale.

    Redirect e mensagem detalhada contam ao visitante o que existe e como o
    sistema esta configurado. O 404 seco e a resposta certa la.
    """
    from django.http import Http404

    for host in ("localhost", settings.ROOT_DOMAIN, f"naoexiste.{settings.ROOT_DOMAIN}"):
        Domain.objects.filter(domain=settings.ROOT_DOMAIN).delete()
        request = RequestFactory().get("/", HTTP_HOST=host)
        with pytest.raises(Http404):
            _middleware().process_request(request)
