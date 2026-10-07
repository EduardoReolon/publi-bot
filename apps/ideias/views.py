"""A caixa de ideias: mandar (texto, audio, links) e acompanhar."""

from __future__ import annotations

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.ideias.models import Ideia

MAXIMO_DE_LINKS = 5


def pendencias() -> dict:
    """No menu: ideias esperando a curadoria das fontes."""
    return {"ideias": Ideia.objects.filter(situacao=Ideia.Situacao.CURADORIA).count()}


def _disparar(ideia: Ideia) -> None:
    from apps.ideias.tasks import processar_ideia

    pk = str(ideia.pk)
    transaction.on_commit(lambda: processar_ideia.delay(pk))


def _links(texto: str) -> list[str]:
    links = []
    for pedaco in (texto or "").split():
        if pedaco.startswith(("http://", "https://")) and pedaco not in links:
            links.append(pedaco[:500])
    return links[:MAXIMO_DE_LINKS]


@login_required
def inicio(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        texto = request.POST.get("texto", "").strip()
        audio = request.FILES.get("audio")
        if not (texto or audio):
            messages.error(request, _("Escreva a ideia ou grave um audio."))
            return redirect("ideias:inicio")
        ideia = Ideia.objects.create(
            texto=texto[:20000],
            links=_links(request.POST.get("links", "")),
            criada_por=request.user,
        )
        if audio is not None:
            ideia.audio.save(audio.name[-80:], ContentFile(audio.read()), save=True)
        _disparar(ideia)
        messages.success(
            request,
            _(
                "Ideia guardada. Em segundo plano o PubliBot le, monta a pauta e busca o que "
                "se diz, o que sustenta e o que contradiz a sua ideia; volte quando quiser."
            ),
        )
        return redirect("ideias:inicio")
    ideias = list(Ideia.objects.select_related("pauta")[:100])
    from apps.ideias.investigacao import fontes_por_frente

    for ideia in ideias:
        ideia.frentes = fontes_por_frente(ideia)
    return render(
        request,
        "ideias/inicio.html",
        {
            "aba": "ideias",
            "ideias": ideias,
            "redes": _destinos_das_redes(),
        },
    )


def _destinos_das_redes() -> list:
    if not apps.is_installed("apps.social"):
        return []
    from apps.social.models import Destino

    return list(Destino.objects.filter(ligado=True)) or list(Destino.objects.all())


@login_required
@require_POST
def acao(request: HttpRequest, pk) -> HttpResponse:
    ideia = get_object_or_404(Ideia, pk=pk)
    qual = request.POST.get("acao")
    if qual == "de_novo":
        ideia.situacao = Ideia.Situacao.NOVA
        ideia.save(update_fields=["situacao", "atualizada_em"])
        _disparar(ideia)
        messages.info(request, _("Lendo e buscando de novo, em segundo plano."))
    elif qual == "descartar":
        ideia.situacao = Ideia.Situacao.DESCARTADA
        ideia.save(update_fields=["situacao", "atualizada_em"])
        messages.info(request, _("Ideia descartada (a pauta, se houver, continua em Pautas)."))
    elif qual == "redes" and apps.is_installed("apps.social"):
        from apps.ideias.investigacao import material_para_as_redes
        from apps.social.proprio import EntradaInvalida, criar_comentario

        leitura = ideia.leitura or {}
        texto = "\n".join(
            x
            for x in [
                f"O que se diz: {leitura.get('afirmacao', '')}",
                f"O que eu suspeito: {leitura.get('tese', '')}",
                ideia.relato,
            ]
            if x.strip()
        )
        try:
            criar_comentario(
                texto=texto,
                referencias=material_para_as_redes(ideia),
                destinos=request.POST.getlist("destinos"),
                por=request.user,
            )
            messages.success(request, _("Os posts estao sendo escritos (Redes > Para revisar)."))
        except EntradaInvalida as exc:
            messages.error(request, str(exc))
    return redirect(f"{reverse('ideias:inicio')}#ideia-{ideia.pk}")
