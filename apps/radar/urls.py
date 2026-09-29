"""Rotas do radar, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.radar import views

app_name = "radar"

urlpatterns = [
    path("", views.radar, name="radar"),
    path("configuracao/", views.configuracao, name="configuracao"),
    path("configuracao/salvar/", views.salvar_configuracao, name="salvar_configuracao"),
    path("sementes-cientificas/", views.colar_sementes_cientificas, name="sementes_cientificas"),
    path("artigos/buscar/", views.buscar_artigos_agora, name="buscar_artigos_agora"),
    path("contas/", views.salvar_contas, name="salvar_contas"),
    path("rodar/", views.rodar_agora, name="rodar_agora"),
    path("buscar/", views.busca_manual, name="busca_manual"),
    path("buscas/<uuid:pk>/decidir/", views.decidir_busca, name="decidir_busca"),
    path("grupos/<uuid:pk>/pauta/", views.grupo_para_pauta, name="grupo_para_pauta"),
    path("grupos/<uuid:pk>/descartar/", views.descartar_grupo, name="descartar_grupo"),
    path("grupos/descartar-ruins/", views.descartar_ruins, name="descartar_ruins"),
    path("resposta-da-ia/", views.revisar_resposta_ia, name="revisar_resposta_ia"),
    path("resposta-da-ia/aplicar/", views.aplicar_resposta_ia, name="aplicar_resposta_ia"),
    path(
        "concorrentes/<uuid:pk>/decidir/",
        views.decidir_concorrente,
        name="decidir_concorrente",
    ),
    path("locais/", views.procurar_locais, name="procurar_locais"),
    path("locais/atualizar/", views.atualizar_locais, name="atualizar_locais"),
    path("oportunidades/", views.oportunidades, name="oportunidades"),
    path(
        "oportunidades/arquivar-ruins/", views.arquivar_ruins, name="arquivar_oportunidades_ruins"
    ),
    path("imprensa/", views.imprensa, name="imprensa"),
    path("imprensa/<uuid:pk>/contatado/", views.veiculo_contatado, name="veiculo_contatado"),
    path("links-quebrados/<uuid:pk>/", views.decidir_link_quebrado, name="decidir_link_quebrado"),
    path(
        "oportunidades/<uuid:pk>/decidir/",
        views.decidir_oportunidade,
        name="decidir_oportunidade",
    ),
    path(
        "oportunidades/<uuid:pk>/descrever/",
        views.descrever_oportunidade,
        name="descrever_oportunidade",
    ),
    path("sementes/sugerir/", views.sugerir_sementes, name="sugerir_sementes"),
    path(
        "sementes/<uuid:pk>/decidir/",
        views.decidir_semente_sugerida,
        name="decidir_semente_sugerida",
    ),
    path("atualizacoes/", views.atualizacoes, name="atualizacoes"),
    path(
        "atualizacoes/recalcular/",
        views.recalcular_atualizacoes,
        name="recalcular_atualizacoes",
    ),
    path(
        "atualizacoes/<uuid:pk>/decidir/",
        views.decidir_atualizacao,
        name="decidir_atualizacao",
    ),
    path("search-console/", views.coletar_console, name="coletar_console"),
]
