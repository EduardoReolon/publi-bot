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
from apps.editorial.linguagem import termos_que_faltam
from apps.editorial.models import EditorialProfile, PerfilDoNegocio
from apps.editorial.presets import MODOS
from apps.editorial.services import aplicar_modo


@login_required
def guia(request: HttpRequest) -> HttpResponse:
    from apps.editorial import primeiros_passos
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = EditorialProfile.carregar()
    colado = False

    if request.method == "POST" and request.POST.get("acao") == "colar_guia":
        # A resposta do pedido preenche a tela SEM salvar: a pessoa revisa.
        lido = primeiros_passos.ler_guia(request.POST.get("resposta", ""))
        if not lido:
            messages.error(request, _("Nao achei os blocos do guia na resposta colada."))
            return redirect("editorial:guia")
        form = PerfilEditorialForm(instance=perfil, initial=lido)
        colado = True
    elif request.method == "POST":
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
            "colado": colado,
            "conselhos_que_faltam": termos_que_faltam(perfil),
            "pedido_do_guia": primeiros_passos.pedido_do_guia(
                PerfilDoNegocio.carregar(), ConfiguracaoDoRadar.carregar()
            ),
        },
        status=400 if request.method == "POST" and not colado else 200,
    )


@login_required
@require_POST
def adicionar_conselhos(request: HttpRequest) -> HttpResponse:
    """Acrescenta ao vocabulario proibido os termos das regras de publicidade em
    saude (CFM/CFO) que faltam. Nao tira nem muda nada do que ja esta la."""
    perfil = EditorialProfile.carregar()
    novos = termos_que_faltam(perfil)
    perfil.termos = [*(perfil.termos or []), *novos]
    perfil.save(update_fields=["termos"])
    messages.success(request, _("%(n)s termo(s) adicionados ao guia.") % {"n": len(novos)})
    return redirect("editorial:guia")


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
    """O que o site e e o que vende, em passos: a referencia de tudo que e medido."""
    from apps.editorial import primeiros_passos
    from apps.editorial.forms import BasicoDoNegocioForm, ValoresDoNegocioForm
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = PerfilDoNegocio.carregar()
    config = ConfiguracaoDoRadar.carregar()
    acao = request.POST.get("acao", "salvar") if request.method == "POST" else ""
    dados = request.POST if request.method == "POST" else None
    form = PerfilDoNegocioForm(instance=perfil, config=config)
    basico = BasicoDoNegocioForm(instance=perfil)
    valores = ValoresDoNegocioForm(instance=perfil)
    colado = invalido = False

    if acao == "basico":
        basico = BasicoDoNegocioForm(dados, instance=perfil)
        if basico.is_valid():
            basico.save()
            messages.success(request, _("Salvo. Agora o passo 2: copie o pedido 1."))
            return redirect(reverse("editorial:negocio") + "#passo-2")
        invalido = True
    elif acao == "colar_negocio":
        # A resposta do pedido 1 preenche o passo 3 SEM salvar: a pessoa
        # revisa antes. Dores e frentes somam as que ja existem.
        lido = primeiros_passos.ler_negocio(request.POST.get("resposta", ""))
        if not lido:
            messages.error(
                request,
                _("Nao achei TEMA, PUBLICO, OFERTA, DORES nem FRENTES na resposta colada."),
            )
            return redirect(reverse("editorial:negocio") + "#passo-2")
        inicial = {k: v for k, v in lido.items() if k in {"tema", "publico", "oferta"}}
        if "dores" in lido:
            # As dores vivem no Radar, junto das sementes: somam ja, sem repetir,
            # e a pessoa as ve (e edita) la.
            antes = len(config.lista_de_dores)
            config.dores = primeiros_passos.unir(config.dores, lido["dores"])
            config.save(update_fields=["dores"])
            novas = len(config.lista_de_dores) - antes
            if novas:
                messages.info(
                    request,
                    _("%(n)s dor(es) somada(s) em Radar > Configuracao, junto das sementes.")
                    % {"n": novas},
                )
        if "frentes" in lido:
            inicial["frentes"] = primeiros_passos.unir(perfil.frentes, lido["frentes"])
        form = PerfilDoNegocioForm(instance=perfil, config=config, initial=inicial)
        colado = True
    elif acao == "colar_sementes":
        criadas = primeiros_passos.sugerir_sementes(request.POST.get("resposta", ""))
        if criadas:
            messages.success(
                request,
                _(
                    "%(n)s semente(s) em Radar > Configuracao > Sementes sugeridas, para "
                    "aceitar ou recusar."
                )
                % {"n": criadas},
            )
        else:
            messages.error(
                request, _("Nenhuma semente nova na resposta (ou todas ja estavam na lista).")
            )
        return redirect("editorial:negocio")
    elif acao == "valores":
        valores = ValoresDoNegocioForm(dados, instance=perfil)
        if valores.is_valid():
            valores.save()
            messages.success(request, _("Valores salvos."))
            return redirect("editorial:negocio")
        invalido = True
    elif acao == "salvar":
        form = PerfilDoNegocioForm(dados, instance=perfil, config=config)
        if form.is_valid():
            form.save()
            messages.success(request, _("Negocio salvo. Vale para a proxima rodada e geracao."))
            return redirect(reverse("editorial:negocio") + "#passo-3")
        invalido = True

    return render(
        request,
        "editorial/negocio.html",
        {
            "aba": "negocio",
            "form": form,
            "basico": basico,
            "valores": valores,
            "colado": colado,
            "conselhos_que_faltam": termos_que_faltam(perfil),
            "sementes": config.lista_de_sementes,
            "dores": config.lista_de_dores,
            "regioes": [nome for _codigo, nome in config.locais() if nome],
            "concorrentes": config.lista_de_concorrentes,
            "pedido_do_negocio": primeiros_passos.pedido_do_negocio(perfil, config),
            "pedido_das_sementes": primeiros_passos.pedido_das_sementes(perfil, config),
            "comecando": not (perfil.tema and perfil.oferta),
            "pronto_para_sementes": bool(perfil.tema and perfil.oferta)
            and not config.lista_de_sementes,
        },
        status=400 if invalido else 200,
    )
