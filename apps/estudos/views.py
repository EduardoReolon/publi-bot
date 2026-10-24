"""As telas dos estudos: a lista, o estudo (area, rodadas, palavras, funil) e
uma palavra (historico, cidades, sazonalidade)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.estudos import coleta, funil, municipios, pedido
from apps.estudos.acesso import exige
from apps.estudos.models import Estudo, Rodada

MAXIMO_NA_TABELA = 300


def _cidade(texto: str) -> int | None:
    """'Cascavel (PR)', 'Cascavel - PR' ou 'Cascavel': o codigo do IBGE (com
    a sigla, a do estado certo)."""
    import re

    texto = (texto or "").strip()
    sigla = re.search(r"\b([A-Za-z]{2})\)?\s*$", texto) if re.search(r"[(,\-]", texto) else None
    nome = re.split(r"[(,\-]", texto)[0].strip()
    achados = municipios.procurar(nome) if nome else []
    if sigla:
        achados = [m for m in achados if m.sigla == sigla.group(1).upper()] or achados
    return achados[0].codigo if achados else None


def _carregar_populacao(request, estudo: Estudo) -> None:
    ufs = {m.uf for m, _ in municipios.no_raio(estudo.cidade, estudo.raio_km)}
    for aviso in municipios.carregar_populacao(ufs):
        messages.warning(request, aviso)


@login_required
@exige
def inicio(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        if not nome:
            messages.error(request, _("Dê um nome ao estudo."))
            return redirect("estudos:inicio")
        estudo = Estudo.objects.create(
            nome=nome[:200],
            descricao=request.POST.get("descricao", "").strip(),
            cidade=_cidade(request.POST.get("cidade", "")),
            raio_km=int(request.POST.get("raio_km") or 50),
        )
        if estudo.cidade:
            _carregar_populacao(request, estudo)
        else:
            messages.warning(request, _("Cidade nao encontrada: escolha de novo no estudo."))
        return redirect("estudos:estudo", pk=estudo.pk)
    return render(
        request,
        "estudos/inicio.html",
        {"aba": "estudos", "estudos": Estudo.objects.prefetch_related("rodadas")},
    )


def _decimal(valor, padrao):
    try:
        return Decimal(str(valor).replace(",", "."))
    except (InvalidOperation, ValueError):
        return padrao


def _salvar(request, estudo: Estudo) -> None:
    antes = (estudo.cidade, estudo.raio_km)
    estudo.nome = request.POST.get("nome", estudo.nome).strip()[:200] or estudo.nome
    estudo.descricao = request.POST.get("descricao", estudo.descricao).strip()
    cidade = _cidade(request.POST.get("cidade", ""))
    if cidade:
        estudo.cidade = cidade
    estudo.raio_km = max(0, min(int(request.POST.get("raio_km") or estudo.raio_km), 500))
    for campo in ("ctr", "conversao", "fechamento", "cotacao_do_dolar"):
        setattr(estudo, campo, _decimal(request.POST.get(campo), getattr(estudo, campo)))
    valor = request.POST.get("valor_do_cliente", "").strip()
    estudo.valor_do_cliente = _decimal(valor, None) if valor else None
    estudo.save()
    if (estudo.cidade, estudo.raio_km) != antes and estudo.cidade:
        _carregar_populacao(request, estudo)
    messages.success(request, _("Estudo salvo."))


def _lista(texto: str) -> list[str]:
    from apps.radar.provedores import palavra_para_volume

    return list(dict.fromkeys(filter(None, map(palavra_para_volume, texto.splitlines()))))


def _acao(request, estudo: Estudo):
    acao = request.POST.get("acao", "")
    if acao == "salvar":
        _salvar(request, estudo)
    elif acao == "area":
        dentro = {int(c) for c in request.POST.getlist("dentro")}
        estudo.fora_da_area = [
            c["codigo"] for c in coleta.area(estudo) if c["codigo"] not in dentro
        ]
        estudo.save(update_fields=["fora_da_area", "atualizado_em"])
        messages.success(request, _("Area salva."))
    elif acao == "populacao":
        _carregar_populacao(request, estudo)
    elif acao == "colar":
        resposta = request.POST.get("resposta", "")
        lido = pedido.ler(estudo, resposta)
        if not (lido["palavras"] or lido["sementes"] or lido["site"]):
            messages.error(
                request, _("A resposta nao trouxe PALAVRAS, SEMENTES nem SITE. Confira o formato.")
            )
            return
        numero = (
            estudo.rodadas.order_by("-numero").values_list("numero", flat=True).first() or 0
        ) + 1
        Rodada.objects.create(
            estudo=estudo,
            numero=numero,
            pedido=request.POST.get("pedido", ""),
            resposta=resposta,
            analise=lido["analise"],
            modo=lido["modo"],
            palavras=lido["palavras"],
            sementes=lido["sementes"],
            site=lido["site"],
            cidades=lido["cidades"],
        )
        # A campanha e as negativas sugeridas valem para o estudo (a tela
        # deixa desmarcar).
        if lido["campanha"]:
            estudo.campanha = lido["campanha"]
        if lido["negativas"]:
            estudo.negativas = lido["negativas"]
        estudo.save()
        messages.success(request, _("Rodada %(n)s pronta para conferir.") % {"n": numero})
    elif acao == "buscar":
        rodada = get_object_or_404(estudo.rodadas, pk=request.POST.get("rodada"))
        if rodada.situacao not in (Rodada.Situacao.RASCUNHO, Rodada.Situacao.FALHOU):
            messages.info(request, _("Essa rodada ja foi buscada."))
            return
        rodada.modo = (
            Rodada.Modo.REAL
            if request.POST.get("modo") == Rodada.Modo.REAL
            else Rodada.Modo.ESTIMATIVA
        )
        rodada.palavras = _lista(request.POST.get("palavras", ""))
        rodada.sementes = _lista(request.POST.get("sementes", ""))[:20]
        rodada.site = pedido.site_limpo(request.POST.get("site", ""))
        rodada.cidades = [int(c) for c in request.POST.getlist("cidades")]
        rodada.situacao = Rodada.Situacao.NA_FILA
        rodada.erro = ""
        rodada.save()
        from apps.estudos.tasks import buscar_rodada
        from apps.ops.acompanhamento import na_fila

        na_fila(
            buscar_rodada,
            str(rodada.pk),
            rotulo=f"Estudo {estudo.nome}: rodada {rodada.numero}",
            alvo=estudo.pk,
        )
        messages.info(request, _("Buscando no Google (alguns minutos). Recarregue depois."))
    elif acao == "virar_cliente":
        _virar_cliente(request, estudo)
    elif acao == "campanha":
        estudo.campanha = request.POST.getlist("campanha")
        estudo.negativas = _lista(request.POST.get("negativas", ""))
        estudo.save(update_fields=["campanha", "negativas", "atualizado_em"])
        messages.success(request, _("Campanha salva."))


def _pedido_do_cliente(estudo: Estudo) -> str:
    from apps.estudos import passagem

    return passagem.pedido(estudo) if not estudo.tenant_criado else ""


def _endereco_do_tenant(estudo: Estudo) -> str:
    from apps.accounts.models import Domain

    if not estudo.tenant_criado:
        return ""
    dominio = Domain.objects.filter(
        tenant__schema_name=estudo.tenant_criado, is_primary=True
    ).first()
    return f"//{dominio.domain}/" if dominio else ""


def _virar_cliente(request, estudo: Estudo) -> None:
    """Um tenant novo para a empresa do estudo, com os primeiros passos feitos
    (o cadastro de sempre, com quem esta logado como dono)."""
    from django.conf import settings
    from django.db import transaction

    from apps.accounts.forms import SignupForm, criar_tenant_e_dono
    from apps.estudos import passagem
    from apps.estudos.tasks import criar_tenant_do_estudo
    from apps.ops.acompanhamento import na_fila

    if estudo.tenant_criado:
        messages.info(
            request, _("Este estudo ja virou o tenant %(t)s.") % {"t": estudo.tenant_criado}
        )
        return
    form = SignupForm(
        {
            "subdomain": request.POST.get("subdomain", ""),
            "organization": request.POST.get("organization", ""),
            "aceite": "on",
        },
        usuario=request.user,
    )
    if not form.is_valid():
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
        return
    dados = passagem.dados_do_estudo(estudo, request.POST.get("resposta", ""))
    copiar = bool(request.POST.get("copiar_contas"))
    from django_tenants.utils import get_public_schema_name, schema_context

    with transaction.atomic():
        # O registro do tenant vive no public (o django-tenants recusa de dentro
        # de um tenant); o estudo e a fila continuam aqui.
        with schema_context(get_public_schema_name()):
            tenant, _usuario = criar_tenant_e_dono(
                subdomain=form.cleaned_data["subdomain"],
                organization=form.cleaned_data["organization"],
                root_domain=settings.ROOT_DOMAIN,
                usuario=request.user,
            )
        estudo.tenant_criado = tenant.schema_name
        estudo.save(update_fields=["tenant_criado", "atualizado_em"])
        transaction.on_commit(
            lambda: na_fila(
                criar_tenant_do_estudo,
                str(tenant.pk),
                dados,
                copiar_contas=copiar,
                rotulo=f"Tenant {tenant.name} a partir do estudo",
                alvo=estudo.pk,
            )
        )
    messages.success(
        request,
        _(
            "Tenant %(t)s sendo criado (um minuto ou dois), ja com o Negocio, o Radar e o "
            "Guia preenchidos. Falta o site, que o cliente conecta em Site e cadencia."
        )
        % {"t": tenant.name},
    )


@login_required
@exige
def estudo(request: HttpRequest, pk) -> HttpResponse:
    estudo = get_object_or_404(Estudo, pk=pk)
    if request.method == "POST":
        _acao(request, estudo)
        voltar = request.POST.get("voltar", "")
        return (
            redirect(voltar) if voltar.startswith("/") else redirect("estudos:estudo", pk=estudo.pk)
        )
    dados = coleta.consolidado(estudo)
    ordem = sorted(dados.items(), key=lambda x: -(x[1].get("volume_area") or 0))
    filtro = request.GET.get("q", "").strip().lower()
    if filtro:
        ordem = [(p, i) for p, i in ordem if filtro in p]
    linhas = [
        {
            "palavra": p,
            **i,
            "tendencia": funil.tendencia(i.get("meses_area")),
            "pico": funil.pico(i.get("meses_area")),
            "reais_medidos": sum(1 for v in (i.get("reais") or {}).values() if v is not None),
            "na_campanha": p in (estudo.campanha or []),
            **funil.contas_da_palavra(estudo, i),
        }
        for p, i in ordem[:MAXIMO_NA_TABELA]
    ]
    rodadas = list(estudo.rodadas.all())
    rascunho = next((r for r in rodadas if r.situacao in ("rascunho", "falhou")), None)
    area = coleta.area(estudo)
    return render(
        request,
        "estudos/estudo.html",
        {
            "aba": "estudos",
            "estudo": estudo,
            "centro": municipios.todos().get(estudo.cidade),
            "area": area,
            "populacao_da_area": sum(c["populacao"] or 0 for c in area if c["dentro"]),
            "falta_populacao": any(c["populacao"] is None for c in area),
            "pedido": pedido.montar(estudo) if estudo.cidade else "",
            "rodadas": rodadas,
            "rascunho": rascunho,
            "custo_do_rascunho": coleta.custo_estimado(rascunho) if rascunho else None,
            "palavras_do_rascunho": "\n".join(rascunho.palavras) if rascunho else "",
            "sementes_do_rascunho": "\n".join(rascunho.sementes) if rascunho else "",
            "negativas_do_estudo": "\n".join(estudo.negativas or []),
            "linhas": linhas,
            # As da campanha que nao estao na tabela (filtro, ou alem das 300):
            # o formulario as manda de volta, para nao sumirem ao salvar.
            "campanha_oculta": [
                p for p in estudo.campanha or [] if p not in {x["palavra"] for x in linhas}
            ],
            "total_de_palavras": len(dados),
            "filtro": filtro,
            "funil": funil.funil(estudo, dados) if estudo.campanha else None,
            "pedido_do_cliente": _pedido_do_cliente(estudo),
            "endereco_do_tenant": _endereco_do_tenant(estudo),
        },
    )


@login_required
@exige
def palavra(request: HttpRequest, pk) -> HttpResponse:
    estudo = get_object_or_404(Estudo, pk=pk)
    texto = request.GET.get("p", "")
    item = coleta.consolidado(estudo).get(texto)
    if item is None:
        messages.error(request, _("Essa palavra nao esta no estudo."))
        return redirect("estudos:estudo", pk=estudo.pk)
    nomes = {c["codigo"]: c for c in coleta.area(estudo)}
    cidades = []
    for codigo, estimado in sorted((item.get("cidades") or {}).items(), key=lambda x: -x[1]):
        real = (item.get("reais") or {}).get(codigo, "nao medido")
        cidades.append(
            {
                "cidade": nomes.get(int(codigo), {}).get("municipio"),
                "distancia": nomes.get(int(codigo), {}).get("distancia"),
                "estimado": estimado,
                "medido": real != "nao medido",
                "real": None if real == "nao medido" else real,
                "fator": round(real / estimado, 2)
                if isinstance(real, int | float) and estimado
                else None,
            }
        )
    meses = item.get("meses_area") or []
    maior = max((v for _, _, v in meses), default=0) or 1
    indices = funil.sazonalidade(meses)
    return render(
        request,
        "estudos/palavra.html",
        {
            "aba": "estudos",
            "estudo": estudo,
            "palavra": texto,
            "item": item,
            "cidades": cidades,
            "meses": [
                {
                    "rotulo": f"{funil.MESES[m - 1]}/{str(a)[2:]}",
                    "valor": v,
                    "altura": round(v / maior * 100),
                }
                for a, m, v in meses
            ],
            "sazonalidade": [
                {"mes": funil.MESES[i], "indice": v, "altura": round(v / max(indices) * 100)}
                for i, v in enumerate(indices)
            ]
            if indices
            else [],
            "tendencia": funil.tendencia(meses),
            "pico": funil.pico(meses),
            "contas": funil.contas_da_palavra(estudo, item),
            "na_campanha": texto in (estudo.campanha or []),
        },
    )
