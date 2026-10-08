from django.urls import path

from apps.ideias import views

app_name = "ideias"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("<uuid:pk>/", views.acao, name="acao"),
    path("<uuid:pk>/pedido/", views.pedido, name="pedido"),
    path("pauta/<uuid:pk>/investigar/", views.investigar_pauta, name="investigar_pauta"),
    path("pauta/<uuid:pk>/dossie/", views.dossie, name="dossie"),
    path("pauta/<uuid:pk>/veredito/", views.colar_veredito, name="colar_veredito"),
    path("pauta/<uuid:pk>/sugestoes/", views.aplicar_sugestoes, name="aplicar_sugestoes"),
    path("pauta/<uuid:pk>/capturar-textos/", views.capturar_textos, name="capturar_textos"),
]
