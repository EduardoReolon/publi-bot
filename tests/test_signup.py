"""Testes do cadastro autonomo e do controle de acesso por tenant."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.urls import reverse

from apps.accounts.forms import SUBDOMINIOS_RESERVADOS, SignupForm
from apps.accounts.models import Domain, Tenant, TenantMembership

User = get_user_model()

# As rotas do dominio raiz vivem em `core.urls_public`, nao no ROOT_URLCONF
# (que e o dos tenants). Fora de uma request o `reverse` usa o ROOT_URLCONF,
# entao o urlconf precisa ser dito explicitamente — o que tambem documenta a
# divisao de roteamento entre a home e um tenant.
URLCONF_PUBLICO = "core.urls_public"


def url_publica(nome: str, *args) -> str:
    return reverse(nome, args=args, urlconf=URLCONF_PUBLICO)


DADOS_VALIDOS = {
    "subdomain": "acme",
    "organization": "ACME Ltda",
    "full_name": "Maria Souza",
    "email": "maria@acme.com.br",
    "password1": "uma-senha-bem-longa-42",
    "password2": "uma-senha-bem-longa-42",
}


@pytest.fixture
def cliente_publico(client):
    """Cliente HTTP apontando para o dominio raiz (schema public)."""
    client.defaults["HTTP_HOST"] = settings.ROOT_DOMAIN
    return client


# ---------------------------------------------------------------------------
# Validacao do subdominio
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_subdominio_reservado_e_recusado(public_tenant):
    """Nomes como `login` ou `admin` permitiriam phishing convincente dentro
    do proprio dominio, alem de colidir com hosts reais da plataforma."""
    for reservado in ["www", "admin", "login", "api", "seguranca"]:
        form = SignupForm({**DADOS_VALIDOS, "subdomain": reservado})
        assert not form.is_valid()
        assert "subdomain" in form.errors

    assert "publibot" in SUBDOMINIOS_RESERVADOS


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalido",
    ["-comeca", "termina-", "com--duplo", "1comeca", "ab"],
)
def test_subdominio_malformado_e_recusado(public_tenant, invalido):
    form = SignupForm({**DADOS_VALIDOS, "subdomain": invalido})
    assert not form.is_valid()
    assert "subdomain" in form.errors


@pytest.mark.django_db
def test_hifen_vira_underscore_no_schema_name(public_tenant, cliente_publico):
    """`meu-site` e valido como subdominio, mas `-` nao e aceito num
    identificador do PostgreSQL. A conversao precisa ser explicita."""
    resposta = cliente_publico.post(
        url_publica("accounts:signup"), {**DADOS_VALIDOS, "subdomain": "meu-site"}
    )
    assert resposta.status_code == 302

    tenant = Tenant.objects.get(slug="meu-site")
    assert tenant.schema_name == "meu_site"


@pytest.mark.django_db
def test_subdominio_duplicado_e_recusado(public_tenant, tenant_factory):
    tenant_factory("ocupado")
    form = SignupForm({**DADOS_VALIDOS, "subdomain": "ocupado"})
    assert not form.is_valid()


@pytest.mark.django_db
def test_email_duplicado_e_recusado(public_tenant, user):
    form = SignupForm({**DADOS_VALIDOS, "email": user.email})
    assert not form.is_valid()
    assert "email" in form.errors


@pytest.mark.django_db
def test_senha_fraca_e_recusada(public_tenant):
    form = SignupForm({**DADOS_VALIDOS, "password1": "123456", "password2": "123456"})
    assert not form.is_valid()
    assert "password1" in form.errors


# ---------------------------------------------------------------------------
# Fluxo de cadastro
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cadastro_cria_tenant_em_provisionamento_sem_criar_schema(
    public_tenant, cliente_publico, settings
):
    """O schema NAO pode ser criado dentro da request: criar schema e rodar as
    migrations leva de segundos a mais de um minuto (ADR-0001)."""
    settings.CELERY_TASK_ALWAYS_EAGER = False

    resposta = cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)
    assert resposta.status_code == 302

    tenant = Tenant.objects.get(slug="acme")
    assert tenant.status == Tenant.Status.PROVISIONING
    assert tenant.provisioned_at is None

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [tenant.schema_name],
        )
        assert cursor.fetchone() is None, "o schema nao devia existir ainda"


@pytest.mark.django_db
def test_cadastro_cria_usuario_dominio_e_vinculo(public_tenant, cliente_publico):
    cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)

    tenant = Tenant.objects.get(slug="acme")
    usuario = User.objects.get(email="maria@acme.com.br")

    assert usuario.role == User.Role.OWNER
    assert Domain.objects.filter(
        tenant=tenant, domain=f"acme.{settings.ROOT_DOMAIN}", is_primary=True
    ).exists()
    assert TenantMembership.objects.filter(
        tenant=tenant, user=usuario, role=User.Role.OWNER, is_active=True
    ).exists()


@pytest.mark.django_db
def test_cadastro_autentica_e_redireciona_para_a_espera(public_tenant, cliente_publico):
    resposta = cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)
    assert resposta.url == url_publica("accounts:provisioning", "acme")
    assert resposta.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_task_de_provisionamento_cria_o_schema(public_tenant, cliente_publico):
    from apps.accounts.tasks import provision_tenant

    cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)
    tenant = Tenant.objects.get(slug="acme")

    provision_tenant(str(tenant.pk))

    tenant.refresh_from_db()
    assert tenant.status == Tenant.Status.ACTIVE
    assert tenant.provisioned_at is not None

    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [tenant.schema_name],
        )
        assert cursor.fetchone() is not None
        cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema_name}" CASCADE')


@pytest.mark.django_db
def test_provisionamento_e_idempotente(public_tenant, tenant_factory):
    """`acks_late=True` significa que uma task pode ser reentregue apos queda
    do worker. Rodar duas vezes nao pode quebrar."""
    from apps.accounts.tasks import provision_tenant

    tenant = tenant_factory("repetido")
    tenant.status = Tenant.Status.PROVISIONING
    tenant.save(update_fields=["status"])

    assert provision_tenant(str(tenant.pk)) == "repetido"
    assert provision_tenant(str(tenant.pk)) == "repetido"

    tenant.refresh_from_db()
    assert tenant.status == Tenant.Status.ACTIVE


# ---------------------------------------------------------------------------
# Controle de acesso — a parte critica
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_status_do_provisionamento_nao_vaza_para_quem_nao_tem_vinculo(
    public_tenant, cliente_publico, tenant_factory
):
    """Sem esta checagem, qualquer pessoa autenticada leria o estado — e a
    mensagem de erro — de qualquer tenant, bastando adivinhar o slug."""
    alheio = tenant_factory("alheio")

    cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)

    resposta = cliente_publico.get(url_publica("accounts:provisioning_status", alheio.slug))
    assert resposta.status_code == 404


@pytest.mark.django_db
def test_dono_ve_o_status_do_proprio_tenant(public_tenant, cliente_publico):
    cliente_publico.post(url_publica("accounts:signup"), DADOS_VALIDOS)

    resposta = cliente_publico.get(url_publica("accounts:provisioning_status", "acme"))
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["status"] == Tenant.Status.PROVISIONING
    assert dados["pronto"] is False


@pytest.mark.django_db
def test_usuario_sem_vinculo_nao_entra_no_painel_de_outro_tenant(
    public_tenant, client, tenant_factory, user
):
    """O teste mais importante desta entrega.

    Os usuarios sao compartilhados (ADR-0006) e o cookie de sessao vale para
    todos os subdominios. Sem o TenantAccessMiddleware, bastaria digitar o
    subdominio de outro cliente para entrar no painel dele — o django-tenants
    resolveria o schema e serviria os dados normalmente, sem erro nenhum.
    """
    alheio = tenant_factory("empresa_x")
    client.force_login(user)

    resposta = client.get("/", HTTP_HOST=f"{alheio.slug}.{settings.ROOT_DOMAIN}")

    assert resposta.status_code == 302
    assert settings.ROOT_DOMAIN in resposta.url
    assert alheio.slug not in resposta.url


@pytest.mark.django_db
def test_usuario_com_vinculo_entra_no_painel(public_tenant, client, tenant_factory, user):
    meu = tenant_factory("minha_empresa")
    TenantMembership.objects.create(tenant=meu, user=user, role=User.Role.OWNER)
    client.force_login(user)

    resposta = client.get("/", HTTP_HOST=f"{meu.slug}.{settings.ROOT_DOMAIN}")

    assert resposta.status_code == 200
    assert b"minha_empresa" in resposta.content


@pytest.mark.django_db
def test_vinculo_inativo_nao_da_acesso(public_tenant, client, tenant_factory, user):
    meu = tenant_factory("suspensa")
    TenantMembership.objects.create(tenant=meu, user=user, role=User.Role.OWNER, is_active=False)
    client.force_login(user)

    resposta = client.get("/", HTTP_HOST=f"{meu.slug}.{settings.ROOT_DOMAIN}")
    assert resposta.status_code == 302


# ---------------------------------------------------------------------------
# Configuracao do cookie de sessao
# ---------------------------------------------------------------------------


def test_dominio_de_desenvolvimento_precisa_de_dois_rotulos():
    """Verificado com o Chromium: ao receber
    `Set-Cookie: ...; Domain=.localhost`, o navegador DESCARTA o atributo
    Domain e grava o cookie como host-only, porque `localhost` e tratado como
    sufixo publico. O login funciona no apex, o cookie aparece no navegador, e
    mesmo assim o subdominio do tenant devolve a tela de login — sem nenhuma
    mensagem que aponte a causa.

    Com dois rotulos (`publibot.localhost`) o cookie `.publibot.localhost` e
    aceito e atravessa os subdominios.
    """
    assert "." in settings.ROOT_DOMAIN, (
        f"ROOT_DOMAIN={settings.ROOT_DOMAIN!r} tem um unico rotulo. O cookie de "
        f"sessao nao vai atravessar para o subdominio do tenant. Use algo como "
        f"'publibot.localhost' em desenvolvimento."
    )


def test_cookie_de_sessao_abrange_os_subdominios():
    assert settings.SESSION_COOKIE_DOMAIN == f".{settings.ROOT_DOMAIN}"
