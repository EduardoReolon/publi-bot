"""Tela do guia editorial."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.editorial.forms import PerfilDoNegocioForm, PerfilEditorialForm
from apps.editorial.models import EditorialProfile, PerfilDoNegocio
from apps.editorial.presets import MODOS
from apps.editorial.services import aplicar_modo


@login_required
def guia(request: HttpRequest) -> HttpResponse:
    perfil = EditorialProfile.carregar()

    if request.method == "POST":
        form = PerfilEditorialForm(request.POST, instance=perfil)
        if form.is_valid():
            form.save()
            messages.success(request, _("Guia editorial salvo. Vale a partir da proxima geracao."))
            return redirect("editorial:guia")
    else:
        form = PerfilEditorialForm(instance=perfil)

    return render(
        request,
        "editorial/guia.html",
        {
            "aba": "editorial",
            "perfil": perfil,
            "form": form,
            "modos": MODOS,
            "url_negocio": reverse("editorial:negocio"),
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def aplicar(request: HttpRequest) -> HttpResponse:
    """Troca o guia inteiro pelo ponto de partida de um modo.

    Sobrescreve voz, termos e regra de ouro. As estruturas e os exemplos ficam:
    sao trabalho do cliente, e nao mudam com o tipo de negocio.
    """
    perfil = EditorialProfile.carregar()
    try:
        aplicar_modo(perfil, request.POST.get("modo", ""))
    except ValueError:
        messages.error(request, _("Modo desconhecido."))
        return redirect("editorial:guia")
    perfil.save()
    messages.success(
        request,
        _("Ponto de partida aplicado: %(modo)s. Ajuste o que quiser.")
        % {"modo": perfil.get_mode_display()},
    )
    return redirect("editorial:guia")


@login_required
def negocio(request: HttpRequest) -> HttpResponse:
    """O que o site e e o que vende: a referencia de tudo que o PubliBot mede."""
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = PerfilDoNegocio.carregar()
    config = ConfiguracaoDoRadar.carregar()
    form = PerfilDoNegocioForm(request.POST or None, instance=perfil, config=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Negocio salvo. Vale para a proxima rodada e geracao."))
        return redirect("editorial:negocio")
    return render(
        request,
        "editorial/negocio.html",
        {
            "aba": "negocio",
            "form": form,
            "sementes": config.lista_de_sementes,
            "regioes": [nome for _codigo, nome in config.locais() if nome],
            "concorrentes": config.lista_de_concorrentes,
        },
        status=400 if request.method == "POST" else 200,
    )
