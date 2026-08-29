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

    capa = _capa_do_artigo(article)
    if capa:
        payload["cover_image"] = capa

    return payload


def _capa_do_artigo(article: Article) -> dict | None:
    """A capa escolhida, por referencia.

    Sai do payload por inteiro quando nao ha capa escolhida, e nao como um
    objeto vazio: um `cover_image` sem `url` faria o site tentar baixar nada e
    recusar o envio com 422.

    Nenhuma imagem e escolhida automaticamente. Um artigo publicado com uma
    capa que ninguem olhou e como um artigo publicado sem que ninguem tenha
    lido o texto.
    """
    from apps.content.capas import capa_escolhida, digest_da_capa, url_publica_da_capa

    imagem = capa_escolhida(article)
    if imagem is None:
        return None

    url = url_publica_da_capa(imagem)
    if not url:
        # Sem dominio resolvido nao ha link valido a enviar. Sair sem capa e
        # melhor que sair com um link quebrado no site do cliente.
        logger.warning("Artigo %s tem capa escolhida, mas o tenant nao tem dominio.", article.pk)
        return None

    largura, altura = 0, 0
    try:
        from apps.content.imagens import dimensoes

        with imagem.image.open("rb") as arquivo:
            largura, altura = dimensoes(arquivo)
    except (OSError, ValueError):
        # Dimensao e informacao util, nao requisito. O contrato pede url,
        # mime_type e sha256 — e esses saem daqui de qualquer jeito.
        logger.warning("Nao foi possivel ler as dimensoes da capa do artigo %s.", article.pk)

    return {
        "url": url,
        "mime_type": "image/webp",
        "width": largura,
        "height": altura,
        "bytes": imagem.image.size,
        "sha256": digest_da_capa(imagem),
        "alt_text": imagem.alt_text or article.title,
    }


def montar_dados_do_autor(conteudo, site: Site) -> dict:
    """Os dados de assinatura que acompanham o conteudo.

    Serve artigo e resposta: os dois carregam a mesma assinatura, e duplicar
    esta funcao deixaria os dois formatos divergirem com o tempo.

    O cadastro de autor e a fonte da verdade, mas nao pode ser exigencia: ha
    conteudo anterior ao cadastro, e um texto pronto nao deve travar por falta
    de um vinculo. A ordem e cadastro, depois o retrato guardado no proprio
    conteudo, depois o padrao do site.
    """
    if conteudo.author_id is not None:
        return conteudo.author.como_payload()

    return {
        "name": conteudo.author_name or site.default_author,
        "credentials": conteudo.author_credentials or site.default_author_credentials,
        "has_photo": False,
    }


def _credencial_do_conteudo(conteudo, site: Site) -> str:
    if conteudo.author_id is not None and conteudo.author.credentials:
        return conteudo.author.credentials
    return conteudo.author_credentials or site.default_author_credentials


def _montar_divulgacao(conteudo, site: Site) -> str:
    revisor = conteudo.reviewed_by.get_full_name() if conteudo.reviewed_by else ""
    credencial = _credencial_do_conteudo(conteudo, site)
    partes = [
        "Conteudo produzido com apoio de inteligencia artificial a partir de literatura tecnica"
    ]
    if revisor:
        partes.append(f"e revisado por {revisor}")
    if credencial:
        partes.append(f"({credencial})")
    return ". ".join([" ".join(partes), "Nao substitui orientacao profissional."])


def montar_payload_de_resposta(answer, site: Site) -> dict:
    """Monta o corpo de uma resposta de Q&A.

    Mesma assinatura, mesma divulgacao e mesma chave de idempotencia do artigo:
    uma resposta publicada no site de um cliente tem o mesmo peso editorial que
    um artigo, e um caminho mais curto aqui seria uma segunda porta com travas
    diferentes.

    Nao ha imagem. Uma resposta e um texto curto numa listagem de perguntas;
    ilustrar cada uma custaria uma inferencia por pergunta para algo que
    ninguem pediu.
    """
    return {
        "type": "qa",
        "idempotency_key": str(answer.idempotency_key),
        "question_id": answer.question.remote_id,
        "html_content": answer.body_html,
        "language": site.content_language,
        "author": montar_dados_do_autor(answer, site),
        "reviewed_by": answer.reviewed_by.get_full_name() if answer.reviewed_by else "",
        "reviewed_at": answer.reviewed_at.isoformat() if answer.reviewed_at else None,
        "content_disclosure": _montar_divulgacao(answer, site),
        "status": "published",
    }


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
    return _publicar(article, site, payload=montar_payload_de_artigo(article, site))


def publicar_resposta(answer, site: Site):
    """Entrega uma resposta de Q&A.

    Mesmo caminho do artigo, de proposito: mesma idempotencia, mesma
    reconciliacao apos timeout, mesmo backoff, mesmo registro. Uma resposta
    publicada no site de um cliente tem o mesmo peso editorial que um artigo, e
    um caminho proprio aqui divergiria do do artigo na primeira correcao feita
    so de um lado.
    """
    if not site.suporta("qa"):
        raise PublicacaoBloqueada(f"o site {site.name!r} nao declara o recurso 'qa' em /health/.")
    return _publicar(answer, site, payload=montar_payload_de_resposta(answer, site))


def _publicar(conteudo, site: Site, *, payload: dict):
    """O caminho unico de entrega, para artigo e para resposta.

    Os dois models tem os mesmos campos de entrega (`idempotency_key`,
    `publish_attempts`, `next_retry_at`...) e os mesmos estados. Duplicar este
    caminho faria os dois divergirem na primeira correcao feita so de um lado —
    e a que ficasse para tras seria descoberta em producao.
    """
    from apps.integrations.client import SiteClient

    if conteudo.status not in (conteudo.Status.APPROVED_SCHEDULED, conteudo.Status.PUSH_FAILED):
        raise PublicacaoBloqueada(
            f"o conteudo esta em {conteudo.get_status_display()!r} e nao pode ser publicado."
        )

    tentativa = conteudo.publish_attempts + 1

    # Monta o payload completo e registra, sem sair para a rede. E o unico jeito
    # seguro de conferir o contrato contra um site real durante o
    # desenvolvimento.
    if settings.PUBLISH_DRY_RUN:
        _registrar_tentativa(
            conteudo,
            site,
            attempt_number=tentativa,
            payload_summary=resumir_payload(payload),
            dry_run=True,
            succeeded=True,
        )
        logger.info("Simulacao: %s nao foi enviado (PUBLISH_DRY_RUN).", conteudo.pk)
        return conteudo

    cliente = SiteClient(site)

    # Apos um timeout, pergunta antes de reenviar: o conteudo pode ter sido
    # gravado e apenas a resposta ter se perdido.
    if tentativa > 1:
        ja_existe = cliente.reconciliar(conteudo.idempotency_key)
        if ja_existe is not None:
            return _concluir(conteudo, site, ja_existe, tentativa, payload)

    try:
        resposta = cliente.publish(payload, idempotency_key=conteudo.idempotency_key)
    except SiteAuthError as exc:
        return _falhar(conteudo, site, exc, tentativa, payload, terminal=True)
    except SitePermanentError as exc:
        return _falhar(conteudo, site, exc, tentativa, payload, terminal=True)
    except SiteTransientError as exc:
        return _falhar(conteudo, site, exc, tentativa, payload, terminal=False)

    return _concluir(conteudo, site, resposta, tentativa, payload)


def _registrar_tentativa(conteudo, site: Site, **campos) -> None:
    """Grava a tentativa na coluna certa: artigo ou resposta, nunca as duas."""
    from apps.content.models import Article as ModeloDeArtigo

    chave = "article" if isinstance(conteudo, ModeloDeArtigo) else "answer"
    PublishAttempt.objects.create(site=site, **{chave: conteudo}, **campos)


def _concluir(conteudo, site: Site, resposta, tentativa: int, payload: dict):
    _registrar_tentativa(
        conteudo,
        site,
        attempt_number=tentativa,
        payload_summary=resumir_payload(payload),
        http_status=200 if resposta.ja_existia else 201,
        succeeded=True,
    )

    conteudo.status = conteudo.Status.PUBLISHED
    conteudo.published_at = timezone.now()
    conteudo.published_url = resposta.url
    conteudo.remote_id = resposta.remote_id
    conteudo.publish_attempts = tentativa
    conteudo.last_publish_error = ""
    conteudo.last_error_code = ""
    conteudo.next_retry_at = None
    conteudo.save()

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

        registrar_pedido_de_foto(conteudo, site)

    return conteudo


def _falhar(conteudo, site: Site, erro, tentativa: int, payload: dict, *, terminal: bool):
    _registrar_tentativa(
        conteudo,
        site,
        attempt_number=tentativa,
        payload_summary=resumir_payload(payload),
        http_status=erro.status,
        error_code=erro.code,
        error_message=str(erro)[:2000],
        succeeded=False,
    )

    conteudo.status = conteudo.Status.PUSH_FAILED
    conteudo.publish_attempts = tentativa
    conteudo.last_publish_error = str(erro)[:2000]
    conteudo.last_error_code = erro.code

    if terminal:
        # Nao reagenda: credencial recusada ou payload invalido nao melhoram
        # com repeticao. Precisa de alguem olhando.
        conteudo.next_retry_at = None
    else:
        conteudo.next_retry_at = timezone.now() + _proximo_intervalo(tentativa, erro)

    conteudo.save()

    if terminal:
        logger.error("Publicacao terminal de %s: %s", conteudo.pk, erro)
    return conteudo


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
