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
from django.utils.translation import gettext_lazy
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
            "grupos": _grupos(ideias),
            "redes": _destinos_das_redes(),
        },
    )


GRUPOS = [
    ("andamento", gettext_lazy("Em andamento"), True),
    ("curadoria", gettext_lazy("Esperando a sua curadoria"), True),
    ("aprovadas", gettext_lazy("Aprovadas"), False),
    ("pautas", gettext_lazy("Viraram pauta"), False),
    ("descartadas", gettext_lazy("Descartadas"), False),
]


def _grupo(ideia: Ideia) -> str:
    S = Ideia.Situacao
    if ideia.situacao == S.CURADORIA:
        return "curadoria"
    if ideia.situacao == S.APROVADA:
        return "aprovadas"
    if ideia.situacao == S.PAUTA:
        return "pautas"
    if ideia.situacao == S.DESCARTADA:
        return "descartadas"
    return "andamento"  # na fila, lendo, buscando, outra IA, erro


def _grupos(ideias: list) -> list[dict]:
    por_grupo: dict = {}
    for ideia in ideias:
        por_grupo.setdefault(_grupo(ideia), []).append(ideia)
    return [
        {"chave": chave, "titulo": titulo, "aberto": aberto, "ideias": por_grupo[chave]}
        for chave, titulo, aberto in GRUPOS
        if por_grupo.get(chave)
    ]


def _destinos_das_redes() -> list[tuple[str, str]]:
    from apps.ops.extensoes import contas_das_redes

    return contas_das_redes()


@login_required
@require_POST
def acao(request: HttpRequest, pk) -> HttpResponse:
    ideia = get_object_or_404(Ideia, pk=pk)
    qual = request.POST.get("acao")
    if qual == "de_novo":
        # Com frentes ja definidas, e refinamento: elas ficam (e sao buscadas de novo).
        ideia.refino = {"pedido": "", "buscar_existentes": True}
        ideia.situacao = Ideia.Situacao.NOVA
        ideia.save(update_fields=["situacao", "refino", "atualizada_em"])
        _disparar(ideia)
        messages.info(request, _("Lendo e buscando de novo, em segundo plano."))
    elif qual == "aprovar":
        from apps.ideias.investigacao import aprovar

        aprovar(ideia)
        messages.success(
            request,
            _(
                "Ideia aprovada: pode ir para as redes (na escolha do dia, se ligada, "
                "ou agora, em Levar as redes)."
            ),
        )
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
        pauta = ideia.pauta
        if pauta is not None and request.POST.get("rejeitar_pauta") and not pauta.articles.exists():
            from apps.content.models import Topic

            pauta.status = Topic.Status.REJECTED
            pauta.save(update_fields=["status"])
            messages.info(request, _("Ideia descartada e a pauta dela rejeitada."))
        else:
            messages.info(request, _("Ideia descartada (a pauta, se houver, continua em Pautas)."))
    elif qual == "redes":
        from apps.ideias.investigacao import material_para_as_redes, texto_para_as_redes
        from apps.ops.extensoes import comentar_nas_redes

        try:
            comentar_nas_redes(
                texto=texto_para_as_redes(ideia),
                referencias=material_para_as_redes(ideia),
                destinos=request.POST.getlist("destinos"),
                por=request.user,
                origem=f"ideia:{ideia.pk}",
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
    voltar = request.POST.get("voltar", "")
    destino = (
        redirect(voltar)
        if voltar.startswith("/") and not voltar.startswith("//")
        else redirect("content:pauta", pk=pauta.pk)
    )
    # Ja investigada: e REFINAMENTO da mesma ideia. As frentes ficam (as fontes
    # estao ligadas a elas); o texto pode corrigir o que se diz e a suspeita,
    # melhorar as frentes e acrescentar novas. Mudar as frentes: ideia nova.
    existente = next((i for i in pauta.ideias.all() if (i.leitura or {}).get("frentes")), None)
    if existente is not None:
        existente.refino = {
            "pedido": pedido[:5000],
            "buscar_existentes": bool(request.POST.get("buscar_existentes")),
        }
        existente.links = list(dict.fromkeys([*(existente.links or []), *_links(pedido)]))[
            : MAXIMO_DE_LINKS * 2
        ]
        existente.situacao = Ideia.Situacao.NOVA
        existente.save(update_fields=["refino", "links", "situacao", "atualizada_em"])
        _disparar(existente)
        messages.success(
            request,
            _(
                "Refinando em segundo plano: as frentes ficam; so as novas (ou todas, se "
                "voce marcou) sao buscadas."
            ),
        )
        return destino
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
    return destino


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


@login_required
@require_POST
def colar_veredito(request: HttpRequest, pk) -> HttpResponse:
    """A resposta da outra IA ao pedido de veredito: guarda o veredito e aplica o
    refinamento (frentes, textos a capturar, sugestoes de curadoria)."""
    from apps.content.models import Topic
    from apps.ideias.tasks import depois_do_veredito
    from apps.ideias.veredito import VereditoInvalido, aplicar_veredito

    pauta = get_object_or_404(Topic, pk=pk)
    destino = redirect(f"{reverse('content:pauta', args=[pauta.pk])}#veredito")
    try:
        resumo = aplicar_veredito(pauta, request.POST.get("resposta", ""))
    except VereditoInvalido as exc:
        messages.error(request, _("Nao deu para ler a resposta: %(e)s") % {"e": exc})
        return destino
    ideia_pk, capturar = resumo["ideia"], resumo["capturar"]
    transaction.on_commit(lambda: depois_do_veredito.delay(ideia_pk, capturar))
    partes = []
    if resumo["frentes_novas"]:
        partes.append(_("frentes novas: %(f)s") % {"f": ", ".join(resumo["frentes_novas"])})
    if resumo["rebuscar"]:
        partes.append(_("buscar de novo: %(f)s") % {"f": ", ".join(resumo["rebuscar"])})
    if capturar:
        partes.append(_("%(n)s fonte(s) para capturar o texto") % {"n": len(capturar)})
    if resumo["sugestoes"]:
        partes.append(
            _("%(n)s sugestao(oes) de curadoria nos cartoes") % {"n": resumo["sugestoes"]}
        )
    messages.success(
        request,
        _("Veredito guardado. %(o)s. O resto roda em segundo plano.")
        % {"o": "; ".join(partes) or _("nada a refinar")},
    )
    return destino
