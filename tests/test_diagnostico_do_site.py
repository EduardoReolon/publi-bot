"""O teste de conexao: aceita o certo e confere que o site recusa o falso."""

from __future__ import annotations

import httpx
import pytest
from django_tenants.utils import schema_context

from apps.inference.security import cifrar
from apps.integrations import client as modulo_do_cliente
from apps.integrations.models import Site
from apps.integrations.signing import conferir

CHAVE, SEGREDO = "chave-de-api-do-site", "segredo-de-assinatura"


def _site_falso(*, confere_assinatura=True, guarda_nonce=True):
    """Um site que segue (ou nao) as regras de autenticacao do contrato."""
    vistos: set[str] = set()

    def responder(pedido: httpx.Request) -> httpx.Response:
        h = pedido.headers
        valida = h.get("X-API-KEY") == CHAVE and conferir(
            SEGREDO, h["X-Timestamp"], h["X-Nonce"], pedido.content, h["X-Signature"]
        )
        repetido = h["X-Nonce"] in vistos
        vistos.add(h["X-Nonce"])
        if (confere_assinatura and not valida) or (guarda_nonce and repetido):
            return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
        if pedido.url.path.endswith("/health/"):
            return httpx.Response(
                200, json={"contract_versions": ["1"], "capabilities": ["publish"]}
            )
        return httpx.Response(200, json={"published_posts": [{"url": "https://x/1"}]})

    return httpx.MockTransport(responder)


@pytest.fixture
def site(tenant_factory):
    tenant = tenant_factory("diagnostico")
    with schema_context(tenant.schema_name):
        instancia = Site.objects.create(
            name="Site",
            slug="site",
            base_url="https://exemplo.com.br",
            api_key_ciphertext=cifrar(CHAVE),
            signing_secret_ciphertext=cifrar(SEGREDO),
        )
        instancia.tenant = tenant
        yield instancia


def _usar(monkeypatch, transporte):
    original = httpx.Client

    def cliente(**kwargs):
        kwargs.pop("limits", None)
        return original(transport=transporte, **kwargs)

    monkeypatch.setattr(modulo_do_cliente.httpx, "Client", cliente)


def _resultado(site):
    from apps.integrations.diagnostico import testar_site

    return {e.nome: e for e in testar_site(site)}


@pytest.mark.django_db
def test_site_que_segue_o_contrato_passa_em_tudo(site, monkeypatch):
    _usar(monkeypatch, _site_falso())

    etapas = _resultado(site)

    assert all(e.ok for e in etapas.values()), etapas
    assert set(etapas) == {
        "Chave e segredo aceitos",
        "Recusa segredo errado",
        "Recusa nonce repetido",
        "Recusa horario vencido",
        "Leitura das publicacoes",
    }


@pytest.mark.django_db
def test_site_que_aceita_qualquer_assinatura_falha(site, monkeypatch):
    """Responde igual ao site correto: so o teste de recusa mostra o buraco."""
    _usar(monkeypatch, _site_falso(confere_assinatura=False, guarda_nonce=False))

    etapas = _resultado(site)

    assert etapas["Chave e segredo aceitos"].ok
    assert not etapas["Recusa segredo errado"].ok
    assert not etapas["Recusa nonce repetido"].ok
    assert not etapas["Recusa horario vencido"].ok


@pytest.mark.django_db
def test_segredo_diferente_para_na_primeira_etapa(site, monkeypatch):
    _usar(monkeypatch, _site_falso())
    site.signing_secret_ciphertext = cifrar("outro-segredo")
    site.save()

    etapas = list(_resultado(site).values())

    assert len(etapas) == 1
    assert not etapas[0].ok
    assert "chave e o segredo" in etapas[0].detalhe


@pytest.mark.django_db
def test_a_tela_mostra_as_etapas(site, monkeypatch, client, settings):
    from apps.accounts.models import TenantMembership, User

    _usar(monkeypatch, _site_falso())
    usuario = User.objects.create_user(email="d@x.com", password="uma-senha-longa-de-teste")
    TenantMembership.objects.create(tenant=site.tenant, user=usuario, is_active=True)
    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{site.tenant.slug}.{settings.ROOT_DOMAIN}"

    resposta = client.post("/site/testar/", follow=True)

    texto = resposta.content.decode()
    assert "Recusa nonce repetido" in texto
    assert "FALHA" not in texto
