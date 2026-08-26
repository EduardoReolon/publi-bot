from __future__ import annotations

from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.inference.models import InferenceConnection, InferenceLease
from apps.inference.security import guardar_chave


class InferenceConnectionForm(forms.ModelForm):
    """Formulario que nunca exibe a credencial guardada.

    O campo de senha vem sempre vazio: preencher exibiria a chave de um
    terceiro na tela e no HTML da pagina. Deixar em branco preserva a atual.
    """

    api_key = forms.CharField(
        label=_("Chave de API"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Deixe em branco para manter a chave atual."),
    )

    class Meta:
        model = InferenceConnection
        # Lista explicita em vez de `exclude`: com exclude, um campo novo
        # adicionado ao model entraria no formulario sozinho. Para um model que
        # guarda credencial de terceiro, um campo aparecer na tela sem alguem
        # ter decidido isso e exatamente o que nao pode acontecer.
        fields = (
            "name",
            "kind",
            "tenant",
            "is_active",
            "base_url",
            "workloads",
            "max_concurrency",
            "lease_seconds",
            "default_model",
            "health_status",
            "circuit_open_until",
        )

    def save(self, commit=True):
        objeto = super().save(commit=False)
        nova = self.cleaned_data.get("api_key")
        if nova:
            guardar_chave(objeto, nova)
        if commit:
            objeto.save()
        return objeto


@admin.register(InferenceConnection)
class InferenceConnectionAdmin(admin.ModelAdmin):
    form = InferenceConnectionForm
    list_display = (
        "name",
        "kind",
        "tenant",
        "max_concurrency",
        "health_status",
        "consecutive_failures",
        "is_active",
    )
    list_filter = ("kind", "is_active", "health_status")
    search_fields = ("name", "base_url")
    readonly_fields = (
        "api_key_last4",
        "consecutive_failures",
        "circuit_open_until",
        "last_success_at",
    )

    fieldsets = (
        (None, {"fields": ("name", "kind", "tenant", "is_active")}),
        (_("Endereco"), {"fields": ("base_url", "api_key", "api_key_last4")}),
        (
            _("Capacidade"),
            {
                "fields": ("workloads", "max_concurrency", "lease_seconds", "default_model"),
                "description": _(
                    "Para uma GPU local, concorrencia maxima 1. Numa placa de 8 GB, "
                    "duas inferencias simultaneas estouram a VRAM e o modelo cai "
                    "para CPU silenciosamente — 15 vezes mais lento, sem erro."
                ),
            },
        ),
        (
            _("Saude"),
            {
                "fields": (
                    "health_status",
                    "consecutive_failures",
                    "circuit_open_until",
                    "last_success_at",
                )
            },
        ),
    )


@admin.register(InferenceLease)
class InferenceLeaseAdmin(admin.ModelAdmin):
    list_display = (
        "connection",
        "owner_key",
        "model_name",
        "acquired_at",
        "expires_at",
        "released_at",
    )
    list_filter = ("connection",)
    readonly_fields = ("connection", "owner_key", "model_name", "acquired_at", "expires_at")
