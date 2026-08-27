"""Rotas de um tenant — `<slug>.<ROOT_DOMAIN>`.

Este e o urlconf padrao (ROOT_URLCONF). O django-tenants o utiliza sempre que
o host resolve para um tenant, com o search_path ja fixado no schema dele.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sondas de saude, sem autenticacao de proposito: o orquestrador e o
    # balanceador precisam alcanca-las antes de qualquer sessao existir.
    path("", include("apps.ops.urls", namespace="ops")),
    path("", include("apps.accounts.urls_tenant", namespace="accounts")),
]
