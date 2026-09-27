"""Medicao de leitura e conversao: do navegador do leitor para o SEU servidor.

Nao faz parte do contrato com o PubliBot: e o que alimenta a rota assinada
`GET /insights/`. Por isso nao tem assinatura — quem chama e o navegador, via
`navigator.sendBeacon` (que nao manda cabecalho nem token de CSRF).

Sendo publicas, as duas rotas se protegem do jeito possivel: corpo pequeno,
limite por IP, robo descartado, numeros com teto e publicacao que precisa
existir. Mesmo assim, qualquer um pode mandar numero falso; o resultado e
indicativo, nao auditavel.

Nada aqui identifica quem le. Nem o IP e gravado: ele so serve ao limite de
requisicoes, que vive no cache por um minuto.
"""

from __future__ import annotations

import json
import re
import uuid

from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from publibot_node.models import Conversao, LeituraDoDia, ReceivedPublication
from publibot_node.throttle import excedeu

TAMANHO_MAXIMO = 4096
LIMITE_POR_IP = 120
LEITURA_MINIMA = 10
TETO_DE_SEGUNDOS = 30 * 60
MAXIMO_NA_JORNADA = 20
ROBO = re.compile(r"bot|crawl|spider|slurp|headless|preview|lighthouse", re.IGNORECASE)


def _aceitar(request) -> dict | None:
    """O corpo, se a requisicao merece ser contada."""
    if len(request.body) > TAMANHO_MAXIMO:
        return None
    if ROBO.search(request.headers.get("User-Agent", "")):
        return None
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
        "REMOTE_ADDR", ""
    )
    if excedeu(f"publibot:medicao:{ip}", LIMITE_POR_IP):
        return None
    try:
        dados = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


def _publicacao(valor):
    try:
        return ReceivedPublication.objects.filter(pk=uuid.UUID(str(valor))).first()
    except ValueError:
        return None


CANAIS = {"organic", "paid", "social", "referral", "email", "direct", "other"}


def _canal(valor) -> str:
    return valor if valor in CANAIS else ("other" if valor else "")


def _segundos(valor) -> int:
    try:
        return max(0, min(int(valor), TETO_DE_SEGUNDOS))
    except (TypeError, ValueError):
        return 0


@csrf_exempt
@require_POST
def leitura(request):
    """Resumo de UMA abertura de pagina: tempo ativo, fim, chamada."""
    dados = _aceitar(request)
    publicacao = _publicacao(dados.get("id")) if dados else None
    if publicacao is None:
        # 204 sempre: o navegador nao faz nada com a resposta, e erro detalhado
        # so ajudaria quem tenta forjar numero.
        return HttpResponse(status=204)

    ativo = _segundos(dados.get("active"))
    incrementos = {
        "views": F("views") + 1,
        "engaged_seconds": F("engaged_seconds") + ativo,
    }
    if ativo >= LEITURA_MINIMA:
        incrementos["engaged_views"] = F("engaged_views") + 1
    if dados.get("end"):
        incrementos["read_to_end"] = F("read_to_end") + 1
    if dados.get("cta_seen"):
        incrementos["cta_views"] = F("cta_views") + 1
    if dados.get("cta_click"):
        incrementos["cta_clicks"] = F("cta_clicks") + 1

    linha, _ = LeituraDoDia.objects.get_or_create(publicacao=publicacao, dia=timezone.localdate())
    LeituraDoDia.objects.filter(pk=linha.pk).update(**incrementos)
    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def conversao(request):
    """Uma conversao, com os artigos lidos antes dela."""
    dados = _aceitar(request)
    if not dados:
        return HttpResponse(status=204)
    try:
        ident = uuid.UUID(str(dados.get("id")))
    except ValueError:
        return HttpResponse(status=204)

    existentes = {
        str(pk)
        for pk in ReceivedPublication.objects.filter(
            pk__in=[
                item.get("id")
                for item in (dados.get("journey") or [])[-MAXIMO_NA_JORNADA:]
                if isinstance(item, dict) and _publicacao(item.get("id"))
            ]
        ).values_list("pk", flat=True)
    }
    jornada = [
        {"remote_id": str(item["id"]), "engaged_seconds": _segundos(item.get("s"))}
        for item in (dados.get("journey") or [])[-MAXIMO_NA_JORNADA:]
        if isinstance(item, dict) and str(item.get("id")) in existentes
    ]
    Conversao.objects.get_or_create(
        id=ident,
        defaults={
            "dia": timezone.localdate(),
            "kind": re.sub(r"[^\w-]", "", str(dados.get("kind") or ""))[:40],
            "via_cta": bool(dados.get("via_cta")),
            "first_channel": _canal(dados.get("first_channel")),
            "last_channel": _canal(dados.get("last_channel")),
            "journey": jornada,
        },
    )
    return HttpResponse(status=204)
