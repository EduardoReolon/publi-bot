from django.urls import path

from apps.estudos import views

app_name = "estudos"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("<uuid:pk>/", views.estudo, name="estudo"),
    path("<uuid:pk>/palavra/", views.palavra, name="palavra"),
]
