"""Rotas do schema `public` — o dominio raiz.

Aqui ficam a landing page, o cadastro de novos tenants e a autenticacao. Um
tenant NAO e alcancavel por estas rotas: o django-tenants escolhe este urlconf
somente quando o host bate no dominio do schema public.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
