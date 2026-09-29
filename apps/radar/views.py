"""Tela do radar: configuracao, contas, grupos de demanda, buscas e custos."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
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
from apps.radar.parceiros import parceiros_com_proposta
from apps.radar.provedores import buscador_efetivo


def _pagina(consulta, request, parametro: str, por_pagina: int):
    from django.core.paginator import Paginator

    numero = request.GET.get(parametro, 1) if request is not None else 1
    return Paginator(consulta, por_pagina).get_page(numero)


def _contexto(config=None, contas=None, busca_form=None, request=None) -> dict:
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
        "proxima_rodada": _proxima_rodada(config_obj),
        "resumo": resumo,
        "filtro_ia": _filtro_ia(request),
        "ruins_da_ia": GrupoDeDemanda.objects.filter(
            situacao=GrupoDeDemanda.Situacao.NOVO, avaliacao_ia="ruim"
        ).count(),
        "consulta_de_temas": _temas_em_observacao(request),
        "grupos": _pagina(
            _temas_em_observacao(request),
            request,
            "temas",
            30,
        ),
        "rodadas": _pagina(RodadaDoRadar.objects.order_by("-iniciada_em"), request, "rodadas", 10),
        "paginando": request is not None
        and any(p in request.GET for p in ("temas", "rodadas", "chamadas")),
        "buscas_pendentes": BuscaManual.objects.filter(decisao=BuscaManual.Decisao.PENDENTE)
        .prefetch_related("sinais")
        .order_by("-criado_em")[:10],
        "comparacoes": comparacoes[:10],
        "comparacao_media": medias,
        "comparacoes_total": len(comparacoes),
        "comparacoes_falhas": sum(1 for c in comparacoes if c.gratuito_falhou),
        "chamadas": _pagina(ChamadaExterna.objects.order_by("-criado_em"), request, "chamadas", 15),
        **_concorrentes_e_parceiros(),
        "sementes_sugeridas": SementeSugerida.objects.filter(
            situacao=SementeSugerida.Situacao.SUGERIDA
        ).order_by("tipo", "origem", "-criada_em")[:40],
        "tarefas_na_fila": TarefaNaFila.objects.filter(
            situacao=TarefaNaFila.Situacao.AGUARDANDO
        ).count(),
        **_contexto_do_console(),
    }


def _concorrentes_e_parceiros() -> dict:
    """Um site aparece numa lista so: parceiro provavel sai dos concorrentes."""
    from apps.radar.parceiros import parceiros_provaveis

    provaveis = parceiros_provaveis()
    ids = {p.pk for p in provaveis}
    return {
        "concorrentes_sugeridos": [c for c in sugeridos_para_a_tela() if c.pk not in ids],
        "parceiros": parceiros_com_proposta(),
        "parceiros_provaveis": provaveis,
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
    from apps.radar.autoridade import diagnosticar, resumo_dos_temas
    from apps.radar.dificuldade import do_grupo, estrategia_efetiva, mapa, prioridade
    from apps.radar.resumo import rendimento_das_sementes, texto_para_ia

    contexto = _contexto(request=request)
    dificuldades = mapa()
    autoridade = diagnosticar()
    estrategia, motivo = estrategia_efetiva(contexto["config_obj"], autoridade)
    ordem = request.GET.get("ordem", "")
    if ordem not in {"brechas", "nota"}:
        ordem = "brechas" if estrategia == "brechas" else "nota"
    if ordem == "brechas":
        # A dificuldade nao esta no banco (sai das paginas de resultado): para
        # ordenar por ela, a lista inteira e medida aqui.
        todos = list(contexto["consulta_de_temas"])
        for grupo in todos:
            grupo.dificuldade = do_grupo(grupo, dificuldades)
        todos.sort(key=lambda g: -prioridade(g.nota, g.dificuldade, "brechas"))
        contexto["grupos"] = _pagina(todos, request, "temas", 30)
    else:
        for grupo in contexto["grupos"]:
            grupo.dificuldade = do_grupo(grupo, dificuldades)
    return render(
        request,
        "radar/radar.html",
        {
            **contexto,
            "rendimento": rendimento_das_sementes(),
            "texto_para_ia": texto_para_ia(),
            "autoridade": autoridade,
            "estrategia": estrategia,
            "motivo_da_estrategia": motivo,
            "ordem_dos_temas": ordem,
            "temas_por_dificuldade": resumo_dos_temas(contexto["grupos"]),
            "mede_dificuldade": bool(dificuldades),
        },
    )


def _temas_em_observacao(request):
    return (
        _com_filtro_ia(
            GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.NOVO), request
        )
        .annotate(total=Count("sinais", filter=~Q(sinais__situacao="descartado")))
        .order_by("-nota")
    )


FILTROS_IA = {"boa", "ruim", "sem"}


def _filtro_ia(request) -> str:
    valor = request.GET.get("ia", "") if request is not None else ""
    return valor if valor in FILTROS_IA else ""


def _com_filtro_ia(consulta, request, campo: str = "avaliacao_ia"):
    """Filtra pela etiqueta que a outra IA deu (radar.revisao_ia)."""
    filtro = _filtro_ia(request)
    if not filtro:
        return consulta
    return consulta.filter(**{campo: "" if filtro == "sem" else filtro})


def _proxima_rodada(config) -> dict:
    """O que a proxima rodada vai buscar, pela mesma escolha que a rodada usa."""
    from apps.radar.coleta import _da_vez, _sementes_da_vez, plano_de

    plano = plano_de(config)
    return {
        "sementes": _sementes_da_vez(config, plano.buscas),
        "dores": _da_vez(config.lista_de_dores, plano.dores, chave="dores"),
    }


@login_required
def configuracao(request: HttpRequest) -> HttpResponse:
    from apps.knowledge.academicos import pedido_das_sementes_cientificas

    return render(
        request,
        "radar/configuracao.html",
        {
            **_contexto(request=request),
            "subaba": "configuracao",
            "pedido_cientifico": pedido_das_sementes_cientificas(),
            "busca_de_artigos": request.session.pop("busca_de_artigos", None),
        },
    )


@login_required
@require_POST
def colar_sementes_cientificas(request: HttpRequest) -> HttpResponse:
    """A resposta da outra IA troca a lista de sementes cientificas."""
    from apps.knowledge.academicos import ler_sementes_cientificas

    sementes = ler_sementes_cientificas(request.POST.get("resposta", ""))
    if not sementes:
        messages.error(request, _("Nao achei o bloco CIENTIFICAS na resposta colada."))
    else:
        config = ConfiguracaoDoRadar.carregar()
        config.sementes_cientificas = "\n".join(sementes)
        config.save(update_fields=["sementes_cientificas"])
        messages.success(
            request,
            _(
                "%(n)s semente(s) cientifica(s) salva(s). Use 'Buscar artigos agora' para "
                "ver o que cada uma traz."
            )
            % {"n": len(sementes)},
        )
    return redirect(reverse("radar:configuracao") + "#artigos-cientificos")


@login_required
@require_POST
def buscar_artigos_agora(request: HttpRequest) -> HttpResponse:
    """Busca as sementes cientificas no OpenAlex agora, sem esperar a rodada."""
    from apps.knowledge.academicos import (
        SEMENTES_POR_BUSCA_MANUAL,
        BaseIndisponivel,
        buscar_por_sementes,
    )

    sementes = ConfiguracaoDoRadar.carregar().lista_de_sementes_cientificas
    if not sementes:
        messages.error(request, _("Cadastre as sementes cientificas antes."))
        return redirect(reverse("radar:configuracao") + "#artigos-cientificos")
    try:
        relatorio = buscar_por_sementes(sementes[:SEMENTES_POR_BUSCA_MANUAL])
    except BaseIndisponivel as exc:
        messages.error(request, str(exc))
        return redirect(reverse("radar:configuracao") + "#artigos-cientificos")
    request.session["busca_de_artigos"] = relatorio
    return redirect(reverse("radar:configuracao") + "#artigos-cientificos")


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
@require_POST
def descartar_ruins(request: HttpRequest) -> HttpResponse:
    """Descarta de uma vez os temas em observacao que a IA marcou como ruins."""
    grupos = GrupoDeDemanda.objects.filter(
        situacao=GrupoDeDemanda.Situacao.NOVO, avaliacao_ia="ruim"
    )
    SinalDeDemanda.objects.filter(grupo__in=grupos).update(
        situacao=SinalDeDemanda.Situacao.DESCARTADO
    )
    total = grupos.update(situacao=GrupoDeDemanda.Situacao.DESCARTADO)
    messages.success(request, _("%(n)s tema(s) descartado(s).") % {"n": total})
    return redirect(reverse("radar:radar") + "#temas")


@login_required
@require_POST
def revisar_resposta_ia(request: HttpRequest) -> HttpResponse:
    """A resposta colada vira previa: o que entra, o que sai, o que e etiquetado."""
    from apps.radar import revisao_ia

    resposta = request.POST.get("resposta", "")
    leitura = revisao_ia.ler(resposta)
    if leitura.vazia:
        messages.error(
            request,
            _(
                "Nao achei os blocos SEMENTES, DORES, BONS nem RUINS na "
                "resposta. Copie o pedido de novo e cole a resposta inteira."
            ),
        )
        return redirect(reverse("radar:radar") + "#outra-ia")
    return render(
        request,
        "radar/revisao_ia.html",
        {"aba": "radar", "subaba": "radar", "resposta": resposta, **revisao_ia.previa(leitura)},
    )


@login_required
@require_POST
def aplicar_resposta_ia(request: HttpRequest) -> HttpResponse:
    from apps.radar import revisao_ia

    # A previa manda as partes marcadas; sem a previa (chamada direta), tudo
    # menos o negocio, que so muda quando a pessoa marca.
    if request.POST.get("com_partes"):
        partes = set(request.POST.getlist("partes")) & set(revisao_ia.PARTES)
    else:
        partes = {"sementes", "dores", "temas"}
    feito = revisao_ia.aplicar(revisao_ia.ler(request.POST.get("resposta", "")), partes)
    messages.success(
        request,
        _(
            "Aplicado: %(s)s semente(s), %(d)s dor(es), %(t)s tema(s) avaliado(s) e %(n)s "
            "campo(s) do negocio. Os temas ruins estao no filtro 'IA: ruim', para voce "
            "descartar."
        )
        % {"s": feito["sementes"], "d": feito["dores"], "t": feito["temas"], "n": feito["negocio"]},
    )
    return redirect(reverse("radar:radar") + "?ia=ruim#temas")


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
    if request.POST.get("decisao") == "imprensa":
        sugerido.imprensa = True
        sugerido.save(update_fields=["imprensa"])
        messages.success(
            request,
            _("%(d)s marcado como imprensa: entra no angulo das pautas.") % {"d": sugerido.dominio},
        )
        return redirect("radar:radar")
    if request.POST.get("decisao") == "desfazer":
        # Marcado como parceiro por engano: volta a ser so um site sugerido.
        sugerido.situacao = ConcorrenteSugerido.Situacao.SUGERIDO
        sugerido.save(update_fields=["situacao"])
        messages.success(request, _("%(d)s voltou para os sugeridos.") % {"d": sugerido.dominio})
        return redirect(reverse("radar:radar") + "#parceiros")
    if request.POST.get("decisao") == "parceiro":
        sugerido.situacao = ConcorrenteSugerido.Situacao.PARCEIRO
        sugerido.save(update_fields=["situacao"])
        messages.success(
            request, _("%(d)s foi para Possiveis parceiros.") % {"d": sugerido.dominio}
        )
        return redirect(reverse("radar:radar") + "#parceiros")
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
    oportunidades = list(
        Oportunidade.objects.filter(situacao=ver)
        .select_related("grupo")
        .prefetch_related("grupo__sinais")
        .order_by("-nota")[:50]
    )
    if ver == Oportunidade.Situacao.EM_TESTE and oportunidades:
        from apps.content.desempenho import painel
        from apps.radar.teste import resultado

        uma_vez = painel(90)
        for oportunidade in oportunidades:
            oportunidade.teste = resultado(oportunidade, painel=uma_vez)
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
            "oportunidades": oportunidades,
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
                "Pauta criada: %(t)s. Publicado o artigo, o resultado do teste aparece "
                "na aba 'Em teste com artigo' (busca, leitura, chamada e conversao)."
            )
            % {"t": criadas[0]},
        )
    elif decisao == "validar":
        # O unico caminho pelo qual o sistema escreve no Negocio: a pessoa
        # confirmando que o teste deu certo.
        from apps.editorial.models import PerfilDoNegocio

        perfil = PerfilDoNegocio.carregar()
        frentes = [f.strip() for f in perfil.frentes.splitlines() if f.strip()]
        if tema.lower() not in {f.lower() for f in frentes}:
            perfil.frentes = "\n".join([*frentes, tema[:200]])
            perfil.save(update_fields=["frentes"])
        oportunidade.situacao = Oportunidade.Situacao.VALIDADA
        messages.success(
            request, _("'%(t)s' virou frente do negocio (tela Negocio).") % {"t": tema}
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
    return redirect(reverse("radar:configuracao") + "#sementes-sugeridas")


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
    return redirect(reverse("radar:configuracao") + "#sementes-sugeridas")


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


@login_required
def imprensa(request: HttpRequest) -> HttpResponse:
    """Painel de imprensa (um e-mail por veiculo) e links quebrados do assunto."""
    from apps.content.imprensa import painel, pedido_de_email
    from apps.radar.links_quebrados import email
    from apps.radar.models import LinkQuebrado

    veiculos = painel()
    for veiculo in veiculos:
        veiculo.pedido = pedido_de_email(veiculo)
    links = list(
        LinkQuebrado.objects.filter(situacao=LinkQuebrado.Situacao.NOVO).select_related("artigo")[
            :50
        ]
    )
    for link in links:
        link.email = email(link)
    return render(
        request,
        "radar/imprensa.html",
        {
            "aba": "radar",
            "subaba": "imprensa",
            "veiculos": veiculos,
            "links": links,
            "links_contatados": LinkQuebrado.objects.filter(
                situacao=LinkQuebrado.Situacao.CONTATADO
            ).count(),
        },
    )


@login_required
@require_POST
def veiculo_contatado(request: HttpRequest, pk) -> HttpResponse:
    from apps.radar.models import ConcorrenteSugerido

    veiculo = get_object_or_404(ConcorrenteSugerido, pk=pk, imprensa=True)
    veiculo.contatado_em = None if request.POST.get("desfazer") else timezone.now()
    veiculo.save(update_fields=["contatado_em"])
    return redirect(reverse("radar:imprensa") + f"#veiculo-{veiculo.pk}")


@login_required
@require_POST
def decidir_link_quebrado(request: HttpRequest, pk) -> HttpResponse:
    """Contatado, descartado, ou o assunto vira pauta (quando nao ha artigo)."""
    from apps.content.models import Topic
    from apps.radar.models import LinkQuebrado

    link = get_object_or_404(LinkQuebrado, pk=pk)
    decisao = request.POST.get("decisao")
    if decisao == "pauta":
        titulo = (link.texto or link.link_url)[:300]
        Topic.objects.create(
            title=titulo,
            target_keyword=titulo[:120],
            briefing=(
                f"Link quebrado em {link.pagina_url} apontava para {link.link_url}. "
                "Um artigo sobre isso pode ocupar o lugar do link."
            ),
            status=Topic.Status.SUGGESTED,
        )
        messages.success(request, _("Pauta criada: %(t)s") % {"t": titulo})
        link.situacao = LinkQuebrado.Situacao.DESCARTADO
    elif decisao == "contatado":
        link.situacao = LinkQuebrado.Situacao.CONTATADO
    else:
        link.situacao = LinkQuebrado.Situacao.DESCARTADO
    link.save(update_fields=["situacao"])
    return redirect(reverse("radar:imprensa") + "#links-quebrados")
