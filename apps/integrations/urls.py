"""Rotas do site de destino, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.integrations import views

app_name = "integrations"

urlpatterns = [
    path("", views.site, name="site"),
    path("testar/", views.testar_conexao, name="testar"),
    path("horarios/gerar/", views.gerar_horarios, name="gerar_horarios"),
]
