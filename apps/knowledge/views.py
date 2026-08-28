"""Telas do acervo: envio, lista e curadoria."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.knowledge.forms import CuradoriaDeDocumento, EnvioDeDocumento, SelecaoDeTrecho
from apps.knowledge.models import Document, DocumentCategory, SuperChunk
from apps.knowledge.services import (
    ChunkGrandeDemais,
    ingerir_documento,
    marcar_curado,
    possiveis_duplicatas,
    salvar_super_chunk,
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
    """Confirma os metadados e escolhe o trecho que representa o documento."""
    documento = get_object_or_404(Document.objects.select_related("category"), pk=pk)

    if request.method == "POST":
        return _processar_curadoria(request, documento)

    return render(
        request,
        "knowledge/curar.html",
        {
            "aba": "documentos",
            "documento": documento,
            "form": CuradoriaDeDocumento(instance=documento),
            "form_trecho": SelecaoDeTrecho(),
            "trechos": documento.chunks.order_by("kind"),
            "duplicatas": possiveis_duplicatas(documento),
        },
    )


def _processar_curadoria(request: HttpRequest, documento: Document) -> HttpResponse:
    acao = request.POST.get("acao", "salvar")

    if acao == "trecho":
        return _gravar_trecho(request, documento)

    form = CuradoriaDeDocumento(request.POST, instance=documento)
    if not form.is_valid():
        return render(
            request,
            "knowledge/curar.html",
            {
                "aba": "documentos",
                "documento": documento,
                "form": form,
                "form_trecho": SelecaoDeTrecho(),
                "trechos": documento.chunks.order_by("kind"),
                "duplicatas": possiveis_duplicatas(documento),
            },
            status=400,
        )

    documento = form.save(commit=False)
    # A procedencia deixa de ser "extraido automaticamente" no momento em que
    # uma pessoa confirma os campos.
    documento.metadata_confidence = Document.MetadataConfidence.MANUAL
    documento.save()
    # Os metadados sao copiados para os trechos na gravacao; refaze-los aqui
    # mantem a citacao coerente com o que acabou de ser corrigido.
    documento.chunks.update(
        source_title=documento.title,
        source_authors=documento.authors,
        source_year=documento.year,
        source_url=documento.source_url,
        source_authority=documento.authority_score,
    )

    if acao == "concluir":
        if not documento.chunks.exists():
            messages.error(
                request,
                _("Selecione ao menos um trecho antes de concluir: e ele que vai para o indice."),
            )
            return redirect("knowledge:curar", pk=documento.pk)

        marcar_curado(document=documento, revisado_por=request.user)
        messages.success(request, _("Documento curado e disponivel para gerar conteudo."))
        return redirect("knowledge:documentos")

    messages.success(request, _("Metadados salvos."))
    return redirect("knowledge:curar", pk=documento.pk)


def _gravar_trecho(request: HttpRequest, documento: Document) -> HttpResponse:
    form = SelecaoDeTrecho(request.POST)
    if not form.is_valid():
        messages.error(request, _("Trecho invalido."))
        return redirect("knowledge:curar", pk=documento.pk)

    try:
        salvar_super_chunk(
            document=documento,
            kind=form.cleaned_data["kind"],
            content=form.cleaned_data["content"],
        )
    except ChunkGrandeDemais as exc:
        # O modelo truncaria o excedente em silencio. Dizer o tamanho e o
        # limite e o que permite a pessoa dividir o trecho com criterio.
        messages.error(request, str(exc))
        return redirect("knowledge:curar", pk=documento.pk)
    except Exception as exc:
        # Indexar depende de carregar o modelo de embedding — 2 GB, baixados na
        # primeira vez. Sem rede, ou com o download bloqueado, isso levanta uma
        # excecao de HTTP no meio da requisicao e a pessoa recebia um 500 sem
        # nenhuma pista de que o problema era o modelo, e nao o trecho.
        logger.exception("Falha ao indexar trecho do documento %s", documento.pk)
        messages.error(
            request,
            _(
                "Nao foi possivel gerar o vetor do trecho: %(erro)s. O modelo de "
                "embedding e baixado na primeira utilizacao (cerca de 2 GB) e "
                "precisa de rede para isso."
            )
            % {"erro": str(exc)[:200]},
        )
        return redirect("knowledge:curar", pk=documento.pk)

    messages.success(request, _("Trecho indexado."))
    return redirect("knowledge:curar", pk=documento.pk)


@login_required
@require_POST
def remover_trecho(request: HttpRequest, pk, chunk_pk) -> HttpResponse:
    documento = get_object_or_404(Document, pk=pk)
    get_object_or_404(SuperChunk, pk=chunk_pk, document=documento).delete()
    messages.success(request, _("Trecho removido do indice."))
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
