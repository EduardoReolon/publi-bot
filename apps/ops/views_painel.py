"""Tela de operacao: o que rodou, o que travou e o que falhou."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.ops.models import GenerationJob, InferenceLog

logger = logging.getLogger("publibot.ops")


@login_required
def trabalhos(request: HttpRequest) -> HttpResponse:
    situacao = request.GET.get("situacao", "")
    consulta = GenerationJob.objects.order_by("-updated_at")
    if situacao:
        consulta = consulta.filter(status=situacao)

    from apps.content.fluxos import situacao_da_pesquisa
    from apps.content.models import Topic
    from apps.integrations.models import PublishAttempt

    pesquisas = []
    for pauta in Topic.objects.filter(
        busca_de_fontes__pesquisa__situacao__in=["na_fila", "pesquisando"]
    ):
        pesquisas.append({"pauta": pauta, **situacao_da_pesquisa(pauta)})

    return render(
        request,
        "ops/trabalhos.html",
        {
            "aba": "operacao",
            "trabalhos": consulta[:100],
            "pesquisas": pesquisas,
            "situacao": situacao,
            "situacoes": GenerationJob.Status.choices,
            "inferencias": InferenceLog.objects.select_related("connection").order_by(
                "-created_at"
            )[:20],
            "tentativas": PublishAttempt.objects.select_related(
                "article", "answer__question"
            ).order_by("-created_at")[:20],
        },
    )


@login_required
@require_POST
def redespachar(request: HttpRequest, pk) -> HttpResponse:
    """Devolve um trabalho falhado a fila, do passo em que parou.

    Nao reinicia do zero: `step_payloads` guarda o que ja foi feito, e refazer
    um passo concluido pagaria a inferencia duas vezes.
    """
    from apps.ops.orchestrator import devolver_a_fila

    job = get_object_or_404(GenerationJob, pk=pk)

    if job.status not in (GenerationJob.Status.FAILED, GenerationJob.Status.WAITING_CAPACITY):
        messages.error(
            request, _("So trabalhos falhados ou aguardando capacidade sao redespachados.")
        )
        return redirect("operacao:trabalhos")

    devolver_a_fila(job)
    messages.success(
        request, _("Trabalho devolvido a fila, do passo %(n)s.") % {"n": job.current_step}
    )
    return redirect("operacao:trabalhos")


@login_required
def detalhe_do_trabalho(request: HttpRequest, pk) -> HttpResponse:
    job = get_object_or_404(GenerationJob, pk=pk)
    fluxo = None
    try:
        from apps.ops.orchestrator import obter_fluxo

        fluxo = obter_fluxo(job.kind)
    except KeyError:
        pass

    return render(
        request,
        "ops/trabalho.html",
        {
            "aba": "operacao",
            "trabalho": job,
            "passos": fluxo.passos if fluxo else [],
            "inferencias": job.logs.order_by("created_at"),
        },
    )
