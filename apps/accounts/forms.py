"""Formularios do dominio raiz: cadastro e login."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Domain, Tenant

User = get_user_model()

# Nomes que nao podem virar subdominio de tenant.
#
# Dois motivos distintos:
#  - Alguns colidiriam com hosts reais da propria plataforma (www, api, admin).
#  - Outros permitiriam phishing convincente dentro do proprio dominio
#    (login.seudominio.com.br, seguranca.seudominio.com.br).
SUBDOMINIOS_RESERVADOS = frozenset(
    {
        "www",
        "api",
        "admin",
        "app",
        "mail",
        "smtp",
        "imap",
        "pop",
        "ftp",
        "ns",
        "ns1",
        "ns2",
        "dns",
        "mx",
        "cdn",
        "static",
        "media",
        "assets",
        "blog",
        "docs",
        "help",
        "support",
        "status",
        "dashboard",
        "painel",
        "login",
        "logout",
        "signup",
        "cadastro",
        "conta",
        "contas",
        "auth",
        "seguranca",
        "security",
        "billing",
        "pagamento",
        "checkout",
        "test",
        "teste",
        "dev",
        "staging",
        "stage",
        "prod",
        "producao",
        "public",
        "internal",
        "private",
        "root",
        "system",
        "sistema",
        "publibot",
        "webmail",
        "email",
        "no-reply",
        "noreply",
    }
)


class SignupForm(forms.Form):
    """Cadastro autonomo: subdominio desejado, e-mail e senha.

    Cria o Tenant em PROVISIONING e o User no schema public. O schema fisico e
    criado depois, por uma task — ver apps/accounts/tasks.py.
    """

    subdomain = forms.SlugField(
        label=_("Subdominio desejado"),
        max_length=40,
        min_length=3,
        help_text=_("Sera o endereco do seu painel. Apenas letras, numeros e hifen."),
    )
    organization = forms.CharField(label=_("Nome da organizacao"), max_length=120)
    full_name = forms.CharField(label=_("Seu nome"), max_length=150)
    email = forms.EmailField(label=_("E-mail"))
    password1 = forms.CharField(label=_("Senha"), widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(
        label=_("Confirme a senha"), widget=forms.PasswordInput, strip=False
    )

    def clean_subdomain(self) -> str:
        valor = self.cleaned_data["subdomain"].strip().lower()

        if valor in SUBDOMINIOS_RESERVADOS:
            raise ValidationError(_("Este subdominio esta reservado. Escolha outro."))

        # O SlugField aceita hifen, mas `schema_name` do PostgreSQL nao: o
        # identificador vira `_`. Isso torna a conversao explicita aqui, em vez
        # de deixar o banco recusar depois com uma mensagem incompreensivel.
        if valor.startswith("-") or valor.endswith("-") or "--" in valor:
            raise ValidationError(_("O subdominio nao pode comecar, terminar ou repetir hifen."))

        if valor[0].isdigit():
            raise ValidationError(_("O subdominio precisa comecar com uma letra."))

        schema_name = valor.replace("-", "_")

        if (
            Tenant.objects.filter(slug=valor).exists()
            or Tenant.objects.filter(schema_name=schema_name).exists()
        ):
            raise ValidationError(_("Este subdominio ja esta em uso."))

        return valor

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("Ja existe uma conta com este e-mail."))
        return email

    def clean(self):
        dados = super().clean()
        senha1, senha2 = dados.get("password1"), dados.get("password2")

        if senha1 and senha2 and senha1 != senha2:
            self.add_error("password2", _("As senhas nao conferem."))
        elif senha1:
            # Roda os validadores do Django (comprimento, senha comum, so
            # numeros, similaridade com dados da conta).
            usuario_provisorio = User(
                email=dados.get("email", ""), full_name=dados.get("full_name", "")
            )
            try:
                validate_password(senha1, usuario_provisorio)
            except ValidationError as exc:
                self.add_error("password1", exc)

        return dados


class LoginForm(AuthenticationForm):
    """Login por e-mail.

    O `AuthenticationForm` do Django rotula o campo como "username"; aqui ele
    recebe o e-mail, porque e esse o USERNAME_FIELD do model.
    """

    username = forms.EmailField(label=_("E-mail"), widget=forms.EmailInput)


def dominio_do_tenant(subdomain: str, root_domain: str) -> str:
    return f"{subdomain}.{root_domain}"


def criar_tenant_e_dono(
    *, subdomain: str, organization: str, full_name: str, email: str, senha: str, root_domain: str
) -> tuple[Tenant, User]:
    """Cria Tenant, Domain, User e o vinculo, tudo no schema public.

    Nao cria o schema fisico: isso e trabalho da task de provisionamento.
    """
    from apps.accounts.models import TenantMembership

    schema_name = subdomain.replace("-", "_")

    tenant = Tenant.objects.create(
        schema_name=schema_name,
        name=organization,
        slug=subdomain,
        status=Tenant.Status.PROVISIONING,
    )
    Domain.objects.create(
        domain=dominio_do_tenant(subdomain, root_domain), tenant=tenant, is_primary=True
    )

    usuario = User.objects.create_user(
        email=email, password=senha, full_name=full_name, role=User.Role.OWNER
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, role=User.Role.OWNER)

    return tenant, usuario
