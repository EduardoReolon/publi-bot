from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.knowledge.models import Document, DocumentCategory, RetrievalQuery, SuperChunk


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


class SuperChunkInline(admin.TabularInline):
    model = SuperChunk
    extra = 0
    fields = ("kind", "content", "token_count", "embedding_model", "is_active")
    readonly_fields = ("token_count", "embedding_model")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "authors",
        "year",
        "language",
        "license",
        "status",
        "curation_seconds",
    )
    list_filter = ("status", "license", "metadata_confidence", "category", "language")
    search_fields = ("title", "authors", "doi", "file_sha256")
    readonly_fields = ("file_sha256", "file_size_bytes", "content_fingerprint", "created_at")
    inlines = [SuperChunkInline]

    fieldsets = (
        (None, {"fields": ("category", "original_file", "status", "failure_reason")}),
        (
            _("Bibliografia"),
            {
                "fields": (
                    "title",
                    "authors",
                    "authors_raw",
                    "year",
                    "doi",
                    "source_url",
                    "language",
                    "metadata_confidence",
                    "authority_score",
                )
            },
        ),
        (
            _("Direitos"),
            {
                "fields": ("license", "rights_confirmed_by", "rights_confirmed_at"),
                "description": _(
                    "Documentos proprietarios ou de licenca desconhecida perdem o "
                    "texto integral apos a curadoria: o trecho citado permanece, a "
                    "copia completa nao."
                ),
            },
        ),
        (_("Conteudo"), {"fields": ("markdown_full",), "classes": ("collapse",)}),
        (
            _("Curadoria"),
            {"fields": ("uploaded_by", "reviewed_by", "reviewed_at", "curation_seconds")},
        ),
        (
            _("Tecnico"),
            {
                "fields": ("file_sha256", "file_size_bytes", "content_fingerprint", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(SuperChunk)
class SuperChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "kind", "token_count", "embedding_model", "is_active")
    list_filter = ("kind", "is_active", "embedding_model")
    search_fields = ("content", "source_title")


@admin.register(RetrievalQuery)
class RetrievalQueryAdmin(admin.ModelAdmin):
    list_display = ("query_text", "origin", "top_k", "max_distance", "created_at")
    list_filter = ("origin",)
    readonly_fields = ("query_text", "origin", "top_k", "max_distance", "embedding_model")
