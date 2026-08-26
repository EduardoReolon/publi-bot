"""Roteamento usado apenas pelo teste de contrato ponta a ponta.

Monta as rotas do no de referencia na raiz, como estariam no site de um cliente.
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("publibot_node.urls", namespace="publibot_node")),
]
