"""Rotas do acervo, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.knowledge import views

app_name = "knowledge"

urlpatterns = [
    path("", views.documentos, name="documentos"),
    path("apagar-nao-curados/", views.apagar_nao_curados, name="apagar_nao_curados"),
    path("enviar/", views.enviar_documento, name="enviar"),
    path("enviar/url/", views.enviar_url, name="enviar_url"),
    path("nota/", views.nota_do_especialista, name="nota"),
    path(
        "<uuid:pk>/vetorizar-no-servidor/",
        views.vetorizar_no_servidor,
        name="vetorizar_no_servidor",
    ),
    path("fontes-sugeridas/", views.fontes_sugeridas, name="fontes_sugeridas"),
    path("fontes-sugeridas/<uuid:pk>/", views.decidir_candidato, name="decidir_candidato"),
    path("fontes-sugeridas/<uuid:pk>/pdf/", views.enviar_pdf, name="enviar_pdf"),
    path(
        "fontes-sugeridas/<uuid:pk>/sem-pdf/",
        views.seguir_com_o_resumo,
        name="seguir_com_o_resumo",
    ),
    path(
        "fontes-sugeridas/<uuid:pk>/voltar/",
        views.voltar_a_sugestao,
        name="voltar_a_sugestao",
    ),
    path(
        "fontes-sugeridas/<uuid:pk>/texto/",
        views.capturar_texto_do_candidato,
        name="capturar_texto_do_candidato",
    ),
    path("fontes-sugeridas/<uuid:pk>/audio/", views.enviar_audio, name="enviar_audio"),
    path("caminhos/", views.caminhos_confiaveis, name="caminhos"),
    path("categorias/", views.categorias, name="categorias"),
    path("busca/", views.qualidade_da_busca, name="busca"),
    path("<uuid:pk>/", views.curar_documento, name="curar"),
    path("<uuid:pk>/arquivo/", views.baixar_original, name="baixar_original"),
    path("<uuid:pk>/markdown/", views.baixar_markdown, name="baixar_markdown"),
    path("<uuid:pk>/reprocessar/", views.reprocessar, name="reprocessar"),
    path("<uuid:pk>/excluir/", views.excluir_documento, name="excluir"),
    path("<uuid:pk>/recusar/", views.recusar_fonte, name="recusar_fonte"),
    path("<uuid:pk>/marcar/", views.marcar_extracao, name="marcar_extracao"),
]
