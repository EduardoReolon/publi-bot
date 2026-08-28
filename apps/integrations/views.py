"""Telas do site de destino: credenciais, cadencia e horarios."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.integrations.forms import CadenciaForm, SiteForm
from apps.integrations.models import PublicationSlot, Site, SiteApiCall

logger = logging.getLogger("publibot.integrations")


@login_required
def site(request: HttpRequest) -> HttpResponse:
    """Cadastro e situacao do site de destino.

    Um tenant tem no maximo um site, por regra de produto (ADR-0003): um
    cliente com tres sites usa tres tenants. Por isso a tela e singular — nao
    ha lista.
    """
    instancia = Site.objects.first()

    if request.method == "POST":
        return _salvar_site(request, instancia)

    schedule = getattr(instancia, "schedule", None) if instancia else None

    return render(
        request,
        "integrations/site.html",
        {
            "aba": "site",
            "site": instancia,
            "form": SiteForm(instance=instancia),
            "cadencia": CadenciaForm(instance=schedule),
            "horarios": _proximos_horarios(instancia),
            "chamadas": SiteApiCall.objects.order_by("-created_at")[:20] if instancia else [],
            "alertas": _alertas(instancia),
        },
    )


def _alertas(instancia):
    from apps.ops.painel import alertas_do_site

    return alertas_do_site(instancia)


def _proximos_horarios(instancia):
    if instancia is None:
        return []
    return (
        PublicationSlot.objects.filter(site=instancia, slot_at__gte=timezone.now())
        .select_related("article", "answer")
        .order_by("slot_at")[:12]
    )


def _salvar_site(request: HttpRequest, instancia: Site | None) -> HttpResponse:
    secao = request.POST.get("secao", "site")

    if secao == "cadencia":
        return _salvar_cadencia(request, instancia)

    form = SiteForm(request.POST, instance=instancia)
    if not form.is_valid():
        schedule = getattr(instancia, "schedule", None) if instancia else None
        return render(
            request,
            "integrations/site.html",
            {
                "aba": "site",
                "site": instancia,
                "form": form,
                "cadencia": CadenciaForm(instance=schedule),
                "horarios": _proximos_horarios(instancia),
                "chamadas": [],
                "alertas": _alertas(instancia),
            },
            status=400,
        )

    form.save()
    messages.success(request, _("Site salvo."))
    return redirect("integrations:site")


def _salvar_cadencia(request: HttpRequest, instancia: Site | None) -> HttpResponse:
    if instancia is None:
        messages.error(request, _("Cadastre o site antes de configurar a cadencia."))
        return redirect("integrations:site")

    schedule = getattr(instancia, "schedule", None)
    form = CadenciaForm(request.POST, instance=schedule)
    if not form.is_valid():
        return render(
            request,
            "integrations/site.html",
            {
                "aba": "site",
                "site": instancia,
                "form": SiteForm(instance=instancia),
                "cadencia": form,
                "horarios": _proximos_horarios(instancia),
                "chamadas": [],
                "alertas": _alertas(instancia),
            },
            status=400,
        )

    cadencia = form.save(commit=False)
    cadencia.site = instancia
    cadencia.save()
    messages.success(request, _("Cadencia salva."))
    return redirect("integrations:site")


@login_required
@require_POST
def testar_conexao(request: HttpRequest) -> HttpResponse:
    """Bate no site com as credenciais cadastradas e relata o que voltou.

    Descobrir agora que a assinatura esta errada e barato; descobrir na
    primeira publicacao agendada significa um artigo aprovado parado sem que
    ninguem esteja olhando.
    """
    instancia = Site.objects.first()
    if instancia is None:
        messages.error(request, _("Nenhum site cadastrado."))
        return redirect("integrations:site")

    from apps.integrations.client import SiteClient

    try:
        cliente = SiteClient(instancia)
        contexto = cliente.seo_context()
    except Exception as exc:
        messages.error(request, _("Falhou: %(erro)s") % {"erro": exc})
        return redirect("integrations:site")

    posts = (contexto or {}).get("posts", []) if isinstance(contexto, dict) else []
    messages.success(
        request,
        _("Conexao ok. O site respondeu com %(total)s publicacao(oes) no espelho de SEO.")
        % {"total": len(posts)},
    )
    return redirect("integrations:site")


@login_required
@require_POST
def gerar_horarios(request: HttpRequest) -> HttpResponse:
    from apps.integrations.tasks import generate_publication_slots

    total = generate_publication_slots()
    messages.success(request, _("%(total)s horario(s) gerado(s).") % {"total": total})
    return redirect("integrations:site")
