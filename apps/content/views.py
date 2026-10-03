"""Telas de pauta, artigo, revisao e resposta."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.content.chamada import texto_da_oferta
from apps.content.forms import (
    AgendamentoForm,
    PautaForm,
    RevisaoDeArtigo,
    RevisaoDeResposta,
)
from apps.content.models import CHAMADAS, Answer, Article, Author, Question, Topic
from apps.content.services import (
    RevisaoInsuficiente,
    aplicar_edicao_humana,
    aprovar_e_agendar,
    aprovar_resposta_e_agendar,
    pendencias_para_aprovar,
)
from apps.content.tasks import responder_pergunta
from apps.knowledge.referencias import painel as painel_de_referencias
from apps.ops.models import GenerationJob
from apps.radar.links_quebrados import cobertura_do_artigo

logger = logging.getLogger("publibot.content")


def _site():
    from apps.integrations.models import Site

    return Site.objects.first()


def _proximo_horario():
    """Primeiro horario livre da cadencia, ou agora se nao houver cadencia.

    Sem cadencia configurada o conteudo aprovado ficaria parado para sempre
    esperando um horario que ninguem gera. Publicar no proximo tick e um
    default previsivel; a cadencia continua sendo o caminho recomendado.
    """
    from apps.integrations.models import PublicationSlot

    livre = (
        PublicationSlot.objects.filter(
            article__isnull=True, answer__isnull=True, slot_at__gte=timezone.now()
        )
        .order_by("slot_at")
        .first()
    )
    return livre.slot_at if livre else timezone.now()


# ---------------------------------------------------------------------------
# Pautas
# ---------------------------------------------------------------------------
@login_required
def pautas(request: HttpRequest) -> HttpResponse:
    situacao = request.GET.get("situacao", "")
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    consulta = Topic.objects.annotate(artigos=Count("articles")).order_by("-created_at")
    if situacao:
        consulta = consulta.filter(status=situacao)
    # A etiqueta que a outra IA deu ao tema de origem (Radar > Ajustar com outra IA).
    ia = request.GET.get("ia", "")
    if ia in {"boa", "ruim"}:
        consulta = consulta.filter(grupos_de_demanda__avaliacao_ia=ia).distinct()

    lista = list(
        consulta.select_related("artigo_parecido").prefetch_related(
            "articles", "grupos_de_demanda"
        )[:200]
    )
    _preparar_pautas(lista, config, completo=False)
    return render(
        request,
        "content/pautas.html",
        {
            "aba": "pautas",
            "pautas": lista,
            "config": config,
            "situacao": situacao,
            "ia": ia,
            "form": PautaForm(),
        },
    )


@login_required
def pauta(request: HttpRequest, pk) -> HttpResponse:
    """A pagina da pauta: os dois fluxos, o que esta em curso, as referencias,
    a pesquisa e as acoes."""
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    alvo = get_object_or_404(
        Topic.objects.select_related("artigo_parecido").prefetch_related(
            "articles", "grupos_de_demanda"
        ),
        pk=pk,
    )
    _preparar_pautas([alvo], config, completo=True)
    from apps.content.dados_da_pauta import escolhidos, sugestoes

    return render(
        request,
        "content/pauta.html",
        {
            "aba": "pautas",
            "pauta": alvo,
            "config": config,
            "dados_escolhidos": escolhidos(alvo),
            "dados_sugeridos": sugestoes(alvo),
        },
    )


@login_required
@require_POST
def dados_da_pauta(request: HttpRequest, pk) -> HttpResponse:
    """Usa ou tira um dado publico da pauta."""
    from apps.content.models import DadoDaPauta
    from apps.dados.models import Serie

    alvo = get_object_or_404(Topic, pk=pk)
    if request.POST.get("acao") == "tirar":
        # Fica gravado como tirado: o automatico nao o poe de volta.
        DadoDaPauta.objects.filter(topic=alvo, pk=request.POST.get("dado")).update(tirado=True)
    else:
        serie = get_object_or_404(Serie, pk=request.POST.get("serie"))
        DadoDaPauta.objects.update_or_create(
            topic=alvo, serie=serie, defaults={"tirado": False, "automatico": False}
        )
    return redirect(reverse("content:pauta", args=[alvo.pk]) + "#dados")


def _preparar_pautas(lista: list, config, *, completo: bool) -> None:
    """O que a lista e a pagina da pauta mostram de cada uma. A lista fica sem
    os paineis pesados (referencias, imprensa)."""
    from apps.content.fluxos import para_a_tela, trabalhos_em_curso, ultimas_falhas
    from apps.content.outra_ia import motivos_de_peso
    from apps.knowledge.pesquisa import pedidos_em_aberto

    trabalhos = trabalhos_em_curso(lista)
    falhas = ultimas_falhas(lista)
    if completo:
        from apps.content.imprensa import dados_de_imprensa, veiculos

        dados = dados_de_imprensa()
    for item in lista:
        item.grupo_avaliado = next(
            (g for g in item.grupos_de_demanda.all() if g.avaliacao_ia), None
        )
        item.fluxos = para_a_tela(item, config, trabalhos, falhas)
        item.faltam_os_dois = len(item.fluxos) == 2 and all(f["falta"] for f in item.fluxos)
        item.por_curar = ((item.busca_de_fontes or {}).get("acervo") or {}).get("por_curar")
        item.pdfs_esperando = pedidos_em_aberto(item)
        if not completo:
            continue
        item.de_peso = motivos_de_peso(item)
        item.imprensa = veiculos(item, dados)
        # As referencias: so onde ja houve busca ou a pauta espera fontes.
        if item.busca_de_fontes or item.status == Topic.Status.WAITING_SOURCES:
            item.referencias = painel_de_referencias(item)


def _de_volta(pauta) -> HttpResponse:
    return redirect("content:pauta", pauta.pk)


@login_required
@require_POST
def desistir_da_geracao(request: HttpRequest, pk, trabalho) -> HttpResponse:
    """Encerra a geracao em curso de um fluxo (presa esperando a placa, por
    exemplo), para poder gerar de novo ou ir pela outra IA."""
    from apps.content.fluxos import desistir

    alvo = get_object_or_404(Topic, pk=pk)
    job = get_object_or_404(
        GenerationJob, pk=trabalho, target_object_id=alvo.pk, kind=GenerationJob.Kind.PILLAR_ARTICLE
    )
    if job.status in (GenerationJob.Status.DONE, GenerationJob.Status.FAILED):
        messages.info(request, _("Esta geracao ja tinha terminado."))
    else:
        desistir(job, _("Encerrada pela pessoa, na pagina da pauta."))
        messages.success(request, _("Geracao encerrada. O fluxo pode ser gerado de novo."))
    return _de_volta(alvo)


@login_required
def intencao_da_pauta(request: HttpRequest, pk) -> HttpResponse:
    """Analise de intencao com outra IA: copia o pedido, cola a resposta."""
    from apps.content import intencao

    pauta = get_object_or_404(Topic, pk=pk)
    if request.method == "POST":
        if pauta.status in {Topic.Status.USED, Topic.Status.REJECTED}:
            messages.error(request, _("Pauta ja usada ou rejeitada nao muda mais."))
            return redirect("content:pautas")
        mudados = intencao.aplicar(pauta, request.POST.get("resposta", ""))
        if mudados:
            messages.success(
                request,
                _("Pauta atualizada (titulo, tipo e orientacao conforme a resposta)."),
            )
            return _de_volta(pauta)
        messages.error(request, _("Nao achei TITULO, TIPO nem ORIENTACAO na resposta colada."))
    return render(
        request,
        "content/intencao.html",
        {"aba": "pautas", "pauta": pauta, "pedido": intencao.pedido(pauta)},
    )


@login_required
def artigo_por_outra_ia(request: HttpRequest, pk) -> HttpResponse:
    """O artigo escrito por um modelo grande de fora: prepara, copia, cola."""
    from apps.content import outra_ia
    from apps.content.fluxos import artigo_do_fluxo
    from apps.content.rendering import LinkAlucinado
    from apps.content.services import SemFontesSuficientes

    pauta = get_object_or_404(Topic, pk=pk)
    fluxo = (
        "pesquisa" if (request.POST.get("fluxo") or request.GET.get("fluxo")) == "pesquisa" else ""
    )
    artigo = outra_ia.artigo_em_espera(pauta, fluxo)
    pronto = artigo_do_fluxo(pauta, fluxo)
    if artigo is None and pronto is not None:
        return redirect("content:revisar", pronto.pk)

    colado = ""
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "preparar" and artigo is None:
            if pauta.status == Topic.Status.REJECTED:
                messages.error(request, _("Pauta rejeitada nao vira artigo."))
                return redirect("content:pautas")
            if fluxo:
                from apps.knowledge.pesquisa import pronta_para_gerar

                motivo = pronta_para_gerar(pauta)
                if motivo:
                    messages.info(request, _("Ainda nao: %(m)s") % {"m": motivo})
                    return redirect("content:pautas")
            try:
                outra_ia.preparar(pauta, fluxo)
            except SemFontesSuficientes as exc:
                messages.error(request, str(exc))
                return redirect("content:pautas")
            return redirect(
                reverse("content:artigo_por_outra_ia", args=[pauta.pk])
                + ("?fluxo=pesquisa" if fluxo else "")
            )
        if acao == "desistir" and artigo is not None:
            outra_ia.desistir(artigo)
            messages.success(request, _("Pronto: a pauta voltou para a lista, sem artigo."))
            return redirect("content:pautas")
        if acao == "colar" and artigo is not None:
            colado = request.POST.get("resposta", "")
            try:
                outra_ia.aplicar(artigo, colado)
            except (ValueError, LinkAlucinado) as exc:
                messages.error(request, _("O texto nao foi aceito: %(m)s") % {"m": exc})
            else:
                messages.success(request, _("Artigo gravado. Revise antes de aprovar."))
                return redirect("content:revisar", artigo.pk)

    return render(
        request,
        "content/outra_ia.html",
        {
            "aba": "pautas",
            "pauta": pauta,
            "artigo": artigo,
            "pedido": outra_ia.pedido(artigo) if artigo else "",
            "motivos": outra_ia.motivos_de_peso(pauta),
            "colado": colado,
            "fluxo": fluxo,
        },
        status=400 if colado else 200,
    )


@login_required
def imprensa_da_pauta(request: HttpRequest, pk) -> HttpResponse:
    """O pedido para outra IA julgar se a pauta vira materia de jornal."""
    from apps.content import imprensa

    pauta = get_object_or_404(Topic, pk=pk)
    return render(
        request,
        "content/imprensa.html",
        {
            "aba": "pautas",
            "pauta": pauta,
            "pedido": imprensa.pedido(pauta),
            "achados": imprensa.veiculos(pauta),
        },
    )


@login_required
def nova_pauta(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("content:pautas")

    form = PautaForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "content/pautas.html",
            {
                "aba": "pautas",
                "pautas": Topic.objects.annotate(artigos=Count("articles")).order_by("-created_at")[
                    :200
                ],
                "situacao": "",
                "form": form,
            },
            status=400,
        )

    pauta = form.save(commit=False)
    # Criada a mao ja nasce aprovada: quem digitou o titulo ja decidiu.
    pauta.status = Topic.Status.APPROVED
    pauta.approved_by = request.user
    pauta.approved_at = timezone.now()
    pauta.save()
    messages.success(request, _("Pauta criada."))
    return _de_volta(pauta)


@login_required
@require_POST
def gerar(request: HttpRequest, pk) -> HttpResponse:
    """Dispara a geracao. `fluxo`: "" (A), "pesquisa" (B) ou "ligados" (os dois,
    se a configuracao estiver com os dois). Uma pauta tem um artigo por fluxo."""
    from apps.content import fluxos

    pauta = get_object_or_404(Topic, pk=pk)
    pedido = request.POST.get("fluxo", "")
    escolhidos = (
        fluxos.ligados() if pedido == "ligados" else [pedido if pedido == "pesquisa" else ""]
    )
    for fluxo in escolhidos:
        nivel, mensagem = fluxos.disparar(pauta, fluxo)
        getattr(messages, nivel)(request, mensagem)
    artigo = Article.objects.filter(pk=request.POST.get("artigo") or None, topic=pauta).first()
    return _ao_artigo(artigo) if artigo else _de_volta(pauta)


@login_required
@require_POST
def buscar_fontes(request: HttpRequest, pk) -> HttpResponse:
    """Busca referencias para a pauta; com `variar`, com palavras ainda nao usadas."""
    from apps.knowledge.tasks import buscar_fontes_da_pauta

    pauta = get_object_or_404(Topic, pk=pk)
    variar = request.POST.get("variar") == "1"
    transaction.on_commit(lambda: buscar_fontes_da_pauta.delay(str(pauta.pk), variar))
    messages.success(
        request,
        _("Buscando com outras palavras. Recarregue em instantes para ver o resultado.")
        if variar
        else _("Buscando referencias. Recarregue em instantes para ver o resultado."),
    )
    return _de_volta(pauta)


@login_required
@require_POST
def pesquisar_artigos(request: HttpRequest, pk) -> HttpResponse:
    """A pesquisa de artigos cientificos da pauta (fluxo B), na fila."""
    from apps.content.fluxos import iniciar_pesquisa

    pauta = get_object_or_404(Topic, pk=pk)
    iniciar_pesquisa(pauta, gerar_depois=None)
    messages.success(
        request,
        _(
            "Pesquisando artigos: hipoteses, busca por sentido, artigos relacionados e "
            "angulos. Leva alguns minutos; recarregue a pagina."
        ),
    )
    return _de_volta(pauta)


@login_required
@require_POST
def pdf_da_pesquisa(request: HttpRequest, pk, candidato) -> HttpResponse:
    """O PDF que a sintese pediu: aberto, vai para o acervo (e a curadoria); sem
    ele, o artigo espera o PDF em Fontes sugeridas."""
    from apps.knowledge.academicos import aprovar_artigo
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.perfis import categoria_da_natureza

    pauta = get_object_or_404(Topic, pk=pk)
    alvo = get_object_or_404(CandidatoDeFonte, pk=candidato, pauta=pauta)
    alvo = aprovar_artigo(alvo, categoria=categoria_da_natureza("cientifico"), por=request.user)
    if alvo.situacao == CandidatoDeFonte.Situacao.AGUARDANDO_PDF:
        messages.warning(
            request,
            _("O PDF aberto nao baixou. Quando tiver o arquivo, envie aqui mesmo."),
        )
    else:
        messages.success(
            request,
            _("PDF baixado: os trechos pedidos entram no texto quando a leitura terminar."),
        )
    return redirect("content:pdfs_da_pesquisa", pauta.pk)


@login_required
def pdfs_da_pesquisa(request: HttpRequest, pk) -> HttpResponse:
    """Os artigos da pesquisa (B) de que o texto pede mais que o resumo: os
    detalhes, e enviar o PDF quando a pessoa o tiver em maos."""
    from apps.knowledge.pesquisa import pdfs_pedidos

    alvo = get_object_or_404(Topic, pk=pk)
    return render(
        request,
        "content/pdfs_da_pesquisa.html",
        {"aba": "pautas", "pauta": alvo, "itens": pdfs_pedidos(alvo)},
    )


@login_required
@require_POST
def enviar_pdf_da_pesquisa(request: HttpRequest, pk, candidato) -> HttpResponse:
    from apps.knowledge.academicos import receber_pdf
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.perfis import categoria_da_natureza

    alvo = get_object_or_404(Topic, pk=pk)
    artigo = get_object_or_404(CandidatoDeFonte, pk=candidato)
    arquivo = request.FILES.get("pdf")
    if arquivo is None or not (arquivo.name or "").lower().endswith(".pdf"):
        messages.error(request, _("Envie o arquivo PDF do artigo."))
    else:
        receber_pdf(
            artigo, arquivo, categoria=categoria_da_natureza("cientifico"), por=request.user
        )
        messages.success(
            request,
            _("PDF recebido: os trechos pedidos entram no texto quando a leitura terminar."),
        )
    return redirect("content:pdfs_da_pesquisa", alvo.pk)


@login_required
@require_POST
def conferir_referencias(request: HttpRequest, pk) -> HttpResponse:
    """Reconta o acervo (depois de uma curadoria, por exemplo)."""
    from apps.knowledge.referencias import conferir

    pauta = get_object_or_404(Topic, pk=pk)
    acervo = conferir(pauta)
    if acervo.get("liberada"):
        messages.success(request, _("O acervo agora sustenta a pauta: ela pode ser gerada."))
    elif acervo["suficiente"]:
        messages.success(request, _("O acervo sustenta a pauta."))
    else:
        messages.info(request, _("O acervo ainda nao sustenta a pauta."))
    return _de_volta(pauta)


@login_required
@require_POST
def ignorar_falta_de_artigos(request: HttpRequest, pk) -> HttpResponse:
    from apps.knowledge.referencias import registrar

    pauta = get_object_or_404(Topic, pk=pk)
    if request.POST.get("tipo") == "videos":
        registrar(pauta, "videos", ignorado=True)
        messages.success(request, _("Esta pauta segue sem buscar videos."))
    else:
        registrar(pauta, "artigos", ignorado=True)
        messages.success(request, _("Esta pauta segue sem buscar mais artigos cientificos."))
    return _de_volta(pauta)


@login_required
@require_POST
def rejeitar_pauta(request: HttpRequest, pk) -> HttpResponse:
    pauta = get_object_or_404(Topic, pk=pk)
    pauta.status = Topic.Status.REJECTED
    pauta.save(update_fields=["status"])
    messages.success(request, _("Pauta rejeitada."))
    return redirect("content:pautas")


# ---------------------------------------------------------------------------
# Artigos
# ---------------------------------------------------------------------------
@login_required
def artigos(request: HttpRequest) -> HttpResponse:
    situacao = request.GET.get("situacao", "")
    # Rascunho descartado (a geracao largou pela metade) nao e um artigo: fica
    # fora da lista, para a pauta nao aparecer em dobro.
    visiveis = Article.objects.exclude(thesis_json__has_key="descartado")
    consulta = visiveis.select_related("topic").order_by("-updated_at")
    if situacao:
        consulta = consulta.filter(status=situacao)
    fora_do_google = request.GET.get("indexacao") == "fora"
    if fora_do_google:
        from apps.radar.indexacao import fora_do_google as fora

        consulta = consulta.filter(pk__in=fora().values("pk"))

    contagens = dict(visiveis.values_list("status").annotate(total=Count("status")).order_by())

    return render(
        request,
        "content/artigos.html",
        {
            "aba": "artigos",
            "artigos": consulta[:200],
            "situacao": situacao,
            "fora_do_google": fora_do_google,
            "situacoes": [
                (valor, rotulo, contagens.get(valor, 0)) for valor, rotulo in Article.Status.choices
            ],
        },
    )


@login_required
def desempenho(request: HttpRequest) -> HttpResponse:
    """Leitura de verdade e conversao por artigo, contadas pelo site."""
    from apps.content.desempenho import painel

    dias = request.GET.get("dias", "28")
    dias = int(dias) if dias in {"7", "28", "90", "365"} else 28
    site = _site()
    return render(
        request,
        "content/desempenho.html",
        {
            "aba": "artigos",
            "painel": painel(dias),
            "dias": dias,
            "periodos": [7, 28, 90, 365],
            "site": site,
            "site_sem_insights": site is not None and not site.suporta("insights"),
        },
    )


@login_required
def revisar(request: HttpRequest, pk) -> HttpResponse:
    """A tela central: ler o texto ao lado das fontes que o sustentam."""
    artigo = get_object_or_404(Article.objects.select_related("topic", "primary_source"), pk=pk)

    if request.method == "POST":
        return _processar_revisao(request, artigo)

    import time

    inicio = time.perf_counter()
    contexto = _contexto_de_revisao(request, artigo)
    meio = time.perf_counter()
    resposta = render(request, "content/revisar.html", contexto)
    fim = time.perf_counter()
    if fim - inicio > 2:
        logger.warning(
            "Pagina do artigo %s: dados %.1fs, montar o HTML %.1fs.",
            artigo.pk,
            meio - inicio,
            fim - meio,
        )
    return resposta


def _contexto_de_revisao(request, artigo, form=None, agendamento=None) -> dict:
    """Tudo o que a pagina do artigo mostra. As partes que consultam mais que o
    proprio artigo sao medidas: se a pagina passar de 2 s, o log diz qual."""
    import time

    tempos: dict[str, float] = {}

    def medir(nome, funcao, *args):
        inicio = time.perf_counter()
        resultado = funcao(*args)
        tempos[nome] = time.perf_counter() - inicio
        return resultado

    contexto = _montar_contexto_de_revisao(artigo, form, agendamento, medir)
    total = sum(tempos.values())
    if total > 2:
        logger.warning(
            "Pagina do artigo %s lenta (%.1fs): %s",
            artigo.pk,
            total,
            ", ".join(f"{n} {t:.1f}s" for n, t in sorted(tempos.items(), key=lambda i: -i[1])[:5]),
        )
    return contexto


def _montar_contexto_de_revisao(artigo, form, agendamento, medir) -> dict:
    site = _site()
    return {
        "aba": "artigos",
        "artigo": artigo,
        "form": form
        or RevisaoDeArtigo(
            initial={
                "title": artigo.title,
                "meta_description": artigo.meta_description,
                "body_markdown": artigo.body_markdown,
                "author": artigo.author_id,
            }
        ),
        "agendamento": agendamento or AgendamentoForm(),
        "citacoes": artigo.citations.select_related("super_chunk").order_by("rank"),
        "revisoes": artigo.revisions.order_by("-version")[:10],
        "secoes": artigo.sections.all(),
        "titulos_sugeridos": (artigo.thesis_json or {}).get("titulos_sugeridos") or [],
        "moldura": (artigo.thesis_json or {}).get("moldura") or {},
        "refazendo": medir("refazendo", _trabalho_em_curso, artigo),
        "site": site,
        "tem_autores": Author.objects.filter(is_active=True).exists(),
        "posicoes_de_link": Article.LinkPlacement.choices,
        "lotes_de_capa": medir("capas", _lotes_de_capa, artigo),
        "motivo_sem_capa": medir("motivo_sem_capa", _motivo_sem_capa, artigo),
        "capas_em_curso": medir("capas_em_curso", _capas_em_curso, artigo),
        "capa_escolhida": artigo.images.filter(is_chosen=True).first(),
        "faq": artigo.faq.all(),
        "conferencia": medir("conferencia_editorial", _conferencia_editorial, artigo),
        "pendencias": medir("pendencias", _pendencias_com_aba, artigo),
        "geracao": medir("geracao", _geracao_do_artigo, artigo),
        "links_quebrados": medir("links_quebrados", cobertura_do_artigo, artigo),
        "nome_do_fluxo": _nome_do_fluxo(artigo.fluxo),
        "descricao_do_fluxo": _descricao_do_fluxo(artigo.fluxo),
        "fontes_da_pauta": medir("fontes_da_pauta", _fontes_pendentes_da_pauta, artigo),
        "irmaos": medir("irmaos", _irmaos, artigo),
        # O que a outra IA pediu do texto completo (fluxo da pesquisa).
        "pedidos_da_outra_ia": (artigo.thesis_json or {}).get("pedidos") or [],
        # O FAQ vai num campo proprio, e o site so o exibe se implementou.
        # Sem este aviso, a pessoa revisaria perguntas que ninguem vai ver.
        "site_sem_faq": site is not None and not site.suporta("faq"),
        "chamada": (artigo.thesis_json or {}).get("chamada") or {},
        "modos_de_chamada": CHAMADAS,
        "partes_da_chamada": _partes_da_chamada(artigo),
        "indexacao_ligada": _indexacao_ligada(artigo),
        "dados_citados": [d for d in artigo.dados_usados or [] if d.get("citado")],
        "citacoes_conferidas": _resumo_da_conferencia(artigo),
        "sem_oferta": not medir("oferta", texto_da_oferta),
        "site_sem_chamada": site is not None and not site.suporta("call_to_action"),
        "proximo_horario": medir("proximo_horario", _proximo_horario),
    }


def _pendencias_com_aba(artigo) -> list[dict]:
    """As pendencias da aprovacao, cada uma com a aba onde se resolve."""
    abas = (("capa", "capa"), ("secoes", "secoes"), ("o dado ", "fontes"))
    return [
        {"texto": texto, "aba": next((aba for chave, aba in abas if chave in texto), "")}
        for texto in pendencias_para_aprovar(artigo)
    ]


def _geracao_do_artigo(artigo) -> dict:
    """O trabalho que gera este rascunho: em curso, ou a falha que o "Tentar de
    novo" retoma (do passo em que parou, no mesmo artigo)."""
    from apps.content import fluxos

    if artigo.status != Article.Status.DRAFTING or not artigo.topic_id:
        return {}
    em_curso = fluxos.em_andamento(artigo.topic, artigo.fluxo)
    if em_curso is not None:
        return {"em_curso": em_curso}
    falhou = fluxos.retomavel(artigo.topic, artigo.fluxo)
    return {"falhou": falhou} if falhou is not None else {}


def _indexacao_ligada(artigo) -> bool:
    if artigo.status != Article.Status.PUBLISHED or not artigo.published_url:
        return False
    from apps.radar.indexacao import ligada

    return ligada()


def _resumo_da_conferencia(artigo) -> dict:
    from apps.content.citacoes import anotar

    registro = artigo.conferencia_citacoes or []
    corpo, notas = anotar(artigo.body_html or "", registro)
    return {
        "itens": registro,
        "corpo_anotado": corpo,
        "notas": notas,
        "trocadas": sum(1 for r in registro if r.get("acao") == "trocada"),
        "reescritas": sum(1 for r in registro if r.get("acao") == "reescrita"),
        "sem_fonte": sum(
            1 for r in registro if r.get("acao") == "sem_fonte" and not r.get("aceita")
        ),
    }


@login_required
@require_POST
def aceitar_sem_fonte(request: HttpRequest, pk) -> HttpResponse:
    """A afirmacao sem fonte fica como opiniao do texto: libera a aprovacao."""
    artigo = get_object_or_404(Article, pk=pk)
    for r in artigo.conferencia_citacoes or []:
        if r.get("acao") == "sem_fonte":
            r["aceita"] = True
    artigo.save(update_fields=["conferencia_citacoes"])
    messages.success(request, _("Afirmacoes sem fonte aceitas como opiniao do texto."))
    return _ao_artigo(artigo, "revisar")


def _partes_da_chamada(artigo) -> list[tuple]:
    """(onde, rotulo, texto) de cada chamada que o modo usa, para editar."""
    texto = artigo.call_to_action_copy or {}
    partes = [("end", _("No fim do artigo"))]
    if artigo.call_to_action == "inline":
        partes.insert(0, ("inline", _("No meio do artigo")))
    return [(onde, rotulo, texto.get(onde) or {}) for onde, rotulo in partes]


def _descricao_do_fluxo(fluxo: str) -> str:
    from apps.content.fluxos import DESCRICOES

    return DESCRICOES.get(fluxo, "")


def _fontes_pendentes_da_pauta(artigo) -> dict:
    """O que falta de fonte na pauta deste artigo: as mesmas pendencias que a
    pagina da pauta mostra, para quem esta revisando nao precisar ir la ver."""
    from apps.knowledge.pesquisa import pedidos_em_aberto

    if not artigo.topic_id or artigo.status in (
        Article.Status.PUBLISHED,
        Article.Status.SUPERSEDED,
        Article.Status.REJECTED,
    ):
        return {}
    pauta = artigo.topic
    por_curar = ((pauta.busca_de_fontes or {}).get("acervo") or {}).get("por_curar") or []
    pdfs = pedidos_em_aberto(pauta) if artigo.fluxo == "pesquisa" else 0
    if artigo.fluxo == "pesquisa":
        por_curar = []  # o B usa os resumos da pesquisa, nao o acervo curado
    return {"por_curar": len(por_curar), "pdfs": pdfs} if (por_curar or pdfs) else {}


def _nome_do_fluxo(fluxo: str) -> str:
    from apps.content.fluxos import NOMES

    return NOMES.get(fluxo, "")


def _irmaos(artigo) -> list:
    from apps.content.fluxos import NOMES, irmaos

    saida = irmaos(artigo)
    for outro in saida:
        outro.nome_do_fluxo = NOMES.get(outro.fluxo, "")
    return saida


def _lotes_de_capa(artigo) -> list[dict]:
    """As opcoes agrupadas por rodada de geracao, com o prompt de cada rodada.

    Agrupadas, e nao numa lista unica, para que se veja o que mudou quando a
    pessoa pediu mais exemplos — comparar dentro do lote e entre lotes sao duas
    leituras diferentes.

    O prompt sai no LOTE e nao em cada opcao porque e ali que ele existe: uma
    descricao e escrita por rodada e as tres imagens saem dela. O worker de GPU
    devolve `revised_prompt` igual ao que recebeu, entao repetir o texto tres
    vezes seria repetir a mesma coisa tres vezes.

    `divergentes` cobre o caso oposto, que ja existe em provedor pago: o
    dall-e-3 reescreve o prompt POR IMAGEM, e o que ele desenhou passa a ser
    diferente do que se pediu. Quando isso acontece, cada opcao mostra o seu.
    """
    lotes: dict[int, list] = {}
    for imagem in artigo.images.all():
        lotes.setdefault(imagem.batch, []).append(imagem)

    resultado = []
    for numero, opcoes in sorted(lotes.items()):
        prompts = {(opcao.prompt or "").strip() for opcao in opcoes}
        # Um so quando todas concordam; vazio quando divergem, para a tela nao
        # eleger arbitrariamente o prompt da primeira opcao.
        unico = next(iter(prompts)) if len(prompts) == 1 else ""
        resultado.append(
            {
                "numero": numero,
                "opcoes": opcoes,
                "prompt": unico,
                "divergentes": len(prompts) > 1,
            }
        )
    return resultado


def _motivo_sem_capa(artigo) -> str:
    """Por que o pedido de capa mais recente nao produziu nada.

    O passo de capa do fluxo do artigo nao derruba o trabalho: ele grava o
    motivo e segue, porque a essa altura o artigo ja esta pronto
    (apps/content/flows.py). O botao "gerar mais", ao contrario, termina FALHO.
    Nos dois casos, sem trazer o motivo para ca a tela ficaria igual a de quem
    nunca pediu — ou, com lotes anteriores na tela, igual a antes do clique.

    Olha so o trabalho MAIS RECENTE: um lote que saiu depois de uma falha
    responde por ela.
    """

    # Os dois caminhos que geram capa: o passo do fluxo do artigo (que aponta
    # para a PAUTA) e o botao da tela (que aponta para o ARTIGO).
    #
    # O ramo da pauta so entra quando ela existe: `target_object_id` e UUID, e
    # um artigo sem pauta produziria a string "None", que o banco recusa ao
    # converter.
    alvos = Q(kind=GenerationJob.Kind.ARTICLE_COVER, target_object_id=str(artigo.pk))
    if artigo.topic_id:
        alvos |= Q(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(artigo.topic_id))

    job = GenerationJob.objects.filter(alvos).order_by("-created_at").first()
    if job is None:
        return ""

    if job.kind == GenerationJob.Kind.ARTICLE_COVER and job.status == GenerationJob.Status.FAILED:
        return job.last_error

    for payload in (job.step_payloads or {}).values():
        # `capas` no payload identifica o passo da capa sem depender do numero
        # dele, que muda quando um passo novo entra na frente.
        if isinstance(payload, dict) and "capas" in payload and payload.get("motivo"):
            return str(payload["motivo"])
    return ""


def _capas_em_curso(artigo):
    """Um lote de capas ainda rodando para este artigo.

    Separado de `_trabalho_em_curso` de proposito: refazer secoes reescreve o
    TEXTO na tela, e por isso bloqueia a edicao; gerar capa nao toca no texto,
    entao quem revisa pode continuar trabalhando enquanto o lote sai. Juntar os
    dois faria o botao "refazer" sumir porque uma imagem esta sendo desenhada.
    """

    return (
        GenerationJob.objects.filter(
            kind=GenerationJob.Kind.ARTICLE_COVER,
            target_object_id=str(artigo.pk),
            status__in=[
                GenerationJob.Status.PENDING,
                GenerationJob.Status.RUNNING,
                GenerationJob.Status.WAITING_CAPACITY,
            ],
        )
        .order_by("-created_at")
        .first()
    )


def _trabalho_em_curso(artigo):
    """Um trabalho de refazer ainda rodando para este artigo.

    A tela precisa saber: enquanto ele roda, o texto na tela e o antigo, e
    oferecer "refazer" de novo criaria dois trabalhos escrevendo as mesmas
    secoes.
    """

    return (
        GenerationJob.objects.filter(
            kind__in=[
                GenerationJob.Kind.ARTICLE_REDRAFT,
                GenerationJob.Kind.ARTICLE_REPLAN,
            ],
            target_object_id=artigo.pk,
            status__in=[
                GenerationJob.Status.PENDING,
                GenerationJob.Status.RUNNING,
                GenerationJob.Status.WAITING_CAPACITY,
            ],
        )
        .order_by("-created_at")
        .first()
    )


def _ao_artigo(artigo, aba: str = "") -> HttpResponse:
    """Volta para a pagina do artigo, na aba em que a pessoa estava."""
    return redirect(reverse("content:revisar", args=[artigo.pk]) + (f"#{aba}" if aba else ""))


def _processar_revisao(request: HttpRequest, artigo: Article) -> HttpResponse:
    acao = request.POST.get("acao", "salvar")

    if acao == "rejeitar":
        artigo.status = Article.Status.REJECTED
        artigo.reviewed_by = request.user
        artigo.reviewed_at = timezone.now()
        artigo.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        messages.success(request, _("Artigo rejeitado."))
        return redirect("content:artigos")

    form = RevisaoDeArtigo(request.POST)
    if not form.is_valid():
        return render(
            request,
            "content/revisar.html",
            _contexto_de_revisao(request, artigo, form=form),
            status=400,
        )

    dados = form.cleaned_data
    artigo.title = dados["title"]
    artigo.meta_description = dados["meta_description"]

    autor = dados.get("author")
    if autor is not None:
        artigo.author = autor
        # Retrato do que foi assinado. O cadastro pode ser renomeado depois, e
        # renomear alguem nao pode reescrever a assinatura de um artigo que ja
        # saiu.
        artigo.author_name = autor.name
        artigo.author_credentials = autor.credentials

    artigo.save(
        update_fields=["title", "meta_description", "author", "author_name", "author_credentials"]
    )

    if dados["body_markdown"] != artigo.body_markdown:
        # Guarda a versao e mede quanto o humano de fato mudou — o numero que
        # diz se a revisao esta sendo revisao ou carimbo.
        aplicar_edicao_humana(artigo, dados["body_markdown"], editor=request.user)
        artigo.refresh_from_db()

    if acao not in ("aprovar", "publicar_ja"):
        messages.success(request, _("Alteracoes salvas."))
        return redirect("content:revisar", pk=artigo.pk)

    agendamento = AgendamentoForm(request.POST)
    if not agendamento.is_valid():
        return render(
            request,
            "content/revisar.html",
            _contexto_de_revisao(request, artigo, form=form, agendamento=agendamento),
            status=400,
        )

    if agendamento.cleaned_data["confirmar_divergencia"]:
        # A confirmacao vive na tese porque e sobre ela que a trava pergunta.
        tese = dict(artigo.thesis_json or {})
        tese["divergencia_confirmada"] = True
        artigo.thesis_json = tese
        artigo.save(update_fields=["thesis_json"])

    site = _site()
    try:
        aprovar_e_agendar(
            artigo,
            revisor=request.user,
            # Versao nova de artigo no ar nao entra na cadencia: e a mesma
            # pagina, e cada dia com o texto antigo e um dia perdido.
            # "Publicar ja": agora, fora da cadencia, sem mexer na configuracao.
            quando=timezone.now()
            if acao == "publicar_ja"
            else agendamento.cleaned_data["quando"]
            or (timezone.now() if artigo.e_atualizacao else _proximo_horario()),
            exige_revisor_tecnico=bool(site and site.is_sensitive),
            termos_confirmados=agendamento.cleaned_data["confirmar_termos"],
        )
    except RevisaoInsuficiente as exc:
        # Nao e validacao de formulario: e uma condicao do produto, e a
        # mensagem dela e a explicacao.
        messages.error(request, str(exc))
        return redirect("content:revisar", pk=artigo.pk)

    if acao == "publicar_ja":
        from apps.integrations.tasks import publicar_vencidos

        # O mesmo caminho do agendador (que roda a cada minuto), so que ja.
        transaction.on_commit(lambda: publicar_vencidos(limite=50))
        messages.success(
            request, _("Artigo aprovado e enviado ao site agora. Confira em instantes.")
        )
        return redirect("content:revisar", pk=artigo.pk)

    messages.success(request, _("Artigo aprovado e agendado."))
    return redirect("content:artigos")


@login_required
@require_POST
def nova_versao(request: HttpRequest, pk) -> HttpResponse:
    """Cria a versao seguinte de um artigo publicado e abre a revisao dela."""
    from apps.content.versoes import VersaoRecusada, criar_nova_versao

    artigo = get_object_or_404(Article, pk=pk)
    try:
        nova = criar_nova_versao(artigo, notas=request.POST.get("notas", ""), por=request.user)
    except VersaoRecusada as exc:
        messages.error(request, str(exc))
        return redirect("content:revisar", pk=artigo.pk)
    messages.success(
        request,
        _("Versao %(n)s criada. Edite e aprove: ela substitui a pagina no site.")
        % {"n": nova.version_number},
    )
    return redirect("content:revisar", pk=nova.pk)


@login_required
@require_POST
def gerar_de_novo(request: HttpRequest, pk) -> HttpResponse:
    from apps.content import fluxos

    artigo = get_object_or_404(Article, pk=pk)
    nivel, mensagem = fluxos.gerar_de_novo(artigo)
    getattr(messages, nivel)(request, mensagem)
    return redirect("content:revisar", pk=artigo.pk)


@login_required
@require_POST
def salvar_secoes(request: HttpRequest, pk) -> HttpResponse:
    """Grava o que a pessoa editou a mao nas secoes, e remonta o artigo.

    Editar a secao e nao o texto final e o que permite refazer so aquela parte
    depois: o texto do artigo e derivado das secoes, e nao o contrario.
    """
    from apps.content.models import ArticleSection
    from apps.content.services import montar_markdown_das_secoes

    artigo = get_object_or_404(Article, pk=pk)
    alteradas = 0

    for secao in artigo.sections.all():
        texto = (request.POST.get(f"secao_{secao.order}") or "").strip()
        titulo = (request.POST.get(f"titulo_{secao.order}") or "").strip()
        mudou = False

        if titulo and titulo != secao.heading:
            secao.heading = titulo[:200]
            mudou = True
        if texto != secao.body_markdown.strip():
            secao.body_markdown = texto
            secao.status = ArticleSection.Status.EDITED
            mudou = True

        if mudou:
            secao.save(update_fields=["heading", "body_markdown", "status", "updated_at"])
            alteradas += 1

    if alteradas:
        aplicar_edicao_humana(artigo, montar_markdown_das_secoes(artigo), editor=request.user)
        messages.success(
            request,
            _("%(total)s secao(oes) salvas e artigo remontado.") % {"total": alteradas},
        )
    else:
        messages.info(request, _("Nada mudou."))

    return _ao_artigo(artigo, "secoes")


def _conferencia_editorial(artigo):
    """Termos proibidos e marcas de maquina no texto atual, para a tela."""
    from apps.editorial.services import conferir_texto, perfil_atual

    perfil = perfil_atual()
    texto = "\n".join([artigo.title, artigo.meta_description, artigo.body_markdown])
    return conferir_texto(texto, perfil)


@login_required
@require_POST
def mudar_chamada(request: HttpRequest, pk) -> HttpResponse:
    """Onde vai a chamada para a oferta: nenhuma, so no fim, ou tambem no meio."""
    from apps.content.chamada import mudar

    artigo = get_object_or_404(Article, pk=pk)
    modo = request.POST.get("modo")
    if modo not in {valor for valor, _rotulo in CHAMADAS}:
        messages.error(request, _("Escolha onde vai a chamada."))
        return _ao_artigo(artigo, "extras")
    secao = request.POST.get("secao")
    mudar(artigo, modo, int(secao) if (secao or "").isdigit() else None, editor=request.user)
    if modo != "none":
        # O texto da chamada fala da secao onde ela entra: mudou o lugar, reescreve.
        from apps.content.tasks import escrever_chamada

        transaction.on_commit(lambda: escrever_chamada.delay(str(artigo.pk)))
    messages.success(request, _("Chamada atualizada no texto."))
    return _ao_artigo(artigo, "extras")


@login_required
@require_POST
def texto_da_chamada(request: HttpRequest, pk) -> HttpResponse:
    """Grava o texto da chamada editado a mao, ou pede para escrever de novo."""
    from apps.content.chamada import _limpar_texto
    from apps.content.tasks import escrever_chamada

    artigo = get_object_or_404(Article, pk=pk)
    if request.POST.get("acao") == "escrever":
        transaction.on_commit(lambda: escrever_chamada.delay(str(artigo.pk)))
        messages.success(
            request, _("Escrevendo o texto da chamada. Atualize a pagina em instantes.")
        )
        return _ao_artigo(artigo, "extras")
    texto = {}
    for onde in ("inline", "end"):
        partes = _limpar_texto(
            {c: request.POST.get(f"{onde}_{c}", "") for c in ("title", "text", "button")}
        )
        if partes:
            texto[onde] = partes
    artigo.call_to_action_copy = texto
    artigo.save(update_fields=["call_to_action_copy"])
    messages.success(request, _("Texto da chamada salvo."))
    return _ao_artigo(artigo, "extras")


@login_required
@require_POST
def conferir_indexacao(request: HttpRequest, pk) -> HttpResponse:
    """Pergunta ao Google, agora, se o artigo publicado esta no indice."""
    from apps.radar.indexacao import conferir
    from apps.radar.provedores import ProvedorIndisponivel

    artigo = get_object_or_404(Article, pk=pk)
    try:
        dados = conferir(artigo)
    except ProvedorIndisponivel as exc:
        messages.error(request, _("Nao foi possivel conferir: %(erro)s") % {"erro": exc})
    else:
        if dados["indexada"]:
            messages.success(request, _("O artigo esta no Google."))
        else:
            messages.info(request, _("O artigo ainda nao esta no Google."))
    return _ao_artigo(artigo, "revisar")


@login_required
@require_POST
def salvar_faq(request: HttpRequest, pk) -> HttpResponse:
    """Marca, edita, apaga e acrescenta perguntas frequentes, num formulario so."""
    from apps.content.faq import salvar_revisao

    artigo = get_object_or_404(Article, pk=pk)
    if salvar_revisao(artigo, request.POST):
        messages.success(request, _("Perguntas frequentes salvas."))
    else:
        messages.info(request, _("Nada mudou."))
    return _ao_artigo(artigo, "extras")


@login_required
@require_POST
def refazer_secoes(request: HttpRequest, pk) -> HttpResponse:
    """Reescreve APENAS as secoes marcadas. Uma chamada por secao."""
    from apps.content.services import marcar_secoes_para_refazer
    from apps.ops.orchestrator import criar_job
    from apps.ops.tasks import advance_generation_job

    artigo = get_object_or_404(Article, pk=pk)

    if _trabalho_em_curso(artigo):
        messages.error(request, _("Ja ha um trabalho refazendo este artigo. Aguarde."))
        return _ao_artigo(artigo, "secoes")

    # Os parametros sao guardados ANTES da conferencia das secoes. Quem ajusta
    # a palavra-chave e esquece de marcar uma secao nao pode perder o ajuste
    # junto com o clique.
    _guardar_parametros(request, artigo)

    ordens = {int(v) for v in request.POST.getlist("refazer") if v.isdigit()}
    if not ordens:
        messages.error(
            request, _("Parametros salvos. Marque ao menos uma secao para refazer o texto.")
        )
        return _ao_artigo(artigo, "secoes")

    total = marcar_secoes_para_refazer(artigo, ordens)

    job = criar_job(kind=GenerationJob.Kind.ARTICLE_REDRAFT, target_object_id=str(artigo.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))

    messages.success(
        request,
        _("Refazendo %(total)s secao(oes). O resto do artigo fica como esta.") % {"total": total},
    )
    return _ao_artigo(artigo, "secoes")


@login_required
def replanejar(request: HttpRequest, pk) -> HttpResponse:
    """Descarta o esqueleto e recomeca do plano.

    Em duas etapas, como a exclusao de documento: o GET diz o que se perde. E a
    acao mais cara da tela e a mais facil de acionar por engano — alguem que
    queria consertar uma secao pode perder cinco boas.
    """
    from apps.content.services import limpar_plano
    from apps.ops.orchestrator import criar_job
    from apps.ops.tasks import advance_generation_job

    artigo = get_object_or_404(Article, pk=pk)

    if request.method != "POST":
        return render(
            request,
            "content/replanejar.html",
            {"aba": "artigos", "artigo": artigo, "secoes": artigo.sections.all()},
        )

    if _trabalho_em_curso(artigo):
        messages.error(request, _("Ja ha um trabalho refazendo este artigo. Aguarde."))
        return redirect("content:revisar", pk=artigo.pk)

    _guardar_parametros(request, artigo)
    limpar_plano(artigo)

    job = criar_job(kind=GenerationJob.Kind.ARTICLE_REPLAN, target_object_id=str(artigo.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))

    messages.success(request, _("Replanejando o artigo do zero, com as mesmas fontes."))
    return redirect("content:revisar", pk=artigo.pk)


def _guardar_parametros(request: HttpRequest, artigo: Article) -> None:
    """Aplica os parametros de geracao antes de refazer.

    Refazer com os mesmos parametros costuma devolver a mesma coisa. Sao estes
    campos que mudam o resultado: sem poder ajusta-los, "refazer" vira "tentar a
    sorte".
    """
    campos = []

    palavra = (request.POST.get("palavra_chave") or "").strip()
    if palavra and palavra != artigo.focus_keyword:
        artigo.focus_keyword = palavra[:120]
        campos.append("focus_keyword")

    secundarias = [
        termo.strip()
        for termo in (request.POST.get("palavras_secundarias") or "").split(",")
        if termo.strip()
    ]
    if secundarias != artigo.secondary_keywords:
        artigo.secondary_keywords = secundarias[:8]
        campos.append("secondary_keywords")

    for campo, nome in (("publico", "audience"), ("intencao", "search_intent")):
        valor = (request.POST.get(campo) or "").strip()
        if valor != getattr(artigo, nome):
            setattr(artigo, nome, valor[:200])
            campos.append(nome)

    posicao = (request.POST.get("posicao_dos_links") or "").strip()
    validas = {valor for valor, _ in Article.LinkPlacement.choices}
    if posicao in validas and posicao != artigo.link_placement:
        artigo.link_placement = posicao
        campos.append("link_placement")

    if campos:
        artigo.save(update_fields=campos)


# ---------------------------------------------------------------------------
# Capa: opcoes de imagem, e a escolha
# ---------------------------------------------------------------------------
@login_required
@require_POST
def gerar_capas(request: HttpRequest, pk) -> HttpResponse:
    """Pede um lote novo de opcoes de imagem, pela fila.

    Era sincrono, com a justificativa de que "sao segundos, a pessoa esta
    olhando a tela". A medicao desmentiu: numa placa dividida com um modelo de
    texto, um lote de tres passa de minutos — e a primeira geracao de todas
    baixa alguns GB antes. O navegador ficava girando ate o tempo esgotar, o
    worker seguia desenhando, e o clique seguinte batia num servico ocupado.

    Agora e um trabalho como os outros: entra na fila, aparece em Operacao, e
    a tela diz que esta em curso. O passo executado e o MESMO do fluxo do
    artigo (`passo_gerar_capas`), entao as duas portas nao podem divergir.
    """
    from apps.content.capas import MAXIMO_DE_LOTES, ha_conexao_de_imagem
    from apps.ops.orchestrator import criar_job
    from apps.ops.tasks import advance_generation_job

    artigo = get_object_or_404(Article, pk=pk)

    # Conferido ANTES de enfileirar. Sem gerador cadastrado, mandar para a fila
    # so adiaria a mesma mensagem — e quem clicou ficaria esperando um lote que
    # nunca vem, com a tela dizendo "gerando".
    if not ha_conexao_de_imagem():
        messages.error(
            request,
            _(
                "Nenhuma conexao de geracao de imagem disponivel. Cadastre uma "
                "em Configuracao > Inferencia, do tipo 'image'."
            ),
        )
        return _ao_artigo(artigo, "capa")

    if _capas_em_curso(artigo):
        messages.error(request, _("Ja ha um lote de capas sendo gerado. Aguarde."))
        return _ao_artigo(artigo, "capa")

    # O teto e conferido aqui e tambem dentro de `gerar_opcoes`. Aqui para a
    # pessoa saber na hora do clique; la porque o fluxo do artigo tambem chama.
    ultimo_lote = artigo.images.order_by("-batch").values_list("batch", flat=True).first() or 0
    if ultimo_lote >= MAXIMO_DE_LOTES:
        messages.error(
            request,
            _("Este artigo ja tem %(total)s lotes de imagem. Escolha uma das opcoes existentes.")
            % {"total": MAXIMO_DE_LOTES},
        )
        return _ao_artigo(artigo, "capa")

    job = criar_job(kind=GenerationJob.Kind.ARTICLE_COVER, target_object_id=str(artigo.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))

    messages.success(
        request,
        _(
            "Gerando tres opcoes de capa. Pode levar alguns minutos — a placa e "
            "dividida com a geracao de texto. Atualize a pagina para ver."
        ),
    )
    return _ao_artigo(artigo, "capa")


@login_required
@require_POST
def escolher_capa(request: HttpRequest, pk) -> HttpResponse:
    """Marca a opcao escolhida como a capa do artigo."""
    from apps.content.capas import escolher_capa as marcar
    from apps.content.models import ArticleImage

    artigo = get_object_or_404(Article, pk=pk)
    imagem = get_object_or_404(ArticleImage, pk=request.POST.get("imagem"), article=artigo)

    marcar(artigo, imagem)
    messages.success(request, _("Capa escolhida."))
    return _ao_artigo(artigo, "capa")


def capa_publica(request: HttpRequest, pk) -> HttpResponse:
    """Serve a capa escolhida, sem sessao. E a unica midia publica do sistema.

    **Sem `login_required` de proposito**: quem busca esta imagem e o site de
    destino, do outro lado da internet, sem credencial nenhuma. E a mesma
    imagem que ele vai publicar na propria pagina.

    Tres condicoes, e as tres importam:

    * so a opcao ESCOLHIDA sai — as outras sao rascunho, e um lote inteiro
      acessivel por URL entregaria material descartado;
    * so de artigo ja aprovado — antes disso nada deveria estar visivel fora;
    * o id e UUID, entao nao ha como enumerar as capas de um tenant.

    O resto da midia (os PDFs do acervo) continua fora do alcance publico: o
    Nginx serve `/protected-media/` como `internal`.
    """
    from apps.content.models import ArticleImage

    imagem = get_object_or_404(
        ArticleImage.objects.select_related("article"),
        pk=pk,
        is_chosen=True,
        article__status__in=[
            Article.Status.APPROVED_SCHEDULED,
            Article.Status.PUBLISHED,
            Article.Status.PUSH_FAILED,
        ],
    )
    from core.arquivos import entregar_arquivo

    return entregar_arquivo(imagem.image, tipo="image/webp")


# ---------------------------------------------------------------------------
# Autores
# ---------------------------------------------------------------------------
@login_required
def autores(request: HttpRequest) -> HttpResponse:
    """Quem pode assinar o conteudo deste ambiente.

    O cadastro vive so aqui. O site de destino nao conhece o PubliBot antes de
    receber a primeira publicacao: os dados do autor chegam junto do conteudo,
    e a responsabilidade fica com quem validou o texto.
    """
    return render(
        request,
        "content/autores.html",
        {
            "aba": "autores",
            "autores": Author.objects.annotate(total=Count("articles")).order_by("name"),
        },
    )


@login_required
def editar_autor(request: HttpRequest, pk=None) -> HttpResponse:
    from apps.content.forms import CadastroDeAutor

    autor = get_object_or_404(Author, pk=pk) if pk else None

    if request.method != "POST":
        return render(
            request,
            "content/autor.html",
            {"aba": "autores", "autor": autor, "form": CadastroDeAutor(instance=autor)},
        )

    form = CadastroDeAutor(request.POST, request.FILES, instance=autor)
    if not form.is_valid():
        return render(
            request,
            "content/autor.html",
            {"aba": "autores", "autor": autor, "form": form},
            status=400,
        )

    salvo = form.save(commit=False)
    if form.cleaned_data.get("remover_foto"):
        salvo.photo = None
    salvo.social_links = _links_do_formulario(request)
    salvo.save()

    messages.success(request, _("Autor salvo."))
    return redirect("content:autores")


def _links_do_formulario(request: HttpRequest) -> list[dict]:
    """Le os pares rotulo/endereco das linhas preenchidas.

    Sem formset: sao poucos campos, sempre juntos, e um formset traria
    gerenciamento de indice e um `management_form` para resolver um problema
    que nao existe aqui.
    """
    links = []
    for rotulo, endereco in zip(
        request.POST.getlist("link_label"), request.POST.getlist("link_url"), strict=False
    ):
        endereco = (endereco or "").strip()
        if endereco:
            links.append({"label": (rotulo or "").strip()[:40], "url": endereco[:300]})
    return links


@login_required
@require_POST
def excluir_autor(request: HttpRequest, pk) -> HttpResponse:
    autor = get_object_or_404(Author, pk=pk)
    nome = autor.name
    # `SET_NULL` no artigo: a assinatura ja publicada continua, porque o nome
    # foi copiado para `author_name` no momento da publicacao.
    autor.delete()
    messages.success(request, _("Autor %(nome)s excluido.") % {"nome": nome})
    return redirect("content:autores")


# ---------------------------------------------------------------------------
# Perguntas e respostas
# ---------------------------------------------------------------------------
@login_required
def perguntas(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "content/perguntas.html",
        {
            "aba": "perguntas",
            "perguntas": Question.objects.select_related("answer").order_by("-submitted_at")[:200],
        },
    )


@login_required
@require_POST
def responder(request: HttpRequest, pk) -> HttpResponse:
    pergunta = get_object_or_404(Question, pk=pk)
    if hasattr(pergunta, "answer"):
        messages.error(request, _("Esta pergunta ja tem resposta."))
        return redirect("content:perguntas")

    responder_pergunta(pergunta)
    Question.objects.filter(pk=pergunta.pk).update(status=Question.Status.DRAFTING)
    messages.success(request, _("Resposta em producao. Ela tambem passa por revisao."))
    return redirect("content:perguntas")


@login_required
@require_POST
def responder_a_mao(request: HttpRequest, pk) -> HttpResponse:
    """Abre uma resposta em branco para a pessoa escrever.

    Existe para os dois casos que a geracao nao cobre: o acervo nao sustentar a
    pergunta — e ai nao ha texto automatico possivel, so o silencio ou a
    invencao — e a pessoa simplesmente preferir escrever. Sem esta porta, uma
    pergunta sem fonte no acervo ficaria parada para sempre.

    A resposta escrita a mao entra pela MESMA revisao e pela mesma aprovacao da
    gerada. Um atalho aqui seria uma segunda porta para o site do cliente.
    """
    pergunta = get_object_or_404(Question, pk=pk)
    if hasattr(pergunta, "answer"):
        messages.error(request, _("Esta pergunta ja tem resposta."))
        return redirect("content:perguntas")

    resposta = Answer.objects.create(
        question=pergunta,
        origin=Answer.Origin.MANUAL,
        status=Answer.Status.PENDING_REVIEW,
    )
    Question.objects.filter(pk=pergunta.pk).update(status=Question.Status.PENDING_REVIEW)

    messages.success(request, _("Escreva a resposta. Ela passa pela mesma aprovacao."))
    return redirect("content:revisar_resposta", pk=resposta.pk)


@login_required
@require_POST
def descartar_pergunta(request: HttpRequest, pk) -> HttpResponse:
    pergunta = get_object_or_404(Question, pk=pk)
    pergunta.status = Question.Status.DISCARDED
    pergunta.save(update_fields=["status"])
    messages.success(request, _("Pergunta descartada."))
    return redirect("content:perguntas")


@login_required
def revisar_resposta(request: HttpRequest, pk) -> HttpResponse:
    resposta = get_object_or_404(Answer.objects.select_related("question"), pk=pk)

    if request.method == "POST":
        return _processar_resposta(request, resposta)

    return render(
        request,
        "content/revisar_resposta.html",
        {
            "aba": "perguntas",
            "resposta": resposta,
            "form": RevisaoDeResposta(
                initial={
                    "body_markdown": resposta.body_markdown,
                    "author": resposta.author_id,
                }
            ),
            "agendamento": AgendamentoForm(),
            "citacoes": _fontes_da_resposta(resposta),
            "pesquisa": resposta.question.pesquisa if resposta.question_id else {},
            "proximo_horario": _proximo_horario(),
            "tem_autores": Author.objects.filter(is_active=True).exists(),
        },
    )


def _fontes_da_resposta(resposta) -> list:
    """As citacoes com o que quem confere precisa: titulo, autores e ano, o
    link, o trecho que o modelo leu, e, no artigo cientifico, se foi lido so o
    resumo e onde esta o PDF (aberto, ou pelo DOI)."""
    from apps.knowledge.models import CandidatoDeFonte, Document

    citacoes = list(resposta.citations.select_related("super_chunk__document").order_by("rank"))
    documentos = [c.super_chunk.document_id for c in citacoes if c.super_chunk_id]
    candidatos = {
        c.documento_id: c for c in CandidatoDeFonte.objects.filter(documento_id__in=documentos)
    }
    for citacao in citacoes:
        trecho = citacao.super_chunk
        documento = trecho.document if trecho else None
        candidato = candidatos.get(documento.pk) if documento else None
        citacao.documento = documento
        citacao.trecho = (trecho.content if trecho else "")[:1200]
        citacao.so_resumo = bool(
            documento and documento.extraction_method == Document.ExtractionMethod.RESUMO
        )
        citacao.pdf = (candidato.pdf_url if candidato else "") or ""
        citacao.doi = ((candidato.doi if candidato else "") or "").removeprefix("https://doi.org/")
    return citacoes


def _processar_resposta(request: HttpRequest, resposta: Answer) -> HttpResponse:
    acao = request.POST.get("acao", "salvar")

    if acao == "rejeitar":
        resposta.status = Answer.Status.REJECTED
        resposta.save(update_fields=["status"])
        messages.success(request, _("Resposta rejeitada."))
        return redirect("content:perguntas")

    form = RevisaoDeResposta(request.POST)
    if not form.is_valid():
        messages.error(request, _("Confira os campos."))
        return redirect("content:revisar_resposta", pk=resposta.pk)

    from apps.content.rendering import markdown_para_html

    dados = form.cleaned_data
    resposta.body_markdown = dados["body_markdown"]
    resposta.body_html = markdown_para_html(dados["body_markdown"])

    autor = dados.get("author")
    if autor is not None:
        resposta.author = autor
        # Retrato da assinatura, como no artigo: renomear alguem no cadastro
        # nao reescreve o que ja foi publicado.
        resposta.author_name = autor.name
        resposta.author_credentials = autor.credentials

    resposta.save()

    if acao != "aprovar":
        messages.success(request, _("Alteracoes salvas."))
        return redirect("content:revisar_resposta", pk=resposta.pk)

    agendamento = AgendamentoForm(request.POST)
    if not agendamento.is_valid():
        messages.error(request, _("Data invalida."))
        return redirect("content:revisar_resposta", pk=resposta.pk)

    site = _site()
    try:
        aprovar_resposta_e_agendar(
            resposta,
            revisor=request.user,
            quando=agendamento.cleaned_data["quando"] or _proximo_horario(),
            exige_revisor_tecnico=bool(site and site.is_sensitive),
        )
    except RevisaoInsuficiente as exc:
        messages.error(request, str(exc))
        return redirect("content:revisar_resposta", pk=resposta.pk)

    messages.success(request, _("Resposta aprovada e agendada."))
    return redirect("content:perguntas")
