"""Tela do radar: configuracao, contas, grupos de demanda, buscas e custos."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.radar import custos
from apps.radar.concorrentes import sugeridos_para_a_tela
from apps.radar.forms import BuscaManualForm, ConfiguracaoForm, ContasForm
from apps.radar.models import (
    BuscaManual,
    ChamadaExterna,
    ComparacaoDeBusca,
    ConfiguracaoDoRadar,
    ContasExternas,
    GrupoDeDemanda,
    LocalDisponivel,
    RodadaDoRadar,
    SementeSugerida,
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
    resumo = custos.resumo_do_mes()
    teto = float(resumo["teto"] or 0)
    return {
        "aba": "radar",
        "subaba": "radar",
        "uso_do_teto": min(100, round(100 * float(resumo["gasto"]) / teto)) if teto else 0,
        "config": config or ConfiguracaoForm(instance=config_obj),
        "contas": contas or ContasForm(instance=contas_obj),
        "contas_obj": contas_obj,
        "config_obj": config_obj,
        "tem_lista_de_locais": LocalDisponivel.objects.exists(),
        "buscador_efetivo": ConfiguracaoDoRadar.Buscador(
            buscador_efetivo(config_obj, contas_obj)
        ).label,
        "busca_form": busca_form or BuscaManualForm(),
        "resumo": resumo,
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
        "concorrentes_sugeridos": sugeridos_para_a_tela(),
        "sementes_sugeridas": SementeSugerida.objects.filter(
            situacao=SementeSugerida.Situacao.SUGERIDA
        ).order_by("tipo", "origem", "-criada_em")[:40],
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
def configuracao(request: HttpRequest) -> HttpResponse:
    return render(request, "radar/configuracao.html", {**_contexto(), "subaba": "configuracao"})


@login_required
@require_POST
def salvar_configuracao(request: HttpRequest) -> HttpResponse:
    form = ConfiguracaoForm(request.POST, instance=ConfiguracaoDoRadar.carregar())
    if not form.is_valid():
        return render(
            request,
            "radar/configuracao.html",
            {**_contexto(config=form), "subaba": "configuracao"},
            status=400,
        )
    form.save()
    messages.success(request, _("Configuracao do radar salva."))
    return redirect("radar:configuracao")


@login_required
@require_POST
def salvar_contas(request: HttpRequest) -> HttpResponse:
    form = ContasForm(request.POST, instance=ContasExternas.carregar())
    if not form.is_valid():
        return render(
            request,
            "radar/configuracao.html",
            {**_contexto(contas=form), "subaba": "configuracao"},
            status=400,
        )
    form.save()
    messages.success(request, _("Contas salvas."))
    return redirect("radar:configuracao")


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
    criadas = propor_pautas({grupo.pk}, limite=1, nota_minima=0, pedido_pela_pessoa=True)
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
def procurar_locais(request: HttpRequest) -> JsonResponse:
    """O seletor de regioes procura aqui, a cada tecla."""
    from apps.radar.locais import procurar

    return JsonResponse({"locais": procurar(request.GET.get("q", ""))})


@login_required
@require_POST
def atualizar_locais(request: HttpRequest) -> HttpResponse:
    """Baixa a lista de locais da DataForSEO (rota gratuita)."""
    from apps.radar.locais import atualizar_lista
    from apps.radar.provedores import ProvedorIndisponivel

    try:
        total = atualizar_lista(ContasExternas.carregar())
    except ProvedorIndisponivel as exc:
        messages.error(request, _("Nao foi possivel baixar a lista de locais: %(e)s") % {"e": exc})
    else:
        messages.success(request, _("%(n)s locais disponiveis para escolher.") % {"n": total})
    return redirect("radar:configuracao")


@login_required
@require_POST
def decidir_concorrente(request: HttpRequest, pk) -> HttpResponse:
    """Confirmar poe o dominio na lista de concorrentes; recusar o esquece."""
    from apps.radar.concorrentes import confirmar
    from apps.radar.models import ConcorrenteSugerido

    sugerido = get_object_or_404(ConcorrenteSugerido, pk=pk)
    if request.POST.get("decisao") == "confirmar":
        confirmar(sugerido, nome=request.POST.get("nome", "")[:200])
        messages.success(
            request, _("%(d)s entrou na lista de concorrentes.") % {"d": sugerido.dominio}
        )
    else:
        sugerido.situacao = ConcorrenteSugerido.Situacao.RECUSADO
        sugerido.save(update_fields=["situacao"])
        messages.success(request, _("%(d)s nao sera mais sugerido.") % {"d": sugerido.dominio})
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
        from apps.radar.atualizacoes import atualizar_sugestoes

        atualizar_sugestoes()
        messages.success(
            request,
            _("Search Console coletado: %(n)s linhas de %(i)s a %(f)s.")
            % {"n": coleta.linhas, "i": coleta.inicio, "f": coleta.fim},
        )
    return redirect("radar:radar")


# ---------------------------------------------------------------------------
# Oportunidades
# ---------------------------------------------------------------------------
@login_required
def oportunidades(request: HttpRequest) -> HttpResponse:
    from apps.radar.models import Oportunidade

    config = ConfiguracaoDoRadar.carregar()
    ver = request.GET.get("ver", "nova")
    situacoes = {s.value for s in Oportunidade.Situacao}
    ver = ver if ver in situacoes else "nova"
    return render(
        request,
        "radar/oportunidades.html",
        {
            "aba": "radar",
            "subaba": "oportunidades",
            "dores": config.lista_de_dores,
            "ver": ver,
            "situacoes": Oportunidade.Situacao.choices,
            "contagens": {s: Oportunidade.objects.filter(situacao=s).count() for s in situacoes},
            "oportunidades": Oportunidade.objects.filter(situacao=ver)
            .select_related("grupo")
            .prefetch_related("grupo__sinais")
            .order_by("-nota")[:50],
            "tem_dores": bool(config.lista_de_dores),
        },
    )


@login_required
@require_POST
def decidir_oportunidade(request: HttpRequest, pk) -> HttpResponse:
    """Arquivar, virar semente (o radar passa a buscar o tema) ou testar com
    um artigo (vira pauta sugerida; o Search Console mede o interesse)."""
    from apps.radar.coleta import propor_pautas
    from apps.radar.models import Oportunidade

    oportunidade = get_object_or_404(Oportunidade.objects.select_related("grupo"), pk=pk)
    decisao = request.POST.get("decisao")
    tema = oportunidade.grupo.rotulo
    if decisao == "semente":
        config = ConfiguracaoDoRadar.carregar()
        if tema.lower() not in {s.lower() for s in config.lista_de_sementes}:
            config.sementes = (config.sementes.rstrip() + "\n" + tema[:200]).strip()
            config.save(update_fields=["sementes"])
        oportunidade.situacao = Oportunidade.Situacao.ACOMPANHANDO
        messages.success(request, _("'%(t)s' virou palavra-semente.") % {"t": tema})
    elif decisao == "testar":
        criadas = propor_pautas(
            {oportunidade.grupo_id}, limite=1, nota_minima=0, pedido_pela_pessoa=True
        )
        if not criadas:
            messages.error(request, _("Nao virou pauta: o tema esta perto do que ja foi escrito."))
            return redirect("radar:oportunidades")
        oportunidade.situacao = Oportunidade.Situacao.EM_TESTE
        messages.success(
            request,
            _(
                "Pauta criada: %(t)s. Publicado o artigo, o Search Console mostra em "
                "algumas semanas se ha interesse."
            )
            % {"t": criadas[0]},
        )
    elif decisao == "arquivar":
        oportunidade.situacao = Oportunidade.Situacao.ARQUIVADA
        messages.success(request, _("Oportunidade arquivada."))
    elif decisao == "reabrir":
        oportunidade.situacao = Oportunidade.Situacao.NOVA
    oportunidade.save(update_fields=["situacao", "atualizada_em"])
    return redirect("radar:oportunidades")


@login_required
@require_POST
def descrever_oportunidade(request: HttpRequest, pk) -> HttpResponse:
    from django.db import transaction

    from apps.radar.models import Oportunidade
    from apps.radar.tasks import descrever_uma_oportunidade

    oportunidade = get_object_or_404(Oportunidade, pk=pk)
    transaction.on_commit(lambda: descrever_uma_oportunidade.delay(str(oportunidade.pk)))
    messages.success(
        request,
        _(
            "Descricao pedida ao modelo. Aparece aqui quando ficar pronta; com a "
            "placa ocupada ou fora do ar, a tentativa se repete de hora em hora."
        ),
    )
    return redirect("radar:oportunidades")


# ---------------------------------------------------------------------------
# Sementes sugeridas
# ---------------------------------------------------------------------------
@login_required
@require_POST
def sugerir_sementes(request: HttpRequest) -> HttpResponse:
    from django.db import transaction

    from apps.radar.tasks import sugerir_sementes as tarefa

    transaction.on_commit(lambda: tarefa.delay())
    messages.success(
        request,
        _(
            "Sugestoes pedidas. As da pagina do site aparecem em instantes; as do "
            "modelo de linguagem, quando a placa estiver livre."
        ),
    )
    return redirect("radar:radar")


@login_required
@require_POST
def decidir_semente_sugerida(request: HttpRequest, pk) -> HttpResponse:
    from apps.radar.models import SementeSugerida
    from apps.radar.sugestoes import aceitar

    sugestao = get_object_or_404(SementeSugerida, pk=pk)
    if request.POST.get("decisao") == "aceitar":
        aceitar(sugestao)
    else:
        sugestao.situacao = SementeSugerida.Situacao.RECUSADA
        sugestao.save(update_fields=["situacao"])
    return redirect(request.POST.get("voltar") or "radar:radar")


# ---------------------------------------------------------------------------
# Atualizacoes
# ---------------------------------------------------------------------------
@login_required
def atualizacoes(request: HttpRequest) -> HttpResponse:
    from apps.radar.models import SugestaoDeAtualizacao

    ver = request.GET.get("ver", "aberta")
    ver = ver if ver in {s.value for s in SugestaoDeAtualizacao.Situacao} else "aberta"
    return render(
        request,
        "radar/atualizacoes.html",
        {
            "aba": "radar",
            "subaba": "atualizacoes",
            "ver": ver,
            "situacoes": SugestaoDeAtualizacao.Situacao.choices,
            "sugestoes": SugestaoDeAtualizacao.objects.filter(situacao=ver)
            .select_related("artigo")
            .order_by("-prioridade")[:60],
        },
    )


@login_required
@require_POST
def recalcular_atualizacoes(request: HttpRequest) -> HttpResponse:
    from apps.radar.atualizacoes import atualizar_sugestoes

    novas = atualizar_sugestoes()
    messages.success(request, _("%(n)s sugestao(oes) nova(s).") % {"n": novas})
    return redirect("radar:atualizacoes")


@login_required
@require_POST
def decidir_atualizacao(request: HttpRequest, pk) -> HttpResponse:
    from django.utils import timezone

    from apps.radar.models import SugestaoDeAtualizacao

    sugestao = get_object_or_404(SugestaoDeAtualizacao, pk=pk)
    decisao = request.POST.get("decisao")
    if decisao == "versao" and sugestao.artigo is not None:
        # Atualizar no PubliBot: cria a versao nova com o motivo anotado. A
        # sugestao so e dada como feita quando a versao for ao ar.
        from apps.content.versoes import (
            VersaoRecusada,
            criar_nova_versao,
            notas_da_sugestao,
            trocar_fontes_substituidas,
        )

        artigo = sugestao.artigo
        # O artigo da sugestao pode ja ter sido substituido: parte da versao no ar.
        while artigo.next_versions.filter(status="published").exists():
            artigo = artigo.next_versions.filter(status="published").latest("created_at")
        try:
            nova = criar_nova_versao(artigo, notas=notas_da_sugestao(sugestao), por=request.user)
        except VersaoRecusada as exc:
            messages.error(request, str(exc))
            return redirect("radar:atualizacoes")
        if sugestao.tipo == SugestaoDeAtualizacao.Tipo.FONTE_VENCIDA:
            trocadas = trocar_fontes_substituidas(nova)
            if trocadas:
                messages.info(
                    request,
                    _("%(n)s citacao(oes) passaram para a fonte nova. Confira os numeros.")
                    % {"n": trocadas},
                )
        sugestao.situacao = SugestaoDeAtualizacao.Situacao.FEITA
        sugestao.decidida_em = timezone.now()
        sugestao.save(update_fields=["situacao", "decidida_em", "atualizada_em"])
        return redirect("content:revisar", pk=nova.pk)
    if decisao == "feita":
        sugestao.situacao = SugestaoDeAtualizacao.Situacao.FEITA
    elif decisao == "dispensar":
        sugestao.situacao = SugestaoDeAtualizacao.Situacao.DISPENSADA
    else:
        sugestao.situacao = SugestaoDeAtualizacao.Situacao.ABERTA
    sugestao.decidida_em = timezone.now()
    sugestao.save(update_fields=["situacao", "decidida_em", "atualizada_em"])
    return redirect("radar:atualizacoes")
