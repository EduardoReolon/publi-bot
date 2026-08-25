"""Tenancy e identidade.

Duas metades que vivem no mesmo app por decisao explicita (ADR-0010):

* `Tenant` e `Domain` sao o encanamento do django-tenants — cada Tenant e um
  schema do Postgres, cada Domain e um subdominio que resolve para ele.
* `User` e `TenantMembership` sao identidade. Ambos moram no schema `public`
  (ADR-0006): existe um unico diretorio de usuarios, e a associacao a um
  tenant e uma linha de TenantMembership.

Por que os usuarios ficam no public e nao dentro de cada schema:

1. O cadastro na home cria o usuario ANTES do schema existir. Com usuarios por
   schema isso e um problema de ovo e galinha.
2. Uma pessoa dona de varios sites loga uma vez, nao uma vez por site.
3. O Zitadel, previsto para depois, e um diretorio central com Organizations —
   mapeia um-para-um para Tenant. Usuarios por schema tornariam esse encaixe
   torto.

Dentro de um tenant o search_path e "<schema>, public, extensions", entao estas
tabelas continuam visiveis, e uma FK saindo de uma tabela do tenant para
`User` resolve normalmente (o Postgres aceita chave estrangeira entre schemas).
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_tenants.models import DomainMixin, TenantMixin


class UserManager(BaseUserManager):
    """Manager que usa e-mail como identificador, nao `username`."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("O e-mail e obrigatorio.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superusuario precisa de is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superusuario precisa de is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Usuario da plataforma, unico em toda a instalacao.

    Chave primaria em UUID porque o id aparece em URL de painel e em log; id
    sequencial vaza o numero total de usuarios do produto.
    """

    class Role(models.TextChoices):
        # Valores em ingles snake_case (ADR-0009): sao gravados em cada linha
        # e viajam no payload trocado com os Nos Finais. Os rotulos e que sao
        # traduzidos.
        OWNER = "owner", _("Proprietario")
        EDITOR = "editor", _("Editor")
        REVIEWER = "reviewer", _("Revisor")
        VIEWER = "viewer", _("Leitor")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # `username` do AbstractUser sai de cena; o login e por e-mail.
    username = None  # type: ignore[assignment]
    email = models.EmailField(_("e-mail"), unique=True)
    full_name = models.CharField(_("nome completo"), max_length=150, blank=True)

    role = models.CharField(
        _("papel"),
        max_length=16,
        choices=Role.choices,
        default=Role.VIEWER,
        help_text=_("Papel padrao. O papel efetivo por tenant vem do vinculo."),
    )

    # Portao para conteudo YMYL: um No marcado como YMYL nao publica sem que o
    # revisor tenha esta marca. Ver ADR-0001 e a politica editorial.
    is_technical_reviewer = models.BooleanField(
        _("revisor tecnico"),
        default=False,
        help_text=_(
            "Habilita aprovar conteudo de saude, financas e afins (YMYL). "
            "Exige credencial profissional registrada."
        ),
    )

    # --- Preparacao para o Zitadel (OIDC), ainda inativo --------------------
    # Quando a autenticacao migrar para o provedor externo, o `subject` do
    # token cai aqui e passa a ser a identidade canonica. Nulo enquanto o login
    # for e-mail e senha; unico quando preenchido, para nunca haver duas contas
    # locais apontando para a mesma identidade externa.
    external_auth_provider = models.CharField(_("provedor externo"), max_length=32, blank=True)
    external_subject_id = models.CharField(
        _("subject externo"), max_length=255, blank=True, null=True, unique=True
    )

    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        indexes = [models.Index(fields=["email"])]

    def __str__(self) -> str:
        return self.full_name or self.email

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email


schema_name_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_]{2,62}$",
    message=_(
        "Use apenas letras minusculas, numeros e sublinhado, comecando por "
        "letra, entre 3 e 63 caracteres."
    ),
)


class Tenant(TenantMixin):
    """Um cliente do SaaS — e, por regra de produto, um site (ADR-0003).

    Cada Tenant e um schema do Postgres. A configuracao do site (URL, chave de
    API, idioma de publicacao) NAO mora aqui: fica no model `Site`, dentro do
    schema do proprio tenant. O motivo e concreto — este model vive no schema
    `public`, compartilhado, e a chave de API de um site de terceiro nao deve
    ficar numa tabela compartilhada.
    """

    class Status(models.TextChoices):
        PROVISIONING = "provisioning", _("Provisionando")
        ACTIVE = "active", _("Ativo")
        SUSPENDED = "suspended", _("Suspenso")
        FAILED = "failed", _("Falha no provisionamento")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # `schema_name` vem do TenantMixin. Validado aqui porque o valor e
    # interpolado em `CREATE SCHEMA` — nao pode aceitar qualquer string.
    schema_name = models.CharField(
        max_length=63, unique=True, db_index=True, validators=[schema_name_validator]
    )

    name = models.CharField(_("nome"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=63, unique=True)

    status = models.CharField(
        _("situacao"),
        max_length=16,
        choices=Status.choices,
        default=Status.PROVISIONING,
        db_index=True,
    )

    # Fuso do cliente. A cadencia de publicacao e calculada nele e convertida
    # para UTC na gravacao — nunca o contrario.
    timezone = models.CharField(_("fuso horario"), max_length=64, default="UTC")

    # Interruptor por tenant. Toda task de publicacao consulta antes de sair
    # para a rede.
    is_paused = models.BooleanField(_("pausado"), default=False)

    created_on = models.DateField(_("criado em"), auto_now_add=True)
    provisioned_at = models.DateTimeField(_("provisionado em"), null=True, blank=True)
    provisioning_error = models.TextField(_("erro de provisionamento"), blank=True)

    # O django-tenants cria o schema no save() quando isto e True. Deixamos
    # False de proposito: criar schema e rodar ~25 migrations leva de segundos
    # a mais de um minuto, e isso nao pode acontecer dentro da request de
    # cadastro. Uma task assincrona faz o provisionamento.
    auto_create_schema = False
    auto_drop_schema = False

    class Meta:
        verbose_name = _("tenant")
        verbose_name_plural = _("tenants")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.schema_name})"


class Domain(DomainMixin):
    """Host que resolve para um tenant.

    Um tenant pode ter mais de um (dominio proprio alem do subdominio), mas
    exatamente um deve ser `is_primary`.
    """

    class Meta:
        verbose_name = _("dominio")
        verbose_name_plural = _("dominios")

    def __str__(self) -> str:
        return self.domain


class TenantMembership(models.Model):
    """Vinculo entre um usuario e um tenant, com o papel efetivo.

    Vive no schema `public`, junto com User e Tenant: e ele que responde
    "quais tenants esta pessoa pode acessar" na home, antes de qualquer schema
    ser selecionado.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("tenant"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("usuario"),
    )
    role = models.CharField(
        _("papel"), max_length=16, choices=User.Role.choices, default=User.Role.EDITOR
    )
    is_active = models.BooleanField(_("ativo"), default=True)
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("vinculo com tenant")
        verbose_name_plural = _("vinculos com tenants")
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="uniq_membership_tenant_user")
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user} @ {self.tenant} ({self.role})"
