"""Rotas do dominio raiz."""

from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views
from apps.accounts.forms import LoginForm

app_name = "accounts"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("cadastro/", views.signup, name="signup"),
    # Exigidas pelas redes (Meta, LinkedIn, Google) para liberar o app.
    path("privacidade/", views.privacidade, name="privacidade"),
    path("termos/", views.termos, name="termos"),
    path("exclusao-de-dados/", views.exclusao_de_dados, name="exclusao_de_dados"),
    path("exclusao-de-dados/meta/", views.meta_exclusao, name="meta_exclusao"),
    path("desautorizar/meta/", views.meta_desautorizar, name="meta_desautorizar"),
    # O retorno unico das conexoes OAuth (core/retorno_oauth.py).
    path("redes/retorno/", views.retorno_oauth, name="retorno_oauth"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=LoginForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("preparando/<slug:slug>/", views.provisioning, name="provisioning"),
    path(
        "preparando/<slug:slug>/status/",
        views.provisioning_status,
        name="provisioning_status",
    ),
]
