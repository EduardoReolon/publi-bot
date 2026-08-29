"""Montagem do payload e envio de conteudo aos sites."""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.utils import timezone

from apps.content.models import Article
from apps.integrations.errors import SiteAuthError, SitePermanentError, SiteTransientError
from apps.integrations.models import PublishAttempt, Site

logger = logging.getLogger("publibot.integrations")


class PublicacaoBloqueada(RuntimeError):
    """Uma condicao impede o envio."""


def montar_payload_de_artigo(article: Article, site: Site) -> dict:
    """Monta o corpo enviado ao site.

    Inclui autoria e data de revisao. A especificacao original nao tinha nenhum
    campo de autor: os artigos sairiam sem assinatura, que e o pior formato
    possivel para conteudo tematico — nao ha como avaliar quem escreveu nem com
    que credencial.

    A imagem viaja por REFERENCIA, nunca embutida. Base64 infla o corpo em 33%,
    e o limite padrao do Nginx e de 1 MB: o envio falharia com 413 antes de
    chegar a aplicacao, o SaaS interpretaria como falha transitoria e reenviaria
    megabytes indefinidamente.

    A foto do autor tambem nao vai aqui. O corpo leva `has_photo`, e o no
    responde se quer receber; so entao a foto e enviada, pela rota de arquivos.
    Ver `apps/integrations/fotos.py`.
    """
    payload = {
        "type": "article",
        "idempotency_key": str(article.idempotency_key),
        "title": article.title,
        "slug": article.slug,
        "html_content": article.body_html,
        "excerpt": article.excerpt,
        "meta_description": article.meta_description[:160],
        "focus_keyword": article.focus_keyword,
        "language": site.content_language,
        "author": montar_dados_do_autor(article, site),
        "reviewed_by": article.reviewed_by.get_full_name() if article.reviewed_by else "",
        "reviewed_at": article.reviewed_at.isoformat() if article.reviewed_at else None,
        # Divulgacao renderizada pelo site. Nao e formalidade: o leitor precisa
        # saber como o conteudo foi produzido e quem o revisou.
        "content_disclosure": _montar_divulgacao(article, site),
        "status": "published",
        "publish_at": article.scheduled_for.isoformat() if article.scheduled_for else None,
    }

    if article.outbound_link_url:
        payload["canonical_source"] = article.outbound_link_url

    return payload


def montar_dados_do_autor(article: Article, site: Site) -> dict:
    """Os dados de assinatura que acompanham o conteudo.

    O cadastro de autor e a fonte da verdade, mas nao pode ser exigencia: ha
    artigos anteriores ao cadastro, e um artigo pronto nao deve travar por
    falta de um vinculo. A ordem e cadastro, depois o retrato guardado no
    proprio artigo, depois o padrao do site.
    """
    if article.author_id is not None:
        return article.author.como_payload()

    return {
        "name": article.author_name or site.default_author,
        "credentials": article.author_credentials or site.default_author_credentials,
        "has_photo": False,
    }


def _credencial_do_artigo(article: Article, site: Site) -> str:
    if article.author_id is not None and article.author.credentials:
        return article.author.credentials
    return article.author_credentials or site.default_author_credentials


def _montar_divulgacao(article: Article, site: Site) -> str:
    revisor = article.reviewed_by.get_full_name() if article.reviewed_by else ""
    credencial = _credencial_do_artigo(article, site)
    partes = [
        "Conteudo produzido com apoio de inteligencia artificial a partir de literatura tecnica"
    ]
    if revisor:
        partes.append(f"e revisado por {revisor}")
    if credencial:
        partes.append(f"({credencial})")
    return ". ".join([" ".join(partes), "Nao substitui orientacao profissional."])


def resumir_payload(payload: dict) -> dict:
    """Resumo do que foi enviado, para auditoria.

    Guarda tamanho e digest em vez do corpo: o conteudo ja esta no artigo, e um
    payload completo por tentativa encheria a tabela sem acrescentar nada.
    """
    import json

    bruto = json.dumps(payload, ensure_ascii=False).encode()
    return {
        "bytes": len(bruto),
        "sha256": hashlib.sha256(bruto).hexdigest(),
        "campos": sorted(payload),
        "titulo": payload.get("title", "")[:120],
    }


def publicar_artigo(article: Article, site: Site) -> Article:
    """Entrega o artigo, respeitando idempotencia e o modo de simulacao."""
    from apps.integrations.client import SiteClient

    if article.status not in (Article.Status.APPROVED_SCHEDULED, Article.Status.PUSH_FAILED):
        raise PublicacaoBloqueada(
            f"o artigo esta em {article.get_status_display()!r} e nao pode ser publicado."
        )

    payload = montar_payload_de_artigo(article, site)
    tentativa = article.publish_attempts + 1

    # Monta o payload completo e registra, sem sair para a rede. E o unico jeito
    # seguro de conferir o contrato contra um site real durante o
    # desenvolvimento.
    if settings.PUBLISH_DRY_RUN:
        PublishAttempt.objects.create(
            article=article,
            site=site,
            attempt_number=tentativa,
            payload_summary=resumir_payload(payload),
            dry_run=True,
            succeeded=True,
        )
        logger.info("Simulacao: artigo %s nao foi enviado (PUBLISH_DRY_RUN).", article.pk)
        return article

    cliente = SiteClient(site)

    # Apos um timeout, pergunta antes de reenviar: o conteudo pode ter sido
    # gravado e apenas a resposta ter se perdido.
    if tentativa > 1:
        ja_existe = cliente.reconciliar(article.idempotency_key)
        if ja_existe is not None:
            return _concluir(article, site, ja_existe, tentativa, payload)

    try:
        resposta = cliente.publish(payload, idempotency_key=article.idempotency_key)
    except SiteAuthError as exc:
        return _falhar(article, site, exc, tentativa, payload, terminal=True)
    except SitePermanentError as exc:
        return _falhar(article, site, exc, tentativa, payload, terminal=True)
    except SiteTransientError as exc:
        return _falhar(article, site, exc, tentativa, payload, terminal=False)

    return _concluir(article, site, resposta, tentativa, payload)


def _concluir(article: Article, site: Site, resposta, tentativa: int, payload: dict) -> Article:
    PublishAttempt.objects.create(
        article=article,
        site=site,
        attempt_number=tentativa,
        payload_summary=resumir_payload(payload),
        http_status=200 if resposta.ja_existia else 201,
        succeeded=True,
    )

    article.status = Article.Status.PUBLISHED
    article.published_at = timezone.now()
    article.published_url = resposta.url
    article.remote_id = resposta.remote_id
    article.publish_attempts = tentativa
    article.last_publish_error = ""
    article.last_error_code = ""
    article.next_retry_at = None
    article.save()

    Site.objects.filter(pk=site.pk).update(
        consecutive_failures=0,
        circuit_open_until=None,
        health_status=Site.Health.HEALTHY,
        last_success_at=timezone.now(),
    )

    # Segunda etapa: o no respondeu que quer a foto do autor. Ela vai por fora,
    # depois, e uma falha aqui nao desfaz a publicacao que ja deu certo.
    if resposta.precisa_da_foto:
        from apps.integrations.fotos import registrar_pedido_de_foto

        registrar_pedido_de_foto(article, site)

    return article


def _falhar(
    article: Article, site: Site, erro, tentativa: int, payload: dict, *, terminal: bool
) -> Article:
    PublishAttempt.objects.create(
        article=article,
        site=site,
        attempt_number=tentativa,
        payload_summary=resumir_payload(payload),
        http_status=erro.status,
        error_code=erro.code,
        error_message=str(erro)[:2000],
        succeeded=False,
    )

    article.status = Article.Status.PUSH_FAILED
    article.publish_attempts = tentativa
    article.last_publish_error = str(erro)[:2000]
    article.last_error_code = erro.code

    if terminal:
        # Nao reagenda: credencial recusada ou payload invalido nao melhoram
        # com repeticao. Precisa de alguem olhando.
        article.next_retry_at = None
    else:
        article.next_retry_at = timezone.now() + _proximo_intervalo(tentativa, erro)

    article.save()

    if terminal:
        logger.error("Publicacao terminal do artigo %s: %s", article.pk, erro)
    return article


def _proximo_intervalo(tentativa: int, erro):
    """Backoff exponencial, respeitando `Retry-After` quando o site informa.

    O site sabe melhor que o SaaS quando volta a aceitar requisicoes.
    """
    from datetime import timedelta

    sugerido = getattr(erro, "retry_after", None)
    if sugerido:
        return timedelta(seconds=min(sugerido, 21_600))

    # 300s, 600s, 1200s... saturando em 6h. Cobre cerca de 3 dias de
    # indisponibilidade dentro do limite de tentativas.
    segundos = min(300 * (2 ** (tentativa - 1)), 21_600)
    return timedelta(seconds=segundos)
