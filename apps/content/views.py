"""Telas de pauta, artigo, revisao e resposta."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
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
from apps.content.models import Answer, Article, Author, Question, Topic
from apps.content.services import (
    RevisaoInsuficiente,
    aplicar_edicao_humana,
    aprovar_e_agendar,
    aprovar_resposta_e_agendar,
)
from apps.content.tasks import gerar_artigo, responder_pergunta
from apps.ops.models import GenerationJob

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

    # O artigo so passa a existir quando a geracao termina, entao a guarda
    # acima nao cobre o segundo clique: ate la, `articles` esta vazio. Dois
    # cliques criavam dois trabalhos para a mesma pauta — que disputam a mesma
    # placa e, se ambos chegassem ao fim, produziriam dois artigos.
    em_andamento = GenerationJob.objects.filter(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=str(pauta.pk),
        status__in=[
            GenerationJob.Status.PENDING,
            GenerationJob.Status.RUNNING,
            GenerationJob.Status.WAITING_CAPACITY,
        ],
    ).first()

    if em_andamento is not None:
        messages.info(
            request,
            _("Esta pauta ja esta sendo gerada (trabalho %(id)s). Acompanhe em Operacao.")
            % {"id": str(em_andamento.pk)[:8]},
        )
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
                "author": artigo.author_id,
            }
        ),
        "agendamento": agendamento or AgendamentoForm(),
        "citacoes": artigo.citations.select_related("super_chunk").order_by("rank"),
        "revisoes": artigo.revisions.order_by("-version")[:10],
        "secoes": artigo.sections.all(),
        "titulos_sugeridos": (artigo.thesis_json or {}).get("titulos_sugeridos") or [],
        "moldura": (artigo.thesis_json or {}).get("moldura") or {},
        "refazendo": _trabalho_em_curso(artigo),
        "site": site,
        "tem_autores": Author.objects.filter(is_active=True).exists(),
        "posicoes_de_link": Article.LinkPlacement.choices,
        "lotes_de_capa": _lotes_de_capa(artigo),
        "motivo_sem_capa": _motivo_sem_capa(artigo),
        "capas_em_curso": _capas_em_curso(artigo),
        "capa_escolhida": artigo.images.filter(is_chosen=True).first(),
        "proximo_horario": _proximo_horario(),
    }


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
    """Por que a geracao automatica de capa nao produziu nada.

    O passo de capa nao derruba o trabalho: ele grava o motivo e segue, porque
    a essa altura o artigo ja esta pronto (apps/content/flows.py). O preco
    disso e que a falha fica escondida num payload — a tela mostraria apenas
    "nenhuma opcao gerada ainda", que e o mesmo texto de quem nunca pediu.

    Trazer o motivo para ca e o que separa "ainda nao pedi" de "falta cadastrar
    a conexao de imagem".
    """
    from apps.ops.models import GenerationJob

    if artigo.images.exists():
        return ""

    # Os dois caminhos que geram capa: o passo do fluxo do artigo (que aponta
    # para a PAUTA) e o botao da tela (que aponta para o ARTIGO). O mais
    # recente dos dois e o que explica a ausencia — olhar so o primeiro deixava
    # a falha do botao invisivel, agora que ele tambem e trabalho de fila.
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
    from apps.ops.models import GenerationJob

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
    from apps.ops.models import GenerationJob

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

    return redirect("content:revisar", pk=artigo.pk)


@login_required
@require_POST
def refazer_secoes(request: HttpRequest, pk) -> HttpResponse:
    """Reescreve APENAS as secoes marcadas. Uma chamada por secao."""
    from apps.content.services import marcar_secoes_para_refazer
    from apps.ops.models import GenerationJob
    from apps.ops.orchestrator import criar_job
    from apps.ops.tasks import advance_generation_job

    artigo = get_object_or_404(Article, pk=pk)

    if _trabalho_em_curso(artigo):
        messages.error(request, _("Ja ha um trabalho refazendo este artigo. Aguarde."))
        return redirect("content:revisar", pk=artigo.pk)

    # Os parametros sao guardados ANTES da conferencia das secoes. Quem ajusta
    # a palavra-chave e esquece de marcar uma secao nao pode perder o ajuste
    # junto com o clique.
    _guardar_parametros(request, artigo)

    ordens = {int(v) for v in request.POST.getlist("refazer") if v.isdigit()}
    if not ordens:
        messages.error(
            request, _("Parametros salvos. Marque ao menos uma secao para refazer o texto.")
        )
        return redirect("content:revisar", pk=artigo.pk)

    total = marcar_secoes_para_refazer(artigo, ordens)

    job = criar_job(kind=GenerationJob.Kind.ARTICLE_REDRAFT, target_object_id=str(artigo.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))

    messages.success(
        request,
        _("Refazendo %(total)s secao(oes). O resto do artigo fica como esta.") % {"total": total},
    )
    return redirect("content:revisar", pk=artigo.pk)


@login_required
def replanejar(request: HttpRequest, pk) -> HttpResponse:
    """Descarta o esqueleto e recomeca do plano.

    Em duas etapas, como a exclusao de documento: o GET diz o que se perde. E a
    acao mais cara da tela e a mais facil de acionar por engano — alguem que
    queria consertar uma secao pode perder cinco boas.
    """
    from apps.content.services import limpar_plano
    from apps.ops.models import GenerationJob
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
    from apps.ops.models import GenerationJob
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
        return redirect("content:revisar", pk=artigo.pk)

    if _capas_em_curso(artigo):
        messages.error(request, _("Ja ha um lote de capas sendo gerado. Aguarde."))
        return redirect("content:revisar", pk=artigo.pk)

    # O teto e conferido aqui e tambem dentro de `gerar_opcoes`. Aqui para a
    # pessoa saber na hora do clique; la porque o fluxo do artigo tambem chama.
    ultimo_lote = artigo.images.order_by("-batch").values_list("batch", flat=True).first() or 0
    if ultimo_lote >= MAXIMO_DE_LOTES:
        messages.error(
            request,
            _("Este artigo ja tem %(total)s lotes de imagem. Escolha uma das opcoes existentes.")
            % {"total": MAXIMO_DE_LOTES},
        )
        return redirect("content:revisar", pk=artigo.pk)

    job = criar_job(kind=GenerationJob.Kind.ARTICLE_COVER, target_object_id=str(artigo.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))

    messages.success(
        request,
        _(
            "Gerando tres opcoes de capa. Pode levar alguns minutos — a placa e "
            "dividida com a geracao de texto. Atualize a pagina para ver."
        ),
    )
    return redirect("content:revisar", pk=artigo.pk)


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
    return redirect("content:revisar", pk=artigo.pk)


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
            "citacoes": resposta.citations.select_related("super_chunk").order_by("rank"),
            "proximo_horario": _proximo_horario(),
            "tem_autores": Author.objects.filter(is_active=True).exists(),
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
