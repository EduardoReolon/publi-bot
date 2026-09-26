"""Tela do radar: configuracao, contas, grupos de demanda, buscas e custos."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.radar import custos
from apps.radar.forms import BuscaManualForm, ConfiguracaoForm, ContasForm
from apps.radar.models import (
    BuscaManual,
    ChamadaExterna,
    ComparacaoDeBusca,
    ConfiguracaoDoRadar,
    ContasExternas,
    GrupoDeDemanda,
    RodadaDoRadar,
    SinalDeDemanda,
    TarefaNaFila,
)
from apps.radar.provedores import buscador_efetivo


def _contexto(config=None, contas=None, busca_form=None) -> dict:
    config_obj = ConfiguracaoDoRadar.carregar()
    contas_obj = ContasExternas.carregar()
    comparacoes = ComparacaoDeBusca.objects.order_by("-criado_em")[:30]
    medias = ComparacaoDeBusca.objects.filter(
        pk__in=[c.pk for c in comparacoes], gratuito_falhou=False
    ).aggregate(urls=Avg("sobreposicao_urls"), dominios=Avg("sobreposicao_dominios"))
    return {
        "aba": "radar",
        "config": config or ConfiguracaoForm(instance=config_obj),
        "contas": contas or ContasForm(instance=contas_obj),
        "contas_obj": contas_obj,
        "config_obj": config_obj,
        "buscador_efetivo": ConfiguracaoDoRadar.Buscador(
            buscador_efetivo(config_obj, contas_obj)
        ).label,
        "busca_form": busca_form or BuscaManualForm(),
        "resumo": custos.resumo_do_mes(),
        "grupos": GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.NOVO)
        .annotate(total=Count("sinais", filter=~Q(sinais__situacao="descartado")))
        .order_by("-nota")[:30],
        "rodadas": RodadaDoRadar.objects.order_by("-iniciada_em")[:8],
        "buscas_pendentes": BuscaManual.objects.filter(decisao=BuscaManual.Decisao.PENDENTE)
        .prefetch_related("sinais")
        .order_by("-criado_em")[:10],
        "comparacoes": comparacoes[:10],
        "comparacao_media": medias,
        "comparacoes_total": len(comparacoes),
        "comparacoes_falhas": sum(1 for c in comparacoes if c.gratuito_falhou),
        "chamadas": ChamadaExterna.objects.order_by("-criado_em")[:15],
        "tarefas_na_fila": TarefaNaFila.objects.filter(
            situacao=TarefaNaFila.Situacao.AGUARDANDO
        ).count(),
        **_contexto_do_console(),
    }


def _contexto_do_console() -> dict:
    from apps.radar.models import ColetaDoConsole
    from apps.radar.search_console import desempenho_dos_artigos, email_da_conta, quase_la

    coleta = ColetaDoConsole.objects.order_by("-coletada_em").first()
    return {
        "console_email": email_da_conta(),
        "console_coleta": coleta,
        "console_quase_la": list(quase_la(coleta)[:20]) if coleta else [],
        "console_artigos": desempenho_dos_artigos() if coleta else [],
    }


@login_required
def radar(request: HttpRequest) -> HttpResponse:
    return render(request, "radar/radar.html", _contexto())


@login_required
@require_POST
def salvar_configuracao(request: HttpRequest) -> HttpResponse:
    form = ConfiguracaoForm(request.POST, instance=ConfiguracaoDoRadar.carregar())
    if not form.is_valid():
        return render(request, "radar/radar.html", _contexto(config=form), status=400)
    form.save()
    messages.success(request, _("Configuracao do radar salva."))
    return redirect("radar:radar")


@login_required
@require_POST
def salvar_contas(request: HttpRequest) -> HttpResponse:
    form = ContasForm(request.POST, instance=ContasExternas.carregar())
    if not form.is_valid():
        return render(request, "radar/radar.html", _contexto(contas=form), status=400)
    form.save()
    messages.success(request, _("Contas salvas."))
    return redirect("radar:radar")


@login_required
@require_POST
def rodar_agora(request: HttpRequest) -> HttpResponse:
    from django.db import transaction

    from apps.radar.tasks import rodar_radar

    if RodadaDoRadar.objects.filter(situacao=RodadaDoRadar.Situacao.AGUARDANDO).exists():
        messages.warning(
            request,
            _(
                "Ja ha uma rodada aguardando os resultados da fila da DataForSEO. "
                "Ela termina sozinha em alguns minutos."
            ),
        )
        return redirect("radar:radar")
    transaction.on_commit(lambda: rodar_radar.delay())
    messages.success(
        request,
        _("Rodada do radar enfileirada. As pautas sugeridas aparecem em Pautas quando terminar."),
    )
    return redirect("radar:radar")


@login_required
@require_POST
def busca_manual(request: HttpRequest) -> HttpResponse:
    from apps.radar.coleta import busca_manual as executar

    form = BuscaManualForm(request.POST)
    if not form.is_valid():
        return render(request, "radar/radar.html", _contexto(busca_form=form), status=400)
    busca = executar(form.cleaned_data["consulta"], com_volume=form.cleaned_data["com_volume"])
    if busca.erro:
        messages.error(request, _("A busca nao completou: %(erro)s") % {"erro": busca.erro})
    else:
        messages.success(
            request,
            _("Busca feita: %(n)s sinal(is). Decida abaixo se e do seu segmento.")
            % {"n": busca.sinais.count()},
        )
    return redirect("radar:radar")


@login_required
@require_POST
def decidir_busca(request: HttpRequest, pk) -> HttpResponse:
    from apps.radar.coleta import decidir_busca as decidir

    busca = get_object_or_404(BuscaManual, pk=pk, decisao=BuscaManual.Decisao.PENDENTE)
    guardar = request.POST.get("decisao") == "guardar"
    decidir(busca, guardar=guardar)
    messages.success(
        request,
        _("Guardada no radar.") if guardar else _("Descartada. O custo continua registrado."),
    )
    return redirect("radar:radar")


@login_required
@require_POST
def grupo_para_pauta(request: HttpRequest, pk) -> HttpResponse:
    """Transforma um grupo em pauta sugerida, sem esperar a proxima rodada."""
    from apps.radar.coleta import propor_pautas

    grupo = get_object_or_404(GrupoDeDemanda, pk=pk, situacao=GrupoDeDemanda.Situacao.NOVO)
    # Pedido pela pessoa: sem nota minima. A trava de canibalizacao continua.
    criadas = propor_pautas({grupo.pk}, limite=1, nota_minima=0)
    if criadas:
        messages.success(request, _("Pauta sugerida criada: %(t)s") % {"t": criadas[0]})
    else:
        messages.error(
            request, _("Nao virou pauta: o tema esta perto demais do que ja foi escrito.")
        )
    return redirect("radar:radar")


@login_required
@require_POST
def descartar_grupo(request: HttpRequest, pk) -> HttpResponse:
    grupo = get_object_or_404(GrupoDeDemanda, pk=pk)
    grupo.situacao = GrupoDeDemanda.Situacao.DESCARTADO
    grupo.save(update_fields=["situacao", "atualizado_em"])
    grupo.sinais.update(situacao=SinalDeDemanda.Situacao.DESCARTADO)
    messages.success(request, _("Grupo descartado."))
    return redirect("radar:radar")


@login_required
@require_POST
def coletar_console(request: HttpRequest) -> HttpResponse:
    """Tira um retrato do Search Console agora."""
    from apps.radar.provedores import ProvedorIndisponivel
    from apps.radar.search_console import coletar

    try:
        coleta = coletar()
    except ProvedorIndisponivel as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            _("Search Console coletado: %(n)s linhas de %(i)s a %(f)s.")
            % {"n": coleta.linhas, "i": coleta.inicio, "f": coleta.fim},
        )
    return redirect("radar:radar")
