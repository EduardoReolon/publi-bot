from django.urls import path

from apps.social import views

app_name = "social"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("posts/<uuid:pk>/", views.acao_no_post, name="acao_no_post"),
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
    # Publicos, sem login.
    path("r/<str:chave>/", views.clique, name="clique"),
    path("m/<str:chave>/<int:n>.png", views.imagem, name="imagem"),
    path("bio/<str:chave>/", views.bio, name="bio"),
]
