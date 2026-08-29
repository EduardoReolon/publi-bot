from __future__ import annotations

from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.inference.security import cifrar
from apps.integrations.models import (
    AuthorPhotoDelivery,
    PublicationSchedule,
    PublicationSlot,
    PublishAttempt,
    Site,
    SiteApiCall,
    SitePost,
)
from apps.integrations.signing import impressao_da_chave


class SiteForm(forms.ModelForm):
    """Nunca exibe as credenciais guardadas.

    Campos de senha vem sempre vazios: preenche-los exibiria a chave de um
    terceiro na tela e no HTML da pagina. Deixar em branco preserva a atual.
    """

    api_key = forms.CharField(
        label=_("Chave de API"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Deixe em branco para manter a chave atual."),
    )
    signing_secret = forms.CharField(
        label=_("Segredo de assinatura"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Distinto da chave de API de proposito: vazar um nao entrega o outro."),
    )

    class Meta:
        model = Site
        # Lista explicita, nao `exclude`: com exclude, um campo novo entraria
        # no formulario sozinho — e este model guarda credencial de terceiro.
        fields = (
            "name",
            "slug",
            "base_url",
            "platform",
            "content_language",
            "site_timezone",
            "niche",
            "is_sensitive",
            "responsible_professional",
            "default_author",
            "default_author_credentials",
            "model_overrides",
            "contract_version",
            "capabilities",
            "publishing_paused",
            "max_articles_per_month",
        )

    def save(self, commit=True):
        from django.utils import timezone

        objeto = super().save(commit=False)

        nova_chave = self.cleaned_data.get("api_key")
        if nova_chave:
            objeto.api_key_ciphertext = cifrar(nova_chave)
            objeto.api_key_fingerprint = impressao_da_chave(nova_chave)
            objeto.api_key_last4 = nova_chave[-4:]
            objeto.api_key_rotated_at = timezone.now()

        novo_segredo = self.cleaned_data.get("signing_secret")
        if novo_segredo:
            objeto.signing_secret_ciphertext = cifrar(novo_segredo)

        if commit:
            objeto.save()
        return objeto


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    form = SiteForm
    list_display = (
        "name",
        "base_url",
        "content_language",
        "is_sensitive",
        "publishing_paused",
        "health_status",
        "consecutive_failures",
    )
    list_filter = ("platform", "is_sensitive", "publishing_paused", "health_status")
    search_fields = ("name", "base_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = (
        "api_key_last4",
        "api_key_fingerprint",
        "api_key_rotated_at",
        "health_status",
        "consecutive_failures",
        "circuit_open_until",
        "last_success_at",
    )

    fieldsets = (
        (None, {"fields": ("name", "slug", "base_url", "platform")}),
        (
            _("Credenciais"),
            {
                "fields": (
                    "api_key",
                    "signing_secret",
                    "api_key_last4",
                    "api_key_fingerprint",
                    "api_key_rotated_at",
                ),
                "description": _(
                    "Guardadas cifradas. A chave de cifra vive fora do banco: um dump "
                    "sozinho nao entrega acesso ao site do cliente. Rotacione a cada "
                    "90 dias."
                ),
            },
        ),
        (
            _("Conteudo"),
            {
                "fields": (
                    "content_language",
                    "site_timezone",
                    "niche",
                    "default_author",
                    "default_author_credentials",
                    "model_overrides",
                )
            },
        ),
        (
            _("Responsabilidade"),
            {
                "fields": ("is_sensitive", "responsible_professional"),
                "description": _(
                    "Tema sensivel exige revisor com credencial tecnica registrada "
                    "para aprovar conteudo."
                ),
            },
        ),
        (
            _("Contrato"),
            {
                "fields": ("contract_version", "capabilities"),
                "description": _(
                    "Declarados pelo site em /api/v1/health/. O SaaS degrada conforme "
                    "o que o site suporta, em vez de assumir."
                ),
            },
        ),
        (
            _("Operacao"),
            {
                "fields": (
                    "publishing_paused",
                    "max_articles_per_month",
                    "health_status",
                    "consecutive_failures",
                    "circuit_open_until",
                    "last_success_at",
                )
            },
        ),
    )


@admin.register(SitePost)
class SitePostAdmin(admin.ModelAdmin):
    list_display = ("title", "site", "url", "published_at", "primary_keyword")
    list_filter = ("site",)
    search_fields = ("title", "remote_id")


@admin.register(SiteApiCall)
class SiteApiCallAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "site",
        "method",
        "path",
        "http_status",
        "error_code",
        "latency_ms",
    )
    list_filter = ("site", "method", "http_status")
    readonly_fields = [f.name for f in SiteApiCall._meta.fields]


@admin.register(PublishAttempt)
class PublishAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "article",
        "site",
        "attempt_number",
        "http_status",
        "error_code",
        "dry_run",
        "succeeded",
    )
    list_filter = ("site", "succeeded", "dry_run", "error_code")
    readonly_fields = [f.name for f in PublishAttempt._meta.fields]


@admin.register(PublicationSchedule)
class PublicationScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "site",
        "mode",
        "max_per_day",
        "buffer_threshold",
        "qa_consumes_slot",
        "is_active",
    )
    list_filter = ("mode", "is_active", "qa_consumes_slot")

    fieldsets = (
        (None, {"fields": ("site", "mode", "is_active")}),
        (
            _("Quando publicar"),
            {
                "fields": ("weekdays", "times_of_day", "interval_days", "max_per_day"),
                "description": _(
                    "Dias da semana: 0 = segunda, 6 = domingo. Horarios no formato "
                    "'HH:MM', no fuso do site — que vem do cadastro do site, nunca "
                    "duplicado aqui."
                ),
            },
        ),
        (
            _("Reserva"),
            {
                "fields": ("buffer_threshold", "qa_consumes_slot"),
                "description": _(
                    "Se a resposta a pergunta nao ocupar horario, o site publica mais "
                    "do que o configurado e o calculo da reserva fica errado."
                ),
            },
        ),
    )


@admin.register(PublicationSlot)
class PublicationSlotAdmin(admin.ModelAdmin):
    list_display = ("slot_at", "local_slot_at", "site", "article", "answer", "filled_at")
    list_filter = ("site",)
    date_hierarchy = "slot_at"
    readonly_fields = ("created_at",)


@admin.register(AuthorPhotoDelivery)
class AuthorPhotoDeliveryAdmin(admin.ModelAdmin):
    """Segunda etapa do envio do autor: quem ja recebeu a foto e quem nao."""

    list_display = ("author", "site", "status", "attempts", "delivered_at")
    list_filter = ("status", "site")
    search_fields = ("author__name", "photo_sha256", "remote_job_id")
    readonly_fields = ("photo_sha256", "remote_job_id", "created_at", "delivered_at")
