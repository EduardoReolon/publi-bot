from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.content.models import (
    Answer,
    AnswerCitation,
    Article,
    ArticleCitation,
    ArticleRevision,
    PromptRun,
    PromptTemplate,
    PromptVersion,
    Question,
    Topic,
)


class PromptVersionInline(admin.TabularInline):
    model = PromptVersion
    extra = 0
    fields = ("version", "variant", "model_name", "temperature", "is_active", "traffic_weight")


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "description")
    inlines = [PromptVersionInline]


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ("template", "version", "variant", "model_name", "is_active", "traffic_weight")
    list_filter = ("template__key", "is_active", "variant", "model_name")
    fieldsets = (
        (None, {"fields": ("template", "version", "variant", "is_active", "traffic_weight")}),
        (
            _("Prompt"),
            {
                "fields": ("system_prompt", "user_prompt_template", "variables"),
                "description": _(
                    "Conteudo de terceiros deve ficar sempre dentro de delimitadores, "
                    "e o prompt de sistema precisa declarar que o delimitado e dado a "
                    "analisar, nunca instrucao a obedecer."
                ),
            },
        ),
        (_("Modelo"), {"fields": ("model_name", "temperature", "max_tokens")}),
    )


@admin.register(PromptRun)
class PromptRunAdmin(admin.ModelAdmin):
    list_display = (
        "prompt_version",
        "human_verdict",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "created_at",
    )
    list_filter = ("human_verdict", "prompt_version__template__key")


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "target_keyword", "cannibalization_score", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "target_keyword")


class ArticleCitationInline(admin.TabularInline):
    model = ArticleCitation
    extra = 0
    fields = ("rank", "source_title", "source_url", "distance", "used_as_primary")
    readonly_fields = fields


class ArticleRevisionInline(admin.TabularInline):
    model = ArticleRevision
    extra = 0
    fields = ("version", "source", "editor", "created_at")
    readonly_fields = fields


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "consensus_com_alerta",
        "author_name",
        "human_edit_ratio",
        "review_seconds",
        "scheduled_for",
    )
    list_filter = ("status", "consensus", "single_source")
    search_fields = ("title", "focus_keyword", "remote_id")
    readonly_fields = (
        "idempotency_key",
        "remote_id",
        "published_url",
        "published_at",
        "publish_attempts",
        "last_publish_error",
        "word_count",
        "links_do_texto",
    )
    inlines = [ArticleCitationInline, ArticleRevisionInline]

    fieldsets = (
        (None, {"fields": ("topic", "title", "slug", "status")}),
        (
            _("Fundamentacao"),
            {
                "fields": (
                    "thesis_json",
                    "consensus",
                    "single_source",
                    "primary_source",
                    "outbound_link_url",
                    "anchor_text",
                    "links_do_texto",
                ),
                "description": _(
                    "Quando as fontes divergem, o texto nao pode apresentar o tema como "
                    "assentado. A aprovacao exige confirmacao explicita de como a "
                    "divergencia foi tratada."
                ),
            },
        ),
        (_("Conteudo"), {"fields": ("body_markdown", "body_html", "excerpt")}),
        (_("SEO"), {"fields": ("meta_description", "focus_keyword", "word_count")}),
        (
            _("Autoria e revisao"),
            {
                "fields": (
                    "author_name",
                    "author_credentials",
                    "reviewed_by",
                    "reviewed_at",
                    "review_seconds",
                    "human_edit_ratio",
                ),
                "description": _(
                    "Proporcao editada perto de zero indica que a revisao nao alterou o "
                    "texto do modelo."
                ),
            },
        ),
        (
            _("Publicacao"),
            {
                "fields": (
                    "scheduled_for",
                    "published_at",
                    "published_url",
                    "remote_id",
                    "idempotency_key",
                    "publish_attempts",
                    "last_publish_error",
                )
            },
        ),
    )

    @admin.display(description=_("Consenso"))
    def consensus_com_alerta(self, obj: Article):
        if obj.consensus == Article.Consensus.CONFLICT:
            return format_html(
                '<strong style="color:#c2334d">{}</strong>', obj.get_consensus_display()
            )
        return obj.get_consensus_display() or "—"

    @admin.display(description=_("Links do texto"))
    def links_do_texto(self, obj: Article):
        """Lista os destinos separadamente, com o dominio em destaque.

        O revisor precisa ver cada link como item proprio: no meio de um
        paragrafo, um destino trocado passa despercebido.
        """
        import re
        from urllib.parse import urlparse

        achados = re.findall(r'href="([^"]+)"', obj.body_html or "")
        if not achados:
            return _("nenhum")

        itens = []
        for url in dict.fromkeys(achados):
            anfitriao = urlparse(url).hostname or "?"
            itens.append(format_html("<li><strong>{}</strong> — {}</li>", anfitriao, url))
        return format_html("<ul>{}</ul>", format_html("".join(itens)))


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "site", "status", "best_similarity", "retention_until", "purged_at")
    list_filter = ("status", "site", "purged_at")
    readonly_fields = ("site", "remote_id", "submitted_at", "imported_at", "purged_at")

    fieldsets = (
        (None, {"fields": ("site", "remote_id", "status", "question_text")}),
        (
            _("Identificacao"),
            {
                "fields": ("author_pseudonym", "consent_at"),
                "description": _(
                    "O nome de quem perguntou nao e necessario para produzir o "
                    "conteudo. So e guardado com consentimento registrado no site de "
                    "origem, e ainda assim apenas o primeiro nome."
                ),
            },
        ),
        (
            _("Retencao"),
            {
                "fields": ("retention_until", "purged_at"),
                "description": _(
                    "Apos o prazo, o texto e a identificacao sao apagados. A linha "
                    "permanece: sem ela, a proxima coleta reimportaria a mesma "
                    "pergunta como se fosse nova."
                ),
            },
        ),
        (_("Recuperacao"), {"fields": ("best_similarity",)}),
        (_("Datas"), {"fields": ("submitted_at", "imported_at")}),
    )


class AnswerCitationInline(admin.TabularInline):
    model = AnswerCitation
    extra = 0
    fields = ("rank", "source_title", "source_url", "distance", "used_as_primary")
    readonly_fields = fields


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = (
        "question",
        "status",
        "author_name",
        "human_edit_ratio",
        "scheduled_for",
        "published_at",
    )
    list_filter = ("status",)
    inlines = [AnswerCitationInline]
    readonly_fields = (
        "idempotency_key",
        "remote_id",
        "published_url",
        "published_at",
        "publish_attempts",
        "last_publish_error",
    )
