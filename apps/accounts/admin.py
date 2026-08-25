from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django_tenants.admin import TenantAdminMixin

from apps.accounts.models import Domain, Tenant, TenantMembership, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Painel de usuario adaptado ao login por e-mail.

    O `UserAdmin` padrao pressupoe o campo `username`, que este model nao tem —
    sem estes fieldsets o admin quebra ao abrir um usuario.
    """

    ordering = ("email",)
    list_display = ("email", "full_name", "role", "is_technical_reviewer", "is_active")
    list_filter = ("role", "is_technical_reviewer", "is_active", "is_staff")
    search_fields = ("email", "full_name")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Identificacao"), {"fields": ("full_name",)}),
        (
            _("Papel"),
            {
                "fields": ("role", "is_technical_reviewer"),
                "description": _(
                    "Revisor tecnico e exigido para aprovar conteudo de saude, financas e afins."
                ),
            },
        ),
        (
            _("Identidade externa"),
            {
                "fields": ("external_auth_provider", "external_subject_id"),
                "classes": ("collapse",),
                "description": _("Preenchido quando o login migrar para OIDC."),
            },
        ),
        (
            _("Permissoes"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Datas"), {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "password1", "password2", "role"),
            },
        ),
    )
    readonly_fields = ("last_login", "created_at")


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Tenant)
class TenantAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "schema_name", "status", "is_paused", "created_on")
    list_filter = ("status", "is_paused")
    search_fields = ("name", "schema_name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TenantMembershipInline]
    readonly_fields = ("provisioned_at", "provisioning_error", "created_on")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("domain",)


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__email", "tenant__name")
    autocomplete_fields = ("user", "tenant")
