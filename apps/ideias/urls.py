from django.urls import path

from apps.ideias import views

app_name = "ideias"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("<uuid:pk>/", views.acao, name="acao"),
    path("pauta/<uuid:pk>/investigar/", views.investigar_pauta, name="investigar_pauta"),
]
