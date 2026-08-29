"""Rotas do contrato. Inclua sob o prefixo `api/v1/`."""

from __future__ import annotations

from django.urls import path

from publibot_node import views

app_name = "publibot_node"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("seo-context/", views.seo_context, name="seo_context"),
    path("publish/", views.publish, name="publish"),
    path("author-photos/", views.author_photos, name="author_photos"),
    path("pending-questions/", views.pending_questions, name="pending_questions"),
    path("pending-questions/ack/", views.acknowledge_questions, name="acknowledge_questions"),
    path("publications/", views.publications, name="publications"),
]
