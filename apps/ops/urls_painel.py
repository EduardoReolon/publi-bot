"""Rotas da tela de operacao, dentro de um tenant.

Separadas de `urls.py`, que so tem as sondas de saude: aquelas respondem sem
autenticacao e antes da resolucao de tenant, estas exigem sessao.
"""

from __future__ import annotations

from django.urls import path

from apps.ops import views_painel

app_name = "operacao"

urlpatterns = [
    path("", views_painel.trabalhos, name="trabalhos"),
    path("<uuid:pk>/", views_painel.detalhe_do_trabalho, name="trabalho"),
    path("<uuid:pk>/redespachar/", views_painel.redespachar, name="redespachar"),
]
