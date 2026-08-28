"""Telas do acervo: envio, lista e curadoria."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.knowledge.blocos import preparar_blocos
from apps.knowledge.forms import CuradoriaDeDocumento, EnvioDeDocumento
from apps.knowledge.models import Document, DocumentCategory
from apps.knowledge.services import (
    blocos_marcados,
    indexar_blocos,
    ingerir_documento,
    marcar_curado,
    possiveis_duplicatas,
)
from apps.knowledge.tasks import iniciar_ingestao

logger = logging.getLogger("publibot.knowledge")


@login_required
def documentos(request: HttpRequest) -> HttpResponse:
    """Lista o acervo, filtrando por situacao."""
    situacao = request.GET.get("situacao", "")

    consulta = Document.objects.select_related("category").order_by("-created_at")
    if situacao:
        consulta = consulta.filter(status=situacao)

    # A contagem por situacao vem da tabela inteira, e nao do resultado
    # filtrado: senao o filtro escolhido zera todos os outros na tela.
    contagens = dict(
        Document.objects.values_list("status").annotate(total=Count("status")).order_by()
    )

    return render(
        request,
        "knowledge/documentos.html",
        {
            "aba": "documentos",
            "documentos": consulta[:200],
            "situacao": situacao,
            "situacoes": [
                (valor, rotulo, contagens.get(valor, 0))
                for valor, rotulo in Document.Status.choices
            ],
            "tem_categoria": DocumentCategory.objects.exists(),
        },
    )


@login_required
def enviar_documento(request: HttpRequest) -> HttpResponse:
    """Recebe o arquivo e o coloca na fila de conversao."""
    if not DocumentCategory.objects.exists():
        # Sem categoria o formulario nao tem o que oferecer. Dizer isso e
        # melhor que mostrar um campo vazio que nunca valida.
        messages.error(
            request,
            _("Crie ao menos uma categoria de documento antes de enviar arquivos."),
        )
        return redirect("knowledge:categorias")

    if request.method != "POST":
        return render(
            request,
            "knowledge/enviar.html",
            {"aba": "documentos", "form": EnvioDeDocumento()},
        )

    form = EnvioDeDocumento(request.POST, request.FILES)
    if not form.is_valid():
        return render(
            request, "knowledge/enviar.html", {"aba": "documentos", "form": form}, status=400
        )

    resultado = ingerir_documento(
        arquivo=form.cleaned_data["arquivo"],
        category=form.cleaned_data["category"],
        uploaded_by=request.user,
        title=form.cleaned_data["title"],
    )

    if resultado.ja_existia:
        # Deduplicado pelo hash do arquivo: o mesmo PDF enviado duas vezes nao
        # vira duas fontes, o que inflaria o consenso artificialmente.
        messages.info(
            request,
            _("Este arquivo ja estava no acervo; abrindo o documento existente."),
        )
        return redirect("knowledge:curar", pk=resultado.document.pk)

    if form.cleaned_data["source_url"]:
        resultado.document.source_url = form.cleaned_data["source_url"]
        resultado.document.save(update_fields=["source_url"])

    iniciar_ingestao(resultado.document)
    messages.success(
        request,
        _("Documento recebido. A conversao roda em segundo plano e a lista avisa quando terminar."),
    )
    return redirect("knowledge:documentos")


@login_required
def curar_documento(request: HttpRequest, pk) -> HttpResponse:
    """Confere os metadados e escolhe que partes do documento vao para o indice.

    A tela e o documento na ordem em que ele foi escrito: primeiro o que
    identifica a obra, depois os blocos que a extracao reconheceu. Marcar um
    bloco significa "esta parte pode sustentar um artigo".
    """
    documento = get_object_or_404(Document.objects.select_related("category"), pk=pk)

    if request.method == "POST":
        return _processar_curadoria(request, documento)

    return render(request, "knowledge/curar.html", _contexto_da_curadoria(documento))


def _contexto_da_curadoria(documento: Document, form=None) -> dict:
    return {
        "aba": "documentos",
        "documento": documento,
        "form": form or CuradoriaDeDocumento(instance=documento),
        "blocos": preparar_blocos(documento),
        "marcados": blocos_marcados(documento),
        "trechos": documento.chunks.order_by("block_index", "paragraph_index"),
        "duplicatas": possiveis_duplicatas(documento),
        "limite_de_tokens": settings.EMBEDDING_MAX_TOKENS,
    }


def _processar_curadoria(request: HttpRequest, documento: Document) -> HttpResponse:
    acao = request.POST.get("acao", "salvar")

    form = CuradoriaDeDocumento(request.POST, instance=documento)
    if not form.is_valid():
        return render(
            request,
            "knowledge/curar.html",
            _contexto_da_curadoria(documento, form=form),
            status=400,
        )

    documento = form.save(commit=False)
    # A procedencia deixa de ser "extraido automaticamente" no momento em que
    # uma pessoa confirma os campos.
    documento.metadata_confidence = Document.MetadataConfidence.MANUAL
    documento.save()

    marcados = {int(v) for v in request.POST.getlist("bloco") if v.isdigit()}

    try:
        criados = indexar_blocos(document=documento, blocos_marcados=marcados)
    except Exception as exc:
        # Indexar carrega o modelo de embedding — 2 GB, baixados na primeira
        # utilizacao. Sem rede isso levantava uma excecao de HTTP no meio da
        # requisicao e virava 500, sem nenhuma pista de que o problema era o
        # modelo e nao o texto.
        logger.exception("Falha ao indexar blocos do documento %s", documento.pk)
        messages.error(
            request,
            _(
                "Nao foi possivel vetorizar: %(erro)s. O modelo de embedding e "
                "baixado na primeira utilizacao (cerca de 2 GB) e precisa de rede."
            )
            % {"erro": str(exc)[:200]},
        )
        return redirect("knowledge:curar", pk=documento.pk)

    if acao == "concluir":
        if not criados:
            messages.error(
                request,
                _("Marque ao menos um bloco: sem trecho no indice o documento nao e citavel."),
            )
            return redirect("knowledge:curar", pk=documento.pk)

        marcar_curado(document=documento, revisado_por=request.user)
        messages.success(
            request,
            _("Documento curado com %(total)s trecho(s) no indice.") % {"total": criados},
        )
        return redirect("knowledge:documentos")

    messages.success(request, _("Salvo. %(total)s trecho(s) no indice.") % {"total": criados})
    return redirect("knowledge:curar", pk=documento.pk)


@login_required
@require_POST
def reprocessar(request: HttpRequest, pk) -> HttpResponse:
    """Reenvia o documento para conversao.

    Util depois de subir o worker com Docling: o que foi convertido pelo
    caminho de emergencia pode ser refeito com analise de layout.
    """
    documento = get_object_or_404(Document, pk=pk)
    iniciar_ingestao(documento)
    messages.success(request, _("Documento devolvido a fila de conversao."))
    return redirect(reverse("knowledge:curar", args=[documento.pk]))


@login_required
def categorias(request: HttpRequest) -> HttpResponse:
    """Categorias do acervo — o agrupamento que orienta a curadoria."""
    if request.method == "POST":
        nome = (request.POST.get("name") or "").strip()
        if nome:
            from django.utils.text import slugify

            DocumentCategory.objects.get_or_create(slug=slugify(nome)[:80], defaults={"name": nome})
            messages.success(request, _("Categoria criada."))
        return redirect("knowledge:categorias")

    return render(
        request,
        "knowledge/categorias.html",
        {
            "aba": "documentos",
            "categorias": DocumentCategory.objects.annotate(total=Count("documents")).order_by(
                "name"
            ),
        },
    )
