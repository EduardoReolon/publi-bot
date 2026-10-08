from django.urls import path

from apps.social import views

app_name = "social"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("estrategia/", views.estrategia, name="estrategia"),
    path("estrategia/acao/", views.acao_na_estrategia, name="acao_na_estrategia"),
    path("estrategia/ia/", views.revisar_resposta_ia, name="revisar_resposta_ia"),
    path("estrategia/ia/aplicar/", views.aplicar_resposta_ia, name="aplicar_resposta_ia"),
    path("diagnostico/", views.diagnostico, name="diagnostico"),
    path("novo/", views.novo_post, name="novo_post"),
    path("fotos/", views.banco_de_fotos, name="banco_de_fotos"),
    path("fotos/<uuid:pk>/", views.midia_privada, name="midia_privada"),
    path("diagnostico/acao/", views.acao_no_diagnostico, name="acao_no_diagnostico"),
    path("posts/<uuid:pk>/", views.acao_no_post, name="acao_no_post"),
    path("posts/<uuid:pk>/previa/", views.previa_da_lamina, name="previa_da_lamina"),
    path("posts/<uuid:pk>/lamina/", views.editar_lamina, name="editar_lamina"),
    path("posts/<uuid:pk>/lamina/foto/", views.foto_da_lamina, name="foto_da_lamina"),
    path("fonte-da-lamina/<str:peso>/", views.fonte_da_lamina, name="fonte_da_lamina"),
    path("artigo/<uuid:artigo_id>/levar/", views.levar_as_redes, name="levar_as_redes"),
    path("configurar/", views.configurar, name="configurar"),
    path("contas/nova/", views.salvar_destino, name="novo_destino"),
    path("contas/<uuid:pk>/", views.salvar_destino, name="salvar_destino"),
    path("contas/<uuid:pk>/conectar/", views.conectar, name="conectar"),
    path("contas/<uuid:pk>/conta/", views.escolher_conta, name="escolher_conta"),
    path("contas/<uuid:pk>/desconectar/", views.desconectar, name="desconectar"),
    path("conectar/retorno/", views.retorno, name="retorno"),
    path("abordagens/nova/", views.salvar_abordagem, name="nova_abordagem"),
    path("abordagens/<uuid:pk>/", views.salvar_abordagem, name="salvar_abordagem"),
    path("recados/novo/", views.salvar_recado, name="novo_recado"),
    path("recados/<uuid:pk>/", views.salvar_recado, name="salvar_recado"),
    # Publicos, sem login.
    path("r/<str:chave>/", views.clique, name="clique"),
    path("m/<str:chave>/<int:n>.png", views.imagem, name="imagem"),
    path("m/<str:chave>/<int:n>/", views.imagem, name="midia_publica"),
    path("bio/<str:chave>/", views.bio, name="bio"),
]
