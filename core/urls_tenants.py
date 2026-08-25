"""Rotas de um tenant — `<slug>.<ROOT_DOMAIN>`.

Este e o urlconf padrao (ROOT_URLCONF). O django-tenants o utiliza sempre que
o host resolve para um tenant, ja com o search_path fixado no schema dele.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
