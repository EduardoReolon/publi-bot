"""Rotas de conteudo, dentro de um tenant."""

from __future__ import annotations

from django.urls import path

from apps.content import views

app_name = "content"

urlpatterns = [
    path("pautas/", views.pautas, name="pautas"),
    path("pautas/nova/", views.nova_pauta, name="nova_pauta"),
    path("pautas/<uuid:pk>/", views.pauta, name="pauta"),
    path("pautas/<uuid:pk>/gerar/", views.gerar, name="gerar"),
    path("pautas/<uuid:pk>/dados/", views.dados_da_pauta, name="dados_da_pauta"),
    path("pautas/<uuid:pk>/pdfs/", views.pdfs_da_pesquisa, name="pdfs_da_pesquisa"),
    path(
        "pautas/<uuid:pk>/pdfs/<uuid:candidato>/enviar/",
        views.enviar_pdf_da_pesquisa,
        name="enviar_pdf_da_pesquisa",
    ),
    path(
        "pautas/<uuid:pk>/desistir/<uuid:trabalho>/",
        views.desistir_da_geracao,
        name="desistir_da_geracao",
    ),
    path("pautas/<uuid:pk>/rejeitar/", views.rejeitar_pauta, name="rejeitar_pauta"),
    path("pautas/<uuid:pk>/fontes/", views.buscar_fontes, name="buscar_fontes"),
    path("pautas/<uuid:pk>/pesquisar/", views.pesquisar_artigos, name="pesquisar_artigos"),
    path(
        "pautas/<uuid:pk>/pesquisa/<uuid:candidato>/pdf/",
        views.pdf_da_pesquisa,
        name="pdf_da_pesquisa",
    ),
    path(
        "pautas/<uuid:pk>/referencias/",
        views.conferir_referencias,
        name="conferir_referencias",
    ),
    path(
        "pautas/<uuid:pk>/ignorar-artigos/",
        views.ignorar_falta_de_artigos,
        name="ignorar_falta_de_artigos",
    ),
    path("pautas/<uuid:pk>/intencao/", views.intencao_da_pauta, name="intencao_da_pauta"),
    path("pautas/<uuid:pk>/outra-ia/", views.artigo_por_outra_ia, name="artigo_por_outra_ia"),
    path("pautas/<uuid:pk>/imprensa/", views.imprensa_da_pauta, name="imprensa_da_pauta"),
    path("artigos/", views.artigos, name="artigos"),
    path("artigos/desempenho/", views.desempenho, name="desempenho"),
    path("artigos/<uuid:pk>/", views.revisar, name="revisar"),
    path("artigos/<uuid:pk>/versao/", views.nova_versao, name="nova_versao"),
    path("artigos/<uuid:pk>/secoes/", views.salvar_secoes, name="salvar_secoes"),
    path("artigos/<uuid:pk>/refazer/", views.refazer_secoes, name="refazer_secoes"),
    path("artigos/<uuid:pk>/replanejar/", views.replanejar, name="replanejar"),
    path("artigos/<uuid:pk>/faq/", views.salvar_faq, name="salvar_faq"),
    path("artigos/<uuid:pk>/chamada/", views.mudar_chamada, name="mudar_chamada"),
    path("artigos/<uuid:pk>/chamada/texto/", views.texto_da_chamada, name="texto_da_chamada"),
    path("artigos/<uuid:pk>/citacoes/aceitar/", views.aceitar_sem_fonte, name="aceitar_sem_fonte"),
    path("artigos/<uuid:pk>/gerar-de-novo/", views.gerar_de_novo, name="gerar_de_novo"),
    path("artigos/<uuid:pk>/indexacao/", views.conferir_indexacao, name="conferir_indexacao"),
    path("artigos/<uuid:pk>/capas/", views.gerar_capas, name="gerar_capas"),
    path("artigos/<uuid:pk>/capas/escolher/", views.escolher_capa, name="escolher_capa"),
    # Sem sessao: quem busca e o site de destino, do outro lado da internet.
    path("capas/<uuid:pk>.webp", views.capa_publica, name="capa_publica"),
    path("autores/", views.autores, name="autores"),
    path("autores/novo/", views.editar_autor, name="novo_autor"),
    path("autores/<uuid:pk>/", views.editar_autor, name="editar_autor"),
    path("autores/<uuid:pk>/excluir/", views.excluir_autor, name="excluir_autor"),
    path("perguntas/", views.perguntas, name="perguntas"),
    path("perguntas/<uuid:pk>/responder/", views.responder, name="responder"),
    path("perguntas/<uuid:pk>/responder-a-mao/", views.responder_a_mao, name="responder_a_mao"),
    path("perguntas/<uuid:pk>/descartar/", views.descartar_pergunta, name="descartar_pergunta"),
    path("respostas/<uuid:pk>/", views.revisar_resposta, name="revisar_resposta"),
]
