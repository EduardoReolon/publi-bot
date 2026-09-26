"""Rotas do radar, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.radar import views

app_name = "radar"

urlpatterns = [
    path("", views.radar, name="radar"),
    path("configuracao/", views.salvar_configuracao, name="salvar_configuracao"),
    path("contas/", views.salvar_contas, name="salvar_contas"),
    path("rodar/", views.rodar_agora, name="rodar_agora"),
    path("buscar/", views.busca_manual, name="busca_manual"),
    path("buscas/<uuid:pk>/decidir/", views.decidir_busca, name="decidir_busca"),
    path("grupos/<uuid:pk>/pauta/", views.grupo_para_pauta, name="grupo_para_pauta"),
    path("grupos/<uuid:pk>/descartar/", views.descartar_grupo, name="descartar_grupo"),
]
