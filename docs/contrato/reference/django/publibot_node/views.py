"""Rotas do contrato /api/v1."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from publibot_node import RECURSOS, VERSAO, VERSOES_DO_CONTRATO
from publibot_node.auth import conferir_assinatura
from publibot_node.models import AuthorPhoto, ReceivedPublication, VisitorQuestion
from publibot_node.sanitize import ConteudoRecusado, sanitizar, sanitizar_texto
from publibot_node.throttle import (
    LIMITE_DE_PUBLICACAO_POR_MINUTO,
    LIMITE_POR_IP_POR_MINUTO,
    esta_bloqueado,
    excedeu,
    registrar_negacao,
    resposta_de_limite,
)

TAMANHO_MAXIMO_DO_CORPO = 12 * 1024 * 1024


def _ip(request) -> str:
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _erro(code: str, mensagem: str, status: int, **detalhes) -> JsonResponse:
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": mensagem,
                "details": detalhes,
                "request_id": str(uuid.uuid4()),
            }
        },
        status=status,
    )


def _proteger(request, *, limite: int = LIMITE_POR_IP_POR_MINUTO):
    """Aplica bloqueio, limite e assinatura. Devolve None quando esta tudo bem."""
    endereco = _ip(request)

    if esta_bloqueado(endereco):
        return resposta_de_limite(900)

    if excedeu(f"publibot:req:{endereco}", limite):
        return resposta_de_limite()

    # HTTPS obrigatorio. Sem TLS a chave trafega em texto claro a cada
    # requisicao, junto com todo o conteudo.
    if not request.is_secure() and not getattr(request, "_publibot_permitir_http", False):
        from django.conf import settings

        if not settings.DEBUG:
            return _erro("forbidden", "Este endpoint exige HTTPS.", 403)

    falha = conferir_assinatura(request)
    if falha is not None:
        registrar_negacao(endereco)
        return falha

    return None


@require_GET
def health(request):
    """Versao do contrato e recursos suportados.

    O PubliBot consulta no cadastro e degrada com elegancia. Sem este aperto de
    mao, adicionar um campo obrigatorio quebraria todos os sites instalados.
    """
    bloqueio = _proteger(request)
    if bloqueio is not None:
        return bloqueio

    return JsonResponse(
        {
            "contract_versions": VERSOES_DO_CONTRATO,
            "implementation": f"publibot-django {VERSAO}",
            "capabilities": RECURSOS,
            "server_time": timezone.now().isoformat(),
        }
    )


@require_GET
def seo_context(request):
    """Publicacoes existentes, paginadas."""
    from django.conf import settings

    bloqueio = _proteger(request)
    if bloqueio is not None:
        return bloqueio

    limite = min(int(request.GET.get("limit", 100)), 500)
    cursor = request.GET.get("cursor", "")
    publicados_apos = request.GET.get("published_after", "")

    consulta = ReceivedPublication.objects.filter(kind=ReceivedPublication.Kind.ARTICLE).order_by(
        "created_at", "id"
    )

    if publicados_apos:
        consulta = consulta.filter(published_at__gte=publicados_apos)
    if cursor:
        consulta = consulta.filter(id__gt=cursor)

    itens = list(consulta[: limite + 1])
    tem_mais = len(itens) > limite
    itens = itens[:limite]

    return JsonResponse(
        {
            "site_title": getattr(settings, "PUBLIBOT_NODE_SITE_TITLE", ""),
            # Truncado: um texto de home longo iria inteiro para dentro do
            # prompt, consumindo contexto sem acrescentar informacao.
            "home_content_text": getattr(settings, "PUBLIBOT_NODE_HOME_TEXT", "")[:8000],
            "published_posts": [
                {
                    "remote_id": str(p.id),
                    "title": p.title,
                    "url": p.url,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "primary_keyword": p.focus_keyword,
                    "word_count": len(p.html_content.split()),
                }
                for p in itens
            ],
            "next_cursor": str(itens[-1].id) if tem_mais and itens else None,
        }
    )


@csrf_exempt
@require_POST
def publish(request):
    """Recebe conteudo. Idempotente por `Idempotency-Key`."""
    bloqueio = _proteger(request, limite=LIMITE_DE_PUBLICACAO_POR_MINUTO)
    if bloqueio is not None:
        return bloqueio

    if len(request.body) > TAMANHO_MAXIMO_DO_CORPO:
        return _erro("payload_too_large", "Corpo excede o limite.", 413)

    chave = request.headers.get("Idempotency-Key", "")
    if not chave:
        return _erro("invalid_payload", "Cabecalho Idempotency-Key obrigatorio.", 400)

    try:
        dados = json.loads(request.body)
    except json.JSONDecodeError:
        return _erro("invalid_payload", "Corpo nao e JSON valido.", 400)

    # Chave ja processada: devolve o que existe, sem criar outro registro. E
    # isto que impede publicacao duplicada quando a resposta anterior se perdeu.
    autor = dados.get("author") or {}
    quer_foto = _precisa_da_foto(autor)

    existente = ReceivedPublication.objects.filter(idempotency_key=chave).first()
    if existente is not None:
        return JsonResponse(
            _resposta(existente, status="already_exists", quer_foto=quer_foto), status=200
        )

    try:
        html = sanitizar(dados.get("html_content", ""))
    except ConteudoRecusado as exc:
        return _erro("content_rejected", str(exc), 422)

    tipo = dados.get("type", "article")
    if tipo not in {"article", "qa"}:
        return _erro("invalid_payload", f"type invalido: {tipo!r}", 400)

    try:
        with transaction.atomic():
            publicacao = ReceivedPublication.objects.create(
                idempotency_key=chave,
                kind=tipo,
                title=sanitizar_texto(dados.get("title", "")),
                slug=dados.get("slug", "")[:300],
                html_content=html,
                excerpt=sanitizar_texto(dados.get("excerpt", ""), limite=1000),
                meta_description=sanitizar_texto(dados.get("meta_description", ""), limite=160),
                focus_keyword=sanitizar_texto(dados.get("focus_keyword", ""), limite=120),
                language=dados.get("language", "pt-br")[:10],
                author_name=sanitizar_texto(autor.get("name", ""), limite=150),
                author_credentials=sanitizar_texto(autor.get("credentials", ""), limite=200),
                author_reference=_uuid_ou_nada(autor.get("reference")),
                reviewed_by=sanitizar_texto(dados.get("reviewed_by", ""), limite=150),
                reviewed_at=dados.get("reviewed_at") or None,
                content_disclosure=sanitizar_texto(dados.get("content_disclosure", ""), limite=500),
                canonical_source=dados.get("canonical_source", "")[:500],
                cover_image_url=(dados.get("cover_image") or {}).get("url", "")[:500],
                cover_image_alt=sanitizar_texto(
                    (dados.get("cover_image") or {}).get("alt_text", "")
                ),
                question_id=str(dados.get("question_id", ""))[:120],
                post_status=dados.get("status", "published")[:20],
                publish_at=dados.get("publish_at") or None,
                published_at=timezone.now() if dados.get("status") == "published" else None,
            )
    except IntegrityError:
        # Duas requisicoes simultaneas com a mesma chave: a restricao UNICA do
        # banco decide, e a perdedora devolve o registro vencedor. Uma checagem
        # feita antes do INSERT nao cobriria este caso.
        existente = ReceivedPublication.objects.get(idempotency_key=chave)
        return JsonResponse(
            _resposta(existente, status="already_exists", quer_foto=quer_foto), status=200
        )

    if publicacao.kind == ReceivedPublication.Kind.QA and publicacao.question_id:
        VisitorQuestion.objects.filter(id=publicacao.question_id).update(answered_at=timezone.now())

    return JsonResponse(_resposta(publicacao, quer_foto=quer_foto), status=201)


def _uuid_ou_nada(valor):
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError):
        return None


def _precisa_da_foto(autor: dict) -> bool:
    """Decide se vale pedir a foto de perfil deste autor.

    Pede apenas quando o PubliBot diz ter uma foto E este site ainda nao a
    tem. Pedir sempre faria o mesmo arquivo ser enviado a cada publicacao;
    nunca pedir deixaria a caixa de autor sem foto para sempre.
    """
    if not autor.get("has_photo"):
        return False

    referencia = _uuid_ou_nada(autor.get("reference"))
    if referencia is None:
        return False

    return not AuthorPhoto.objects.filter(author_reference=referencia).exists()


def _resposta(
    publicacao: ReceivedPublication, *, status: str = "success", quer_foto: bool = False
) -> dict:
    return {
        "status": status,
        "remote_id": str(publicacao.id),
        "idempotency_key": str(publicacao.idempotency_key),
        "url": publicacao.url,
        "slug": publicacao.slug,
        "post_status": publicacao.post_status,
        "published_at": publicacao.published_at.isoformat() if publicacao.published_at else None,
        "author_photo_required": quer_foto,
    }


# A foto e o unico binario do contrato. Uma WebP de 1600 px fica bem abaixo
# disto; o limite existe para recusar cedo o que nao e foto de perfil.
TAMANHO_MAXIMO_DA_FOTO = 5 * 1024 * 1024


@csrf_exempt
@require_POST
def author_photos(request):
    """Recebe a foto de perfil de um autor. Recurso `author_photo`.

    Chamada apenas quando esta implementacao respondeu
    `author_photo_required: true` em `/publish/`.

    **multipart/form-data.** A assinatura cobre o corpo bruto, igual as demais
    rotas — `conferir_assinatura` le `request.body` antes de o Django
    interpretar o multipart, e por isso funciona sem tratamento especial.

    Atencao ao `DATA_UPLOAD_MAX_MEMORY_SIZE` do Django (2,5 MB por padrao):
    ler `request.body` de uma requisicao maior levanta `RequestDataTooBig`
    antes de a view rodar. Suba o valor se aceitar fotos grandes.

    Assincrona por contrato: aceita, responde `202` e processa depois.
    Redimensionar dentro da requisicao estoura o tempo limite de leitura do
    PubliBot e faz o mesmo arquivo ser reenviado.
    """
    bloqueio = _proteger(request, limite=LIMITE_DE_PUBLICACAO_POR_MINUTO)
    if bloqueio is not None:
        return bloqueio

    referencia = _uuid_ou_nada(request.POST.get("author_reference"))
    if referencia is None:
        return _erro("invalid_payload", "author_reference ausente ou invalido.", 400)

    arquivo = request.FILES.get("photo")
    if arquivo is None:
        return _erro("invalid_payload", "Campo photo obrigatorio.", 400)

    if arquivo.size > TAMANHO_MAXIMO_DA_FOTO:
        return _erro("payload_too_large", "Foto acima do limite aceito.", 413)

    conteudo = arquivo.read()
    digest = hashlib.sha256(conteudo).hexdigest()

    # Confere a integridade antes de gravar. Um arquivo truncado gravado aqui
    # so apareceria como imagem quebrada na pagina, muito depois.
    informado = request.POST.get("sha256", "")
    if informado and not hmac.compare_digest(informado, digest):
        return _erro("content_rejected", "O digest nao confere com o arquivo.", 422)

    existente = AuthorPhoto.objects.filter(author_reference=referencia).first()
    if existente is not None and existente.sha256 == digest:
        return JsonResponse({"status": "already_exists"}, status=200)

    registro = existente or AuthorPhoto(author_reference=referencia)
    registro.sha256 = digest
    registro.image.save(f"{referencia}.webp", ContentFile(conteudo), save=False)
    registro.received_at = timezone.now()
    registro.processed_at = None
    registro.save()

    # Aqui entraria a fila: gerar as miniaturas, atualizar o cache da caixa de
    # autor. O arquivo ja esta gravado, entao a resposta nao espera por isso.
    return JsonResponse({"status": "accepted", "job_id": str(registro.id)}, status=202)


@require_GET
def pending_questions(request):
    """Perguntas ainda nao confirmadas nem respondidas."""
    bloqueio = _proteger(request)
    if bloqueio is not None:
        return bloqueio

    limite = min(int(request.GET.get("limit", 50)), 200)
    cursor = request.GET.get("cursor", "")

    consulta = VisitorQuestion.objects.filter(
        acknowledged_at__isnull=True, answered_at__isnull=True
    ).order_by("submitted_at", "id")

    if cursor:
        consulta = consulta.filter(id__gt=cursor)

    itens = list(consulta[: limite + 1])
    tem_mais = len(itens) > limite
    itens = itens[:limite]

    return JsonResponse(
        {
            "pending_questions": [
                {
                    "id": str(q.id),
                    "question_text": q.question_text,
                    "submitted_at": q.submitted_at.isoformat(),
                    # O nome so acompanha quando ha consentimento registrado.
                    # Ele nao e necessario para produzir o conteudo.
                    "author_name": q.author_name if q.consent_at else "",
                    "consent_at": q.consent_at.isoformat() if q.consent_at else None,
                }
                for q in itens
            ],
            "next_cursor": str(itens[-1].id) if tem_mais and itens else None,
        }
    )


@csrf_exempt
@require_POST
def acknowledge_questions(request):
    """Confirma o recebimento, para as perguntas nao voltarem no proximo ciclo."""
    bloqueio = _proteger(request)
    if bloqueio is not None:
        return bloqueio

    try:
        ids = json.loads(request.body).get("ids") or []
    except json.JSONDecodeError:
        return _erro("invalid_payload", "Corpo nao e JSON valido.", 400)

    total = VisitorQuestion.objects.filter(id__in=ids, acknowledged_at__isnull=True).update(
        acknowledged_at=timezone.now()
    )

    return JsonResponse({"acknowledged": total})


@require_GET
def publications(request):
    """Consulta por chave de idempotencia, para reconciliar apos timeout."""
    bloqueio = _proteger(request)
    if bloqueio is not None:
        return bloqueio

    chave = request.GET.get("idempotency_key", "")
    if not chave:
        return _erro("invalid_payload", "idempotency_key obrigatorio.", 400)

    achadas = ReceivedPublication.objects.filter(idempotency_key=chave)
    return JsonResponse({"results": [_resposta(p) for p in achadas]})
