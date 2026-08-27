"""Rotas do schema `public` — o dominio raiz.

Aqui ficam a landing, o cadastro de novos tenants e a autenticacao. Um tenant
NAO e alcancavel por estas rotas: o django-tenants escolhe este urlconf apenas
quando o host bate no dominio do schema public.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sondas de saude, sem autenticacao de proposito: o orquestrador e o
    # balanceador precisam alcanca-las antes de qualquer sessao existir.
    path("", include("apps.ops.urls", namespace="ops")),
    path("", include("apps.accounts.urls_public", namespace="accounts")),
]
