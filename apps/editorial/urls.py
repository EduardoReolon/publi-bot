"""Rotas do guia editorial, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.editorial import views

app_name = "editorial"

urlpatterns = [
    path("", views.guia, name="guia"),
    path("modo/", views.aplicar, name="aplicar_modo"),
    path("conselhos/", views.adicionar_conselhos, name="conselhos"),
    path("negocio/", views.negocio, name="negocio"),
]
