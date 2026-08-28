"""Telas de pauta, artigo, revisao e resposta."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.content.forms import (
    AgendamentoForm,
    PautaForm,
    RevisaoDeArtigo,
    RevisaoDeResposta,
)
from apps.content.models import Answer, Article, Question, Topic
from apps.content.services import (
    RevisaoInsuficiente,
    aplicar_edicao_humana,
    aprovar_e_agendar,
    aprovar_resposta_e_agendar,
)
from apps.content.tasks import gerar_artigo, responder_pergunta

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
    consulta = Topic.objects.annotate(artigos=Count("articles")).order_by("-created_at")
    if situacao:
        consulta = consulta.filter(status=situacao)

    return render(
        request,
        "content/pautas.html",
        {
            "aba": "pautas",
            "pautas": consulta[:200],
            "situacao": situacao,
            "form": PautaForm(),
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
    return redirect("content:pautas")


@login_required
@require_POST
def gerar(request: HttpRequest, pk) -> HttpResponse:
    """Dispara a geracao do artigo para uma pauta."""
    pauta = get_object_or_404(Topic, pk=pk)

    if pauta.articles.exists():
        messages.error(request, _("Esta pauta ja tem artigo. Gerar de novo criaria concorrencia."))
        return redirect("content:pautas")

    job = gerar_artigo(pauta)
    messages.success(
        request,
        _(
            "Geracao iniciada. Ela busca fontes no acervo, consolida a tese e "
            "escreve — acompanhe em Operacao (trabalho %(id)s)."
        )
        % {"id": str(job.pk)[:8]},
    )
    return redirect("content:pautas")


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
    consulta = Article.objects.select_related("topic").order_by("-updated_at")
    if situacao:
        consulta = consulta.filter(status=situacao)

    contagens = dict(
        Article.objects.values_list("status").annotate(total=Count("status")).order_by()
    )

    return render(
        request,
        "content/artigos.html",
        {
            "aba": "artigos",
            "artigos": consulta[:200],
            "situacao": situacao,
            "situacoes": [
                (valor, rotulo, contagens.get(valor, 0)) for valor, rotulo in Article.Status.choices
            ],
        },
    )


@login_required
def revisar(request: HttpRequest, pk) -> HttpResponse:
    """A tela central: ler o texto ao lado das fontes que o sustentam."""
    artigo = get_object_or_404(Article.objects.select_related("topic", "primary_source"), pk=pk)

    if request.method == "POST":
        return _processar_revisao(request, artigo)

    return render(request, "content/revisar.html", _contexto_de_revisao(request, artigo))


def _contexto_de_revisao(request, artigo, form=None, agendamento=None) -> dict:
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
                "author_name": artigo.author_name or getattr(site, "default_author", ""),
                "author_credentials": artigo.author_credentials
                or getattr(site, "default_author_credentials", ""),
            }
        ),
        "agendamento": agendamento or AgendamentoForm(),
        "citacoes": artigo.citations.select_related("super_chunk").order_by("rank"),
        "revisoes": artigo.revisions.order_by("-version")[:10],
        "site": site,
        "proximo_horario": _proximo_horario(),
    }


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
    artigo.author_name = dados["author_name"]
    artigo.author_credentials = dados["author_credentials"]
    artigo.save(update_fields=["title", "meta_description", "author_name", "author_credentials"])

    if dados["body_markdown"] != artigo.body_markdown:
        # Guarda a versao e mede quanto o humano de fato mudou — o numero que
        # diz se a revisao esta sendo revisao ou carimbo.
        aplicar_edicao_humana(artigo, dados["body_markdown"], editor=request.user)
        artigo.refresh_from_db()

    if acao != "aprovar":
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
            quando=agendamento.cleaned_data["quando"] or _proximo_horario(),
            exige_revisor_tecnico=bool(site and site.is_sensitive),
        )
    except RevisaoInsuficiente as exc:
        # Nao e validacao de formulario: e uma condicao do produto, e a
        # mensagem dela e a explicacao.
        messages.error(request, str(exc))
        return redirect("content:revisar", pk=artigo.pk)

    messages.success(request, _("Artigo aprovado e agendado."))
    return redirect("content:artigos")


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
                    "author_name": resposta.author_name,
                    "author_credentials": resposta.author_credentials,
                }
            ),
            "agendamento": AgendamentoForm(),
            "citacoes": resposta.citations.select_related("super_chunk").order_by("rank"),
            "proximo_horario": _proximo_horario(),
        },
    )


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
    resposta.author_name = dados["author_name"]
    resposta.author_credentials = dados["author_credentials"]
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
