from django.urls import path

from apps.dados import views

app_name = "dados"

urlpatterns = [
    path("", views.catalogo, name="catalogo"),
    path("series/nova/", views.nova_serie, name="nova_serie"),
    path("series/<uuid:pk>/situacao/", views.situacao_da_serie, name="situacao_da_serie"),
    path("series/lote/", views.situacao_em_lote, name="situacao_em_lote"),
    path("instituicoes/<int:pk>/confiavel/", views.confiavel, name="confiavel"),
    path("instituicoes/<int:pk>/procurar/", views.procurar, name="procurar"),
    path("pedidos/<int:pk>/", views.situacao_do_pedido, name="situacao_do_pedido"),
]
