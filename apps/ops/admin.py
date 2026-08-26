from __future__ import annotations

from django.contrib import admin

from apps.ops.models import GenerationJob, InferenceLog


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "kind",
        "status",
        "current_step",
        "total_steps",
        "attempt_count",
        "next_attempt_at",
        "created_at",
    )
    list_filter = ("kind", "status")
    readonly_fields = (
        "kind",
        "target_object_id",
        "step_payloads",
        "idempotency_key",
        "lease_token",
        "lease_expires_at",
        "created_at",
        "finished_at",
    )
    search_fields = ("id", "target_object_id")


@admin.register(InferenceLog)
class InferenceLogAdmin(admin.ModelAdmin):
    list_display = (
        "model_name",
        "workload",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "cost_brl",
        "succeeded",
        "created_at",
    )
    list_filter = ("workload", "succeeded", "model_name")
    readonly_fields = [f.name for f in InferenceLog._meta.fields]
