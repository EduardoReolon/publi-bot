"""Rotas do contrato. Inclua sob o prefixo `api/v1/`."""

from __future__ import annotations

from django.urls import path

from publibot_node import medicao, views

app_name = "publibot_node"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("seo-context/", views.seo_context, name="seo_context"),
    path("publish/", views.publish, name="publish"),
    path("author-photos/", views.author_photos, name="author_photos"),
    path("pending-questions/", views.pending_questions, name="pending_questions"),
    path("pending-questions/ack/", views.acknowledge_questions, name="acknowledge_questions"),
    path("publications/", views.publications, name="publications"),
    path("publications/<str:remote_id>/", views.update_publication, name="update_publication"),
    path("insights/", views.insights, name="insights"),
    # Do navegador do leitor para o SEU servidor — nao fazem parte do contrato
    # com o PubliBot, e por isso nao tem assinatura. Ver `medicao.py`.
    path("leitura/", medicao.leitura, name="leitura"),
    path("conversao/", medicao.conversao, name="conversao"),
]
