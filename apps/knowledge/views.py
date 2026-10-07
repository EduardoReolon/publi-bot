"""Telas do acervo: envio, lista e curadoria."""

from __future__ import annotations

import logging
import re
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.knowledge.blocos import preparar_blocos
from apps.knowledge.forms import CuradoriaDeDocumento, EnvioDeDocumento
from apps.knowledge.models import Document, DocumentCategory
from apps.knowledge.services import (
    blocos_marcados,
    ingerir_documento,
    possiveis_duplicatas,
)
from apps.knowledge.tasks import iniciar_ingestao
from core.arquivos import entregar_arquivo

logger = logging.getLogger("publibot.knowledge")


@login_required
def documentos(request: HttpRequest) -> HttpResponse:
    from apps.knowledge.provisorias import espaco

    """Lista o acervo, filtrando por situacao."""
    situacao = request.GET.get("situacao", "")

    consulta = Document.objects.select_related("category").order_by("-created_at")
    if situacao:
        consulta = consulta.filter(status=situacao)
    if request.GET.get("extracao") == "marcada":
        consulta = consulta.filter(extraction_flagged_at__isnull=False)

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
            "marcados": Document.objects.filter(extraction_flagged_at__isnull=False).count(),
            "filtro_extracao": request.GET.get("extracao", ""),
            "fontes_pendentes": _fontes_pendentes(),
            "espaco": espaco(_meses(request.GET.get("meses"))),
        },
    )


def _meses(valor) -> int | None:
    try:
        meses = int(valor or 0)
    except ValueError:
        return None
    return meses if 0 < meses <= 120 else None


@login_required
@require_POST
def apagar_nao_curados(request: HttpRequest) -> HttpResponse:
    """Apaga os nao curados mais antigos que N meses (nunca os curados)."""
    import datetime

    from apps.knowledge.provisorias import apagaveis

    meses = _meses(request.POST.get("meses"))
    if meses is None or request.POST.get("confirmo") != "1":
        messages.error(request, _("Informe os meses e confirme."))
        return redirect("knowledge:documentos")
    limite = timezone.now() - datetime.timedelta(days=30 * meses)
    apagados, _detalhe = apagaveis(limite).delete()
    messages.success(request, _("Apagados: %(n)s registro(s) do acervo.") % {"n": apagados})
    return redirect("knowledge:documentos")


def _fontes_pendentes() -> int:
    from apps.knowledge.models import CandidatoDeFonte

    return CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.PENDENTE).count()


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
        from apps.knowledge.forms import EnvioPorUrl

        return render(
            request,
            "knowledge/enviar.html",
            {"aba": "documentos", "form": EnvioDeDocumento(), "form_url": EnvioPorUrl()},
        )

    form = EnvioDeDocumento(request.POST, request.FILES)
    if not form.is_valid():
        from apps.knowledge.forms import EnvioPorUrl

        return render(
            request,
            "knowledge/enviar.html",
            {"aba": "documentos", "form": form, "form_url": EnvioPorUrl()},
            status=400,
        )

    resultado = ingerir_documento(
        arquivo=form.cleaned_data["arquivo"],
        category=form.cleaned_data["category"],
        uploaded_by=request.user,
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
        _(
            "Documento recebido. Titulo, autores, ano e DOI serao lidos do "
            "arquivo; confira na curadoria quando a conversao terminar."
        ),
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
        # Enquanto processa, a tela volta com o que foi pedido, e nao com o
        # indice antigo.
        "marcados": set(documento.indexacao_pedida.get("blocos", []))
        if documento.indexacao_pedida
        else blocos_marcados(documento),
        "processando": bool(documento.indexacao_pedida),
        "trechos": documento.chunks.order_by("block_index", "paragraph_index"),
        "duplicatas": possiveis_duplicatas(documento),
        "limite_de_tokens": settings.EMBEDDING_MAX_TOKENS,
        "problemas": Document.ProblemaDeExtracao.choices,
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
    concluir = acao == "concluir"
    if concluir and not marcados:
        messages.error(
            request,
            _("Marque ao menos um bloco: sem trecho no indice o documento nao e citavel."),
        )
        return redirect("knowledge:curar", pk=documento.pk)

    # Vetorizar e demorado num servidor pequeno: vai para a fila, e a tela
    # mostra "processando" ate terminar.
    from apps.knowledge.tasks import pedir_indexacao

    pedir_indexacao(documento, blocos=marcados, concluir=concluir, por=request.user)
    if concluir:
        messages.success(
            request,
            _(
                "Concluindo a curadoria em segundo plano: os trechos estao sendo "
                "vetorizados. O documento aparece como curado quando terminar."
            ),
        )
        return redirect("knowledge:documentos")
    messages.success(request, _("Salvo. Os trechos estao sendo vetorizados em segundo plano."))
    return redirect("knowledge:curar", pk=documento.pk)


@login_required
@require_POST
def recusar_fonte(request: HttpRequest, pk) -> HttpResponse:
    """Nao serve: sai do indice, nao volta a ser sugerida, e as pautas que a
    usariam procuram outra."""
    from apps.knowledge.provisorias import recusar

    documento = get_object_or_404(Document, pk=pk)
    recusar(documento, por=request.user)
    messages.success(
        request, _("Fonte recusada: saiu do indice. As pautas que a usariam procuram outra.")
    )
    return redirect("knowledge:documentos")


@login_required
@require_POST
def vetorizar_no_servidor(request: HttpRequest, pk) -> HttpResponse:
    """O worker da placa esta fora: vetoriza aqui mesmo (mais lento)."""
    from apps.knowledge.tasks import vetorizar_no_servidor as no_servidor

    documento = get_object_or_404(Document, pk=pk)
    if no_servidor(documento):
        messages.success(request, _("Vetorizando no servidor. Pode levar alguns minutos."))
    return redirect("knowledge:curar", pk=documento.pk)


@login_required
def baixar_original(request: HttpRequest, pk) -> HttpResponse:
    """Devolve o arquivo que foi enviado, com o nome original.

    O PDF nao e publico: `MEDIA_ROOT` fica atras de `/protected-media/`, que o
    Nginx serve como `internal`. Esta view e a unica porta, e ela exige sessao
    e resolve o documento DENTRO do schema do tenant — um id de outro cliente
    simplesmente nao existe aqui.
    """
    documento = get_object_or_404(Document, pk=pk)

    if not documento.original_file:
        raise Http404("documento sem arquivo")

    return entregar_arquivo(
        documento.original_file,
        tipo="application/pdf" if documento.nome_do_arquivo.endswith(".pdf") else "text/plain",
        nome_para_baixar=documento.nome_do_arquivo,
    )


@login_required
def baixar_markdown(request: HttpRequest, pk) -> HttpResponse:
    """Devolve o resultado da conversao, montado na hora.

    Nao vai para disco: `markdown_full` ja esta no banco, e gravar um `.md` ao
    lado do PDF criaria um segundo lugar para o mesmo dado — que desatualiza na
    primeira reconversao e nao acompanha o descarte do texto integral.

    Serve para comparar o que o conversor entendeu com o que a pagina mostra.
    Foi assim que se descobriu que, num artigo, o Docling tinha lido a ficha do
    artigo como TABELA e picado o resumo em fragmentos: na tela aquilo era so
    um bloco estranho; no Markdown, a causa estava visivel.
    """
    documento = get_object_or_404(Document, pk=pk)

    if not documento.markdown_full:
        raise Http404(
            "este documento nao tem texto convertido guardado "
            "(a licenca pode ter pedido o descarte do texto integral)"
        )

    resposta = HttpResponse(documento.markdown_full, content_type="text/markdown; charset=utf-8")
    nome = (documento.nome_do_arquivo or "documento").rsplit(".", 1)[0]
    resposta["Content-Disposition"] = f'attachment; filename="{_sanear_nome(nome)}.md"'
    return resposta


def _sanear_nome(nome: str) -> str:
    """O nome vem de um arquivo enviado, entao e entrada externa."""
    limpo = nome.replace('"', "").replace("\\", "")
    limpo = "".join(c for c in limpo if c.isprintable() and c not in "\r\n")
    return limpo.strip() or "documento"


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
@require_POST
def marcar_extracao(request: HttpRequest, pk) -> HttpResponse:
    """Registra que a extracao saiu ruim neste documento.

    Nao dispara nada nem tenta consertar: e triagem. Existe porque a comparacao
    automatica entre o que a extracao propos e o que a curadoria gravou e cega
    para os dois piores casos — bloco dividido no lugar errado e texto
    embaralhado nao mudam campo nenhum de metadado, e passariam por acerto.

    Quem marca e quem esta olhando o documento; quem conserta olha depois, com
    `manage.py exportar_casos`.
    """
    documento = get_object_or_404(Document, pk=pk)

    if request.POST.get("acao") == "desmarcar":
        documento.extraction_flagged_at = None
        documento.extraction_flagged_by = None
        documento.extraction_problem = ""
        documento.extraction_note = ""
        documento.save(
            update_fields=[
                "extraction_flagged_at",
                "extraction_flagged_by",
                "extraction_problem",
                "extraction_note",
            ]
        )
        messages.success(request, _("Marcacao removida."))
        return redirect("knowledge:curar", pk=documento.pk)

    problema = request.POST.get("problema", "")
    if problema not in Document.ProblemaDeExtracao.values:
        problema = Document.ProblemaDeExtracao.OUTRO

    documento.extraction_flagged_at = timezone.now()
    documento.extraction_flagged_by = request.user
    documento.extraction_problem = problema
    documento.extraction_note = (request.POST.get("observacao") or "")[:2000]
    documento.save(
        update_fields=[
            "extraction_flagged_at",
            "extraction_flagged_by",
            "extraction_problem",
            "extraction_note",
        ]
    )

    logger.info("Documento %s marcado como extracao ruim (%s).", documento.pk, problema)
    messages.success(
        request,
        _(
            "Marcado. O documento entra na lista de casos para calibrar; "
            "a curadoria dele continua normal."
        ),
    )
    return redirect("knowledge:curar", pk=documento.pk)


@login_required
def excluir_documento(request: HttpRequest, pk) -> HttpResponse:
    """Tira o documento do acervo, com o arquivo e os trechos indexados.

    Em duas etapas de proposito. O GET mostra o que sai junto — e o que NAO sai:
    artigos ja publicados guardam titulo e URL da fonte copiados no momento da
    citacao, entao o texto publicado nao fica com referencia quebrada.
    """
    documento = get_object_or_404(Document.objects.select_related("category"), pk=pk)

    if request.method != "POST":
        return render(
            request,
            "knowledge/excluir.html",
            {
                "aba": "documentos",
                "documento": documento,
                "trechos": documento.chunks.count(),
                "citacoes": _citacoes_do_documento(documento),
            },
        )

    titulo = documento.title or documento.nome_do_arquivo
    arquivo = documento.original_file

    documento.delete()
    # O registro sai do banco por cascata; o arquivo no disco, nao. Deixa-lo
    # para tras faria a media/ crescer com PDFs que nada mais referencia.
    if arquivo:
        arquivo.delete(save=False)

    logger.info("Documento %s excluido por %s.", pk, request.user.pk)
    messages.success(request, _("Documento %(titulo)s excluido do acervo.") % {"titulo": titulo})
    return redirect("knowledge:documentos")


def _citacoes_do_documento(documento) -> int:
    """Quantas citacoes publicadas apontam para trechos deste documento."""
    from apps.content.models import AnswerCitation, ArticleCitation

    ids = list(documento.chunks.values_list("pk", flat=True))
    if not ids:
        return 0
    return ArticleCitation.objects.filter(super_chunk_id__in=ids).count() + (
        AnswerCitation.objects.filter(super_chunk_id__in=ids).count()
    )


@login_required
def categorias(request: HttpRequest) -> HttpResponse:
    """Categorias do acervo, com o perfil de fonte de cada uma.

    `?editar=<id>` abre a categoria no formulario; sem ele, o formulario cria
    uma nova. Uma tela so, porque sao poucas e o perfil de uma se entende
    melhor ao lado das outras.
    """
    from apps.knowledge.forms import CategoriaDeDocumento

    instancia = None
    if request.GET.get("editar"):
        instancia = DocumentCategory.objects.filter(pk=request.GET["editar"]).first()

    if request.method == "POST":
        if request.POST.get("acao") == "padrao":
            from apps.knowledge.perfis import criar_categorias_padrao

            criadas = criar_categorias_padrao()
            messages.success(
                request, _("%(total)s categoria(s) padrao criada(s).") % {"total": criadas}
            )
            return redirect("knowledge:categorias")

        form = CategoriaDeDocumento(request.POST, instance=instancia)
        if form.is_valid():
            categoria = form.save(commit=False)
            if not categoria.slug:
                from django.utils.text import slugify

                base = slugify(categoria.name)[:70] or "categoria"
                slug, n = base, 2
                while DocumentCategory.objects.filter(slug=slug).exclude(pk=categoria.pk).exists():
                    slug = f"{base}-{n}"
                    n += 1
                categoria.slug = slug
            categoria.save()
            messages.success(
                request,
                _(
                    "Categoria salva. O perfil vale para os trechos indexados daqui "
                    "em diante; os ja indexados mudam ao salvar a curadoria de novo."
                ),
            )
            return redirect("knowledge:categorias")
    else:
        form = CategoriaDeDocumento(instance=instancia)

    return render(
        request,
        "knowledge/categorias.html",
        {
            "aba": "documentos",
            "categorias": DocumentCategory.objects.annotate(total=Count("documents")).order_by(
                "name"
            ),
            "form": form,
            "editando": instancia,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def enviar_url(request: HttpRequest) -> HttpResponse:
    """Busca a pagina e a manda para a conversao, como um arquivo enviado."""
    from apps.knowledge.entradas import ingerir_url
    from apps.knowledge.forms import EnvioPorUrl
    from apps.knowledge.web import PaginaIndisponivel

    form = EnvioPorUrl(request.POST)
    if not form.is_valid():
        messages.error(request, _("Informe um endereco valido e a categoria."))
        return redirect("knowledge:enviar")

    try:
        resultado = ingerir_url(
            form.cleaned_data["url"],
            category=form.cleaned_data["category"],
            uploaded_by=request.user,
        )
    except PaginaIndisponivel as exc:
        messages.error(request, _("Nao foi possivel usar a pagina: %(erro)s") % {"erro": exc})
        return redirect("knowledge:enviar")

    if resultado.ja_existia:
        messages.info(request, _("Esta pagina ja estava no acervo; abrindo o documento."))
        return redirect("knowledge:curar", pk=resultado.document.pk)

    messages.success(request, _("Pagina recebida. Confira na curadoria quando a leitura terminar."))
    return redirect("knowledge:documentos")


@login_required
def nota_do_especialista(request: HttpRequest) -> HttpResponse:
    """Registra o conhecimento de quem escreve como fonte, ja curada."""
    from apps.knowledge.entradas import registrar_nota
    from apps.knowledge.forms import NotaDoEspecialista

    if request.method != "POST":
        return render(
            request,
            "knowledge/nota.html",
            {"aba": "documentos", "form": NotaDoEspecialista(initial={"autor": _nome(request)})},
        )

    form = NotaDoEspecialista(request.POST)
    if not form.is_valid():
        return render(
            request, "knowledge/nota.html", {"aba": "documentos", "form": form}, status=400
        )

    try:
        documento = registrar_nota(
            titulo=form.cleaned_data["titulo"],
            autor=form.cleaned_data["autor"],
            credencial=form.cleaned_data["credencial"],
            texto=form.cleaned_data["texto"],
            escrita_por=request.user,
        )
    except Exception as exc:
        logger.exception("Falha ao registrar nota do especialista")
        messages.error(
            request,
            _("Nao foi possivel indexar a nota: %(erro)s") % {"erro": str(exc)[:200]},
        )
        return render(
            request, "knowledge/nota.html", {"aba": "documentos", "form": form}, status=400
        )

    messages.success(
        request,
        _("Nota registrada e ja disponivel como fonte: %(titulo)s") % {"titulo": documento.title},
    )
    return redirect("knowledge:documentos")


def _nome(request) -> str:
    usuario = request.user
    nome = getattr(usuario, "full_name", "") or ""
    return nome or (usuario.get_full_name() if hasattr(usuario, "get_full_name") else "")


@login_required
def qualidade_da_busca(request: HttpRequest) -> HttpResponse:
    """Como o indice esta respondendo, e o ajuste do limiar deste tenant.

    O teste de consulta usa GET de proposito: o resultado fica linkavel e
    recarregavel, e nenhuma medicao muda o estado do sistema.
    """
    from apps.knowledge.forms import ConfiguracaoDeBusca, TesteDeBusca
    from apps.knowledge.models import RetrievalSettings, RotuloDeCalibracao
    from apps.knowledge.saude import alertas_da_busca, montar_resumo_da_busca

    config = RetrievalSettings.carregar()

    if request.method == "POST":
        return _salvar_configuracao_de_busca(request, config)

    resumo = montar_resumo_da_busca()

    from apps.knowledge import calibracao

    teste = TesteDeBusca(request.GET or None)
    medicao = None
    if teste.is_valid():
        medicao = _medir_consulta(request, teste.cleaned_data["consulta"], config)
    if medicao:
        medicao["texto_para_ia"] = calibracao.pedido(medicao["consulta"], medicao["trechos"])
        medicao["ids"] = ",".join(str(linha["trecho_id"]) for linha in medicao["trechos"])
        ja_rotulados = dict(
            RotuloDeCalibracao.objects.filter(consulta=medicao["consulta"][:500]).values_list(
                "trecho_id", "rotulo"
            )
        )
        for linha in medicao["trechos"]:
            linha["rotulo"] = ja_rotulados.get(linha["trecho_id"], "")

    return render(
        request,
        "knowledge/busca.html",
        {
            "aba": "documentos",
            "resumo": resumo,
            "config": config,
            "form": ConfiguracaoDeBusca(instance=config),
            "teste": teste,
            "medicao": medicao,
            "alertas": alertas_da_busca(resumo),
            "consultas_recentes": _consultas_recentes(),
            "sugestao": calibracao.sugerir_corte(),
            "lixo_marcado": calibracao.lixo_marcado().count(),
        },
    )


def _salvar_configuracao_de_busca(request: HttpRequest, config) -> HttpResponse:
    from apps.knowledge.forms import ConfiguracaoDeBusca

    form = ConfiguracaoDeBusca(request.POST, instance=config)
    if not form.is_valid():
        messages.error(request, _("Valor invalido: %(erro)s") % {"erro": form.errors.as_text()})
        return redirect("knowledge:busca")

    config = form.save(commit=False)
    # Guardar QUEM, QUANDO e COM QUE MODELO nao e auditoria por formalidade: e
    # o unico jeito de detectar depois que o limiar sobreviveu a uma troca de
    # modelo e virou um numero sem significado.
    config.calibrated_at = timezone.now()
    config.calibrated_by = request.user
    config.calibrated_model = settings.EMBEDDING_MODEL
    config.calibration_query = (request.POST.get("consulta") or "")[:2000]
    config.save()

    messages.success(
        request,
        _("Limiar salvo em %(valor).4f. Vale para as proximas geracoes deste ambiente.")
        % {"valor": config.max_cosine_distance},
    )
    return redirect("knowledge:busca")


def _medir_consulta(request: HttpRequest, consulta: str, config):
    """As distancias reais entre uma consulta e o corpus, sem registrar nada.

    Nao passa por `recuperar()` porque `recuperar()` grava `RetrievalQuery` — e
    uma medicao de calibracao poluiria exatamente as metricas que esta tela
    existe para mostrar.
    """
    from pgvector.django import CosineDistance

    from apps.knowledge.embeddings import get_embedding_client
    from apps.knowledge.models import SuperChunk

    try:
        cliente = get_embedding_client()
        vetor = cliente.embed_query(consulta)
    except Exception as exc:
        logger.exception("Falha ao vetorizar consulta de calibracao")
        messages.error(
            request,
            _(
                "Nao foi possivel vetorizar a consulta: %(erro)s. O modelo de "
                "embedding e baixado na primeira utilizacao (cerca de 2 GB)."
            )
            % {"erro": str(exc)[:200]},
        )
        return None

    trechos = list(
        SuperChunk.objects.filter(is_active=True, embedding__isnull=False)
        .annotate(distancia=CosineDistance("embedding", vetor))
        .order_by("distancia")[:40]
    )

    if not trechos:
        messages.info(request, _("Nenhum trecho vetorizado no acervo ainda."))
        return None

    limiar = config.max_cosine_distance
    return {
        "consulta": consulta,
        "modelo": cliente.model_name,
        "trechos": [
            {
                "distancia": float(t.distancia),
                "corte": f"{float(t.distancia) + 0.0005:.4f}",
                "aceito": float(t.distancia) <= limiar,
                "titulo": t.source_title or str(t.document_id),
                "heading": t.heading,
                "conteudo": t.content,
                "documento_id": t.document_id,
                "trecho_id": t.pk,
            }
            for t in trechos
        ],
        "aceitos": sum(1 for t in trechos if float(t.distancia) <= limiar),
        "exibidos": len(trechos),
        "limiar": limiar,
    }


@login_required
@require_POST
def rotular_busca(request: HttpRequest) -> HttpResponse:
    """A resposta da outra IA para o teste de consulta: grava os rotulos."""
    from pgvector.django import CosineDistance

    from apps.knowledge import calibracao
    from apps.knowledge.embeddings import get_embedding_client
    from apps.knowledge.models import SuperChunk

    consulta = (request.POST.get("consulta") or "").strip()
    ids = [i for i in (request.POST.get("ids") or "").split(",") if i]
    voltar = reverse("knowledge:busca") + "?" + urlencode({"consulta": consulta}) + "#testar"
    rotulos = calibracao.ler(request.POST.get("resposta", ""), len(ids))
    if not consulta or not rotulos:
        messages.error(request, _("Nao achei rotulos (T1: R, T2: L...) na resposta colada."))
        return redirect(voltar)
    # A distancia e medida de novo aqui, e nao lida do formulario.
    vetor = get_embedding_client().embed_query(consulta)
    por_id = {
        str(t.pk): t
        for t in SuperChunk.objects.filter(pk__in=ids).annotate(
            distancia=CosineDistance("embedding", vetor)
        )
    }
    gravados = calibracao.gravar(consulta, [por_id.get(i) for i in ids], rotulos)
    messages.success(
        request,
        _("%(n)s rotulo(s) gravado(s) de %(total)s trechos.") % {"n": gravados, "total": len(ids)},
    )
    return redirect(voltar)


@login_required
@require_POST
def tirar_lixo_da_busca(request: HttpRequest) -> HttpResponse:
    """Os trechos rotulados como lixo saem da busca (nao sao apagados)."""
    from apps.knowledge import calibracao

    tirados = calibracao.lixo_marcado().update(is_active=False)
    messages.success(request, _("%(n)s trecho(s) de lixo fora da busca.") % {"n": tirados})
    return redirect(reverse("knowledge:busca") + "#rotulos")


def _consultas_recentes(limite: int = 15):
    """As ultimas buscas de verdade, com quantas fontes cada uma achou."""
    from apps.knowledge.models import RetrievalQuery

    return RetrievalQuery.objects.annotate(encontradas=Count("hits")).order_by("-created_at")[
        :limite
    ]


# ---------------------------------------------------------------------------
# Fontes sugeridas pela web, e caminhos confiaveis
# ---------------------------------------------------------------------------
AVISO_DE_CONFIANCA = gettext_lazy(
    "Considere muito bem: e muito comum sites terem areas livres para usuarios "
    "externos (comentarios, forum, perfis, posts de convidados). So marque se "
    "voce tem certeza de que todo o conteudo deste caminho e 100% criado pelo "
    "autor do site."
)


@login_required
def fontes_sugeridas(request: HttpRequest) -> HttpResponse:
    """Candidatos a fonte achados na web, esperando curadoria."""
    from apps.content.models import Topic
    from apps.knowledge.fontes_web import caminho_de, natureza_sugerida
    from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte

    # Vindo do painel de referencias de uma pauta: so as dela, e sem os artigos
    # da pesquisa do B (esses tem a pagina propria, `content:pdfs_da_pesquisa`).
    pauta = None
    try:
        pauta = Topic.objects.filter(pk=uuid.UUID(request.GET.get("pauta", ""))).first()
    except ValueError:
        pass

    # Da caixa de ideias / modo investigativo: so as fontes de uma frente da pauta.
    frente = request.GET.get("frente", "").strip()[:80] if pauta is not None else ""

    def da_pauta(qs):
        if pauta is None:
            return qs
        da_pesquisa = ((pauta.busca_de_fontes or {}).get("pesquisa") or {}).get("candidatos") or []
        qs = qs.filter(pauta=pauta).exclude(pk__in=da_pesquisa)
        return qs.filter(metricas__frente=frente) if frente else qs

    consulta = da_pauta(
        CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.PENDENTE)
    )
    pendentes = list(consulta.select_related("pauta")[:100])
    for candidato in pendentes:
        candidato.natureza_padrao = natureza_sugerida(candidato)
    # Decididas, a mais recente primeiro: e ali que se desfaz um engano.
    so_recusados = request.GET.get("recusados") == "1"
    decididas = CandidatoDeFonte.objects.exclude(
        situacao__in=[
            CandidatoDeFonte.Situacao.PENDENTE,
            CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO,
            CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
        ]
    )
    if so_recusados:
        decididas = decididas.filter(situacao=CandidatoDeFonte.Situacao.RECUSADO)
    decididas = da_pauta(decididas)
    recentes = list(
        decididas.select_related("documento").order_by(
            F("decidido_em").desc(nulls_last=True), "-encontrado_em"
        )[: 200 if so_recusados else 20]
    )
    for candidato in recentes:
        if candidato.situacao == CandidatoDeFonte.Situacao.RECUSADO:
            caminho = caminho_de(candidato.url)
            if caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.BLOQUEAR:
                candidato.bloqueio = caminho
    return render(
        request,
        "knowledge/fontes_sugeridas.html",
        {
            "aba": "documentos",
            "pendentes": pendentes,
            "aguardando_audio": da_pauta(
                CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO)
            ),
            "aguardando_pdf": da_pauta(
                CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.AGUARDANDO_PDF)
            ),
            "pauta": pauta,
            "recentes": recentes,
            "so_recusados": so_recusados,
            "frente": frente,
            **contexto_da_curadoria(),
        },
    )


def contexto_da_curadoria() -> dict:
    """O que o cartao de fonte (`knowledge/_candidato.html`) precisa, onde quer
    que ele apareca (Fontes sugeridas, frentes da pauta)."""
    from apps.knowledge.models import CaminhoConfiavel

    return {
        "categorias": DocumentCategory.objects.order_by("name"),
        "niveis": CaminhoConfiavel.Nivel.choices,
        "aviso": AVISO_DE_CONFIANCA,
    }


def _voltar_as_fontes(request: HttpRequest, ancora: str = "") -> HttpResponse:
    """Volta para onde a pessoa estava (Fontes sugeridas filtrada pela pauta, a
    pagina da pesquisa...), ou para Fontes sugeridas."""
    voltar = request.POST.get("voltar", "")
    if not (voltar.startswith("/") and not voltar.startswith("//")):
        voltar = reverse("knowledge:fontes_sugeridas")
    # O cartao numa frente da pauta volta para a frente (ela reabre sozinha).
    pedida = request.POST.get("ancora", "")
    if not ancora and re.fullmatch(r"#[\w-]{1,80}", pedida):
        ancora = pedida
    return redirect(voltar.split("#")[0] + ancora)


@login_required
@require_POST
def decidir_candidato(request: HttpRequest, pk) -> HttpResponse:
    from apps.knowledge.fontes_web import aprovar, recusar
    from apps.knowledge.models import CandidatoDeFonte

    candidato = get_object_or_404(
        CandidatoDeFonte, pk=pk, situacao=CandidatoDeFonte.Situacao.PENDENTE
    )
    if request.POST.get("decisao") in {"recusar", "bloquear_site", "bloquear_caminho"}:
        bloquear = {"bloquear_site": "site", "bloquear_caminho": "caminho"}.get(
            request.POST["decisao"], ""
        )
        caminho = recusar(
            candidato,
            por=request.user,
            motivo=request.POST.get("motivo", ""),
            bloquear=bloquear,
        )
        if caminho is not None:
            messages.success(
                request,
                _("Recusado, e nada de %(p)s sera sugerido de novo.") % {"p": caminho.prefixo},
            )
        else:
            messages.success(request, _("Recusado. Esta pagina nao sera sugerida de novo."))
        return _voltar_as_fontes(request)

    # O papel: "discurso" (o que se diz, nunca prova) ou "evidencia".
    if request.POST.get("decisao") == "discurso":
        from apps.knowledge.fontes_web import aprovar_como_discurso

        aprovar_como_discurso(candidato, por=request.user)
        messages.success(
            request,
            _(
                "Guardado como DISCURSO: o artigo da pauta cita como 'o que se diz', e ele "
                "nao entra no acervo nem serve de prova. Links de fonte primaria que a "
                "pagina cita viraram sugestao de evidencia."
            ),
        )
        return _voltar_as_fontes(request)
    if request.POST.get("decisao") == "evidencia":
        candidato.papel = CandidatoDeFonte.Papel.EVIDENCIA
        candidato.save(update_fields=["papel"])
        messages.info(request, _("Agora e sugestao de evidencia: aprove com a categoria."))
        return _voltar_as_fontes(request)

    categoria = DocumentCategory.objects.filter(pk=request.POST.get("categoria")).first()
    if categoria is None:
        messages.error(request, _("Escolha a categoria da fonte."))
        return _voltar_as_fontes(request)

    arquivo = request.FILES.get("pdf")
    if arquivo is not None and candidato.tipo == CandidatoDeFonte.Tipo.ARTIGO:
        if not (arquivo.name or "").lower().endswith(".pdf"):
            messages.error(request, _("Envie o arquivo PDF do artigo."))
            return _voltar_as_fontes(request)
        from apps.knowledge.academicos import receber_pdf

        receber_pdf(candidato, arquivo, categoria=categoria, por=request.user)
        messages.success(
            request, _("PDF recebido. Confira na curadoria quando a leitura terminar.")
        )
        return _voltar_as_fontes(request)

    candidato = aprovar(candidato, categoria=categoria, por=request.user)
    if candidato.situacao == CandidatoDeFonte.Situacao.FALHOU:
        messages.error(
            request, _("Nao foi possivel buscar a pagina: %(m)s") % {"m": candidato.motivo}
        )
    elif candidato.situacao == CandidatoDeFonte.Situacao.AGUARDANDO_PDF:
        # Aprovado, mas ainda NAO e documento: sem o PDF nao ha o que ler.
        messages.warning(
            request,
            _(
                "Artigo aprovado, mas sem PDF para baixar: ele ainda nao foi para o "
                "acervo. Esta em 'Artigos aguardando o PDF', no topo desta pagina: "
                "baixe o PDF pelo link e envie ali."
            ),
        )
        return _voltar_as_fontes(request, "#aguardando-pdf")
    elif candidato.situacao == CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO:
        messages.warning(
            request,
            _(
                "Video aprovado, mas sem legenda para ler: ele esta em 'Videos "
                "aguardando o audio', no topo desta pagina."
            ),
        )
        return _voltar_as_fontes(request, "#aguardando-audio")
    else:
        messages.success(
            request,
            _("Enviado para o acervo. Confira na curadoria quando a leitura terminar."),
        )
    return _voltar_as_fontes(request)


@login_required
@require_POST
def capturar_texto_do_candidato(request: HttpRequest, pk) -> HttpResponse:
    """Baixa a pagina e guarda o texto que a extracao tira, so para conferir."""
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.web import PaginaIndisponivel, texto_da_pagina

    candidato = get_object_or_404(
        CandidatoDeFonte,
        pk=pk,
        situacao=CandidatoDeFonte.Situacao.PENDENTE,
        tipo=CandidatoDeFonte.Tipo.PAGINA,
    )
    try:
        candidato.texto_extraido = texto_da_pagina(candidato.url)
    except PaginaIndisponivel as exc:
        messages.error(request, _("Nao foi possivel ler a pagina: %(m)s") % {"m": exc})
    else:
        candidato.save(update_fields=["texto_extraido"])
    return _voltar_as_fontes(request, f"#candidato-{candidato.pk}")


@login_required
def caminhos_confiaveis(request: HttpRequest) -> HttpResponse:
    """Lista e cadastro dos caminhos em que a pessoa confia."""
    from apps.knowledge.fontes_web import CaminhoRecusado, conferir_caminho
    from apps.knowledge.models import CaminhoConfiavel

    if request.method == "POST":
        if request.POST.get("remover"):
            CaminhoConfiavel.objects.filter(pk=request.POST["remover"]).delete()
            messages.success(request, _("Caminho removido."))
            return redirect(request.POST.get("voltar") or "knowledge:caminhos")

        nivel = request.POST.get("nivel", "")
        categoria = DocumentCategory.objects.filter(pk=request.POST.get("categoria")).first()
        bloquear = nivel == CaminhoConfiavel.Nivel.BLOQUEAR
        if nivel not in CaminhoConfiavel.Nivel.values or (categoria is None and not bloquear):
            messages.error(request, _("Escolha o nivel e a categoria."))
            return redirect(request.POST.get("voltar") or "knowledge:caminhos")
        if nivel == CaminhoConfiavel.Nivel.APROVAR and not request.POST.get("confirmo"):
            messages.error(
                request,
                _("Para aprovar automaticamente, confirme que leu o aviso em vermelho."),
            )
            return redirect(request.POST.get("voltar") or "knowledge:caminhos")
        try:
            prefixo = conferir_caminho(request.POST.get("prefixo", ""), nivel)
        except CaminhoRecusado as exc:
            messages.error(request, str(exc))
            return redirect(request.POST.get("voltar") or "knowledge:caminhos")

        CaminhoConfiavel.objects.update_or_create(
            prefixo=prefixo,
            defaults={
                "nivel": nivel,
                "categoria": None if bloquear else categoria,
                "observacao": request.POST.get("observacao", "")[:300],
                "criado_por": request.user,
            },
        )
        messages.success(request, _("Caminho salvo: %(p)s") % {"p": prefixo})
        return redirect(request.POST.get("voltar") or "knowledge:caminhos")

    return render(
        request,
        "knowledge/caminhos.html",
        {
            "aba": "documentos",
            "caminhos": CaminhoConfiavel.objects.select_related("categoria"),
            "categorias": DocumentCategory.objects.order_by("name"),
            "niveis": CaminhoConfiavel.Nivel.choices,
            "aviso": AVISO_DE_CONFIANCA,
        },
    )


@login_required
@require_POST
def enviar_audio(request: HttpRequest, pk) -> HttpResponse:
    """O audio de um video cuja legenda nao veio. Vai para a transcricao."""
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.videos import EXTENSOES_DE_AUDIO, receber_audio

    candidato = get_object_or_404(
        CandidatoDeFonte, pk=pk, situacao=CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO
    )
    arquivo = request.FILES.get("audio")
    if arquivo is None or not (arquivo.name or "").lower().endswith(EXTENSOES_DE_AUDIO):
        messages.error(
            request,
            _("Envie um arquivo de audio (%(lista)s).") % {"lista": ", ".join(EXTENSOES_DE_AUDIO)},
        )
        return _voltar_as_fontes(request)

    receber_audio(candidato, arquivo, por=request.user)
    messages.success(
        request,
        _("Audio recebido. A transcricao roda no worker quando a placa estiver livre."),
    )
    return _voltar_as_fontes(request)


@login_required
@require_POST
def voltar_a_sugestao(request: HttpRequest, pk) -> HttpResponse:
    """Volta para as sugestoes: o recusado, ou o aprovado que espera PDF ou
    audio. Com `recusar`, o que esperava arquivo ja sai recusado."""
    from apps.knowledge.fontes_web import recusar
    from apps.knowledge.fontes_web import voltar_a_sugestao as voltar
    from apps.knowledge.models import CandidatoDeFonte

    candidato = get_object_or_404(
        CandidatoDeFonte,
        pk=pk,
        situacao__in=[
            CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
            CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO,
            CandidatoDeFonte.Situacao.RECUSADO,
            CandidatoDeFonte.Situacao.FALHOU,
        ],
    )
    esperava_arquivo = candidato.situacao in (
        CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
        CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO,
    )
    voltar(candidato)
    if esperava_arquivo and request.POST.get("recusar") == "1":
        recusar(candidato, por=request.user, motivo=_("Sem arquivo para ler."))
        messages.success(request, _("Recusado. Nao sera sugerido de novo."))
        return _voltar_as_fontes(request)
    messages.success(request, _("Voltou para as sugestoes, com todas as opcoes."))
    return _voltar_as_fontes(request, f"#candidato-{candidato.pk}")


@login_required
@require_POST
def seguir_com_o_resumo(request: HttpRequest, pk) -> HttpResponse:
    """Nao achei o PDF: o artigo da pesquisa segue com o resumo."""
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.pesquisa import seguir_com_o_resumo as seguir

    candidato = get_object_or_404(CandidatoDeFonte, pk=pk)
    seguir(candidato)
    messages.success(request, _("Ok: o artigo segue com o resumo, sem o texto completo."))
    return _voltar_as_fontes(request)


@login_required
@require_POST
def enviar_pdf(request: HttpRequest, pk) -> HttpResponse:
    """O PDF de um artigo cientifico que nao tinha acesso aberto."""
    from apps.knowledge.academicos import receber_pdf
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.perfis import categoria_da_natureza

    candidato = get_object_or_404(
        CandidatoDeFonte, pk=pk, situacao=CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    )
    arquivo = request.FILES.get("pdf")
    if arquivo is None or not (arquivo.name or "").lower().endswith(".pdf"):
        messages.error(request, _("Envie o arquivo PDF do artigo."))
        return _voltar_as_fontes(request)
    categoria = DocumentCategory.objects.filter(
        pk=request.POST.get("categoria")
    ).first() or categoria_da_natureza("cientifico")
    receber_pdf(candidato, arquivo, categoria=categoria, por=request.user)
    messages.success(request, _("PDF recebido. Confira na curadoria quando a leitura terminar."))
    return _voltar_as_fontes(request)
