"""Rotas de conteudo, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.content import views

app_name = "content"

urlpatterns = [
    path("pautas/", views.pautas, name="pautas"),
    path("pautas/nova/", views.nova_pauta, name="nova_pauta"),
    path("pautas/<uuid:pk>/gerar/", views.gerar, name="gerar"),
    path("pautas/<uuid:pk>/rejeitar/", views.rejeitar_pauta, name="rejeitar_pauta"),
    path("artigos/", views.artigos, name="artigos"),
    path("artigos/<uuid:pk>/", views.revisar, name="revisar"),
    path("artigos/<uuid:pk>/secoes/", views.salvar_secoes, name="salvar_secoes"),
    path("artigos/<uuid:pk>/refazer/", views.refazer_secoes, name="refazer_secoes"),
    path("artigos/<uuid:pk>/replanejar/", views.replanejar, name="replanejar"),
    path("perguntas/", views.perguntas, name="perguntas"),
    path("perguntas/<uuid:pk>/responder/", views.responder, name="responder"),
    path("perguntas/<uuid:pk>/descartar/", views.descartar_pergunta, name="descartar_pergunta"),
    path("respostas/<uuid:pk>/", views.revisar_resposta, name="revisar_resposta"),
]
