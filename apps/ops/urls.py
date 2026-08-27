"""Rotas de operacao."""

from __future__ import annotations

from django.urls import path

from apps.ops import views

app_name = "ops"

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("readyz/", views.readyz, name="readyz"),
]
