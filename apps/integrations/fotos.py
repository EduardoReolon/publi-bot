"""Entrega da foto de perfil do autor ao no final, em duas etapas.

Por que duas etapas e nao um campo a mais no corpo da publicacao:

* O no e passivo. Ele nao conhece o PubliBot antes da primeira publicacao e nao
  tem cadastro de autor proprio — logo, so ele sabe se ja tem aquela foto.
  Perguntar e a unica forma de nao mandar o arquivo de novo a cada artigo.
* O corpo da publicacao e JSON. Uma imagem embutida ali viraria base64, 33%
  maior, num corpo que o Nginx corta em 1 MB por padrao: o envio falharia com
  413 antes de chegar a aplicacao, e o SaaS leria isso como falha transitoria e
  reenviaria indefinidamente.

Entao: a publicacao leva `has_photo`, o no responde `author_photo_required`, e
so entao a foto vai — sozinha, multipart, para a rota de arquivos.

A entrega e assincrona dos dois lados. Do lado do no porque a rota aceita e
processa depois; do lado daqui porque o artigo ja foi publicado com sucesso, e
um erro no upload da foto nao pode desfazer isso nem prender o worker de
publicacao.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.integrations.errors import SitePermanentError, SiteTransientError
from apps.integrations.models import AuthorPhotoDelivery, Site

logger = logging.getLogger("publibot.integrations")


def registrar_pedido_de_foto(article, site: Site) -> AuthorPhotoDelivery | None:
    """Anota que o no pediu a foto deste autor e despacha o envio.

    Devolve `None` quando nao ha o que enviar: artigo sem autor cadastrado, ou
    autor sem foto. Nenhum dos dois e erro — a foto e opcional no cadastro, e o
    no pede sempre que nao tem.
    """
    autor = article.author if article.author_id else None
    if autor is None or not autor.photo:
        logger.info(
            "Site %s pediu a foto do autor do artigo %s, que nao tem foto cadastrada.",
            site.pk,
            article.pk,
        )
        return None

    digest = autor.digest_da_foto()
    entrega, criada = AuthorPhotoDelivery.objects.get_or_create(
        site=site,
        author=autor,
        photo_sha256=digest,
        defaults={"status": AuthorPhotoDelivery.Status.PENDING},
    )

    # Ja entregue: o no perguntou de novo, provavelmente porque perdeu o
    # arquivo. Reabre a entrega em vez de ignorar — quem sabe o que ele tem e
    # ele.
    if not criada and entrega.status == AuthorPhotoDelivery.Status.SENT:
        entrega.status = AuthorPhotoDelivery.Status.PENDING
        entrega.save(update_fields=["status"])

    from apps.integrations.tasks import deliver_author_photo

    deliver_author_photo.delay(str(entrega.pk))
    return entrega


def entregar_foto(entrega: AuthorPhotoDelivery) -> AuthorPhotoDelivery:
    """Envia o arquivo. Levanta `SiteTransientError` para o worker retentar."""
    from apps.integrations.client import SiteClient

    autor = entrega.author
    if not autor.photo:
        entrega.status = AuthorPhotoDelivery.Status.FAILED
        entrega.last_error = "o autor nao tem mais foto cadastrada."
        entrega.save(update_fields=["status", "last_error"])
        return entrega

    with autor.photo.open("rb") as arquivo:
        conteudo = arquivo.read()

    entrega.attempts += 1

    try:
        dados = SiteClient(entrega.site).enviar_foto_de_autor(
            referencia=str(autor.pk),
            conteudo=conteudo,
            nome_do_arquivo=f"{autor.pk}.webp",
            sha256=entrega.photo_sha256,
        )
    except SiteTransientError as exc:
        entrega.last_error = str(exc)[:2000]
        entrega.save(update_fields=["attempts", "last_error"])
        raise
    except SitePermanentError as exc:
        entrega.status = AuthorPhotoDelivery.Status.FAILED
        entrega.last_error = str(exc)[:2000]
        entrega.save(update_fields=["status", "attempts", "last_error"])
        logger.error("Foto do autor %s recusada por %s: %s", autor.pk, entrega.site, exc)
        return entrega

    entrega.status = AuthorPhotoDelivery.Status.SENT
    entrega.remote_job_id = str(dados.get("job_id", ""))[:120]
    entrega.last_error = ""
    entrega.delivered_at = timezone.now()
    entrega.save(
        update_fields=["status", "attempts", "remote_job_id", "last_error", "delivered_at"]
    )
    return entrega
