"""A caixa de ideias: mandar (texto, audio, links) e acompanhar."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
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
        pela_outra_ia = request.POST.get("modo") == "outra_ia"
        if not (texto or audio):
            messages.error(request, _("Escreva a ideia ou grave um audio."))
            return redirect("ideias:inicio")
        if pela_outra_ia and not texto:
            messages.error(
                request,
                _("Para preparar com outra IA, escreva a ideia (o audio vai pelo modelo daqui)."),
            )
            return redirect("ideias:inicio")
        ideia = Ideia.objects.create(
            texto=texto[:20000],
            links=_links(request.POST.get("links", "")),
            criada_por=request.user,
            situacao=Ideia.Situacao.OUTRA_IA if pela_outra_ia else Ideia.Situacao.NOVA,
        )
        if audio is not None:
            ideia.audio.save(audio.name[-80:], ContentFile(audio.read()), save=True)
        if pela_outra_ia:
            messages.info(
                request,
                _("Copie o pedido abaixo, cole numa IA grande e traga a resposta de volta."),
            )
            return redirect(f"{reverse('ideias:inicio')}#ideia-{ideia.pk}")
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
    from apps.ideias.investigacao import fontes_por_frente, pedido_para_outra_ia

    for ideia in ideias:
        ideia.frentes = fontes_por_frente(ideia)
        ideia.pedido = (
            pedido_para_outra_ia(ideia) if ideia.situacao == Ideia.Situacao.OUTRA_IA else ""
        )
    return render(
        request,
        "ideias/inicio.html",
        {
            "aba": "ideias",
            "ideias": ideias,
            "redes": _destinos_das_redes(),
        },
    )


def _destinos_das_redes() -> list[tuple[str, str]]:
    from apps.ops.extensoes import contas_das_redes

    return contas_das_redes()


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
    elif qual == "outra_ia":
        ideia.situacao = Ideia.Situacao.OUTRA_IA
        ideia.save(update_fields=["situacao", "atualizada_em"])
    elif qual == "resposta":
        from apps.ideias.investigacao import LeituraInvalida, aplicar_resposta
        from apps.ideias.tasks import buscar_ideia

        try:
            aplicar_resposta(ideia, request.POST.get("resposta", ""))
        except LeituraInvalida as exc:
            messages.error(request, _("Nao deu para ler a resposta: %(e)s") % {"e": exc})
        else:
            ideia.situacao = Ideia.Situacao.BUSCANDO
            ideia.save(update_fields=["situacao", "atualizada_em"])
            pk = str(ideia.pk)
            transaction.on_commit(lambda: buscar_ideia.delay(pk))
            messages.success(
                request,
                _("Resposta lida: a pauta nasce e as frentes sao buscadas em segundo plano."),
            )
    elif qual == "descartar":
        ideia.situacao = Ideia.Situacao.DESCARTADA
        ideia.save(update_fields=["situacao", "atualizada_em"])
        messages.info(request, _("Ideia descartada (a pauta, se houver, continua em Pautas)."))
    elif qual == "redes":
        from apps.ideias.investigacao import material_para_as_redes
        from apps.ops.extensoes import comentar_nas_redes

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
            comentar_nas_redes(
                texto=texto,
                referencias=material_para_as_redes(ideia),
                destinos=request.POST.getlist("destinos"),
                por=request.user,
            )
            messages.success(request, _("Os posts estao sendo escritos (Redes > Para revisar)."))
        except (ValueError, LookupError) as exc:
            messages.error(request, str(exc))
    return redirect(f"{reverse('ideias:inicio')}#ideia-{ideia.pk}")


@login_required
@require_POST
def investigar_pauta(request: HttpRequest, pk) -> HttpResponse:
    """O modo investigativo numa pauta que ja existe: a mesma leitura por frentes
    da caixa de ideias, sobre a pauta (titulo e orientacao) e o que o revisor
    quer investigar. A pauta ganha o debate; o titulo dela nao muda."""
    from apps.content.models import Topic

    pauta = get_object_or_404(Topic, pk=pk)
    pedido = request.POST.get("texto", "").strip()
    texto = "\n".join(
        x
        for x in [
            f"Pauta: {pauta.title}",
            f"Orientacao da pauta: {pauta.briefing}" if pauta.briefing else "",
            f"O que o revisor quer investigar: {pedido}" if pedido else "",
        ]
        if x
    )
    ideia = Ideia.objects.create(
        texto=texto[:20000], links=_links(pedido), pauta=pauta, criada_por=request.user
    )
    _disparar(ideia)
    messages.success(
        request,
        _(
            "Modo investigativo ligado: em segundo plano o PubliBot monta as frentes desta "
            "pauta e busca as fontes de cada uma para a sua curadoria (acompanhe em Ideias)."
        ),
    )
    voltar = request.POST.get("voltar", "")
    if voltar.startswith("/") and not voltar.startswith("//"):
        return redirect(voltar)
    return redirect("content:pauta", pk=pauta.pk)


@login_required
def dossie(request: HttpRequest, pk) -> HttpResponse:
    """O pedido de veredito, em texto, para copiar (carregado so ao abrir)."""
    from apps.content.models import Topic
    from apps.ideias.veredito import dossie as montar

    pauta = get_object_or_404(Topic, pk=pk)
    return HttpResponse(montar(pauta), content_type="text/plain; charset=utf-8")


@login_required
@require_POST
def capturar_textos(request: HttpRequest, pk) -> HttpResponse:
    from apps.content.models import Topic
    from apps.ideias.tasks import capturar_textos_da_pauta

    pauta = get_object_or_404(Topic, pk=pk)
    pk_da_pauta = str(pauta.pk)
    transaction.on_commit(lambda: capturar_textos_da_pauta.delay(pk_da_pauta))
    detalhe = _("Capturando os textos em segundo plano; abra o dossie de novo daqui a pouco.")
    if request.headers.get("X-Em-Segundo-Plano"):
        return JsonResponse({"detalhe": detalhe})
    messages.info(request, detalhe)
    return redirect(f"{reverse('content:pauta', args=[pauta.pk])}#investigar")
