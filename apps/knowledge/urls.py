"""Rotas do acervo, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.knowledge import views

app_name = "knowledge"

urlpatterns = [
    path("", views.documentos, name="documentos"),
    path("enviar/", views.enviar_documento, name="enviar"),
    path("categorias/", views.categorias, name="categorias"),
    path("<uuid:pk>/", views.curar_documento, name="curar"),
    path("<uuid:pk>/reprocessar/", views.reprocessar, name="reprocessar"),
    path("<uuid:pk>/trecho/<uuid:chunk_pk>/remover/", views.remover_trecho, name="remover_trecho"),
]
