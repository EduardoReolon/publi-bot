"""Tasks de publicacao e coleta.

O beat tem apenas TRES entradas fixas, todas de infraestrutura. A cadencia de
cada site vive no banco, em `PublicationSchedule` — o beat da o batimento
cardiaco, o banco da a cadencia. Fosse ao contrario, mudar o horario de um
cliente exigiria implantacao.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("publibot.integrations")


@shared_task
def tick_publication_scheduler(limite: int = 50) -> int:
    """Publica o que ja passou da hora. Roda a cada minuto.

    A consulta usa `scheduled_for <= agora`, nunca igualdade de minuto: com
    igualdade, qualquer atraso — uma implantacao, um pico de carga — faria o
    horario ser pulado e o conteudo nunca sair, sem erro em lugar nenhum.
    """
    from apps.integrations.scheduling import conteudo_pronto_para_publicar

    itens = conteudo_pronto_para_publicar(limite=limite)
    for tipo, identificador in itens:
        publish_content.delay(tipo, identificador)

    if itens:
        logger.info("Agendador despachou %s publicacao(oes).", len(itens))
    return len(itens)


@shared_task(bind=True, acks_late=True, max_retries=20)
def publish_content(self, tipo: str, identificador: str) -> str:
    """Entrega um artigo ou uma resposta ao site.

    Recebe apenas o tipo e o id. O payload e montado a partir do banco — enviar
    o payload pronto na mensagem faria cada nova tentativa carregar o conteudo
    inteiro pelo broker.
    """
    from apps.content.models import Answer, Article
    from apps.integrations.errors import SiteTransientError
    from apps.integrations.models import Site
    from apps.integrations.publishing import publicar_artigo

    site = Site.objects.first()
    if site is None:
        logger.error("Nenhum site cadastrado; publicacao de %s abortada.", identificador)
        return "sem_site"

    if tipo == "article":
        artigo = Article.objects.filter(pk=identificador).first()
        if artigo is None:
            return "inexistente"

        try:
            publicar_artigo(artigo, site)
        except SiteTransientError as exc:
            # `countdown` a partir do proprio erro respeita o `Retry-After` que
            # o site informou: ele sabe melhor quando volta a aceitar.
            espera = getattr(exc, "retry_after", None) or 300
            raise self.retry(exc=exc, countdown=min(espera, 21_600)) from exc

        artigo.refresh_from_db()
        return artigo.status

    if tipo == "answer":
        resposta = Answer.objects.filter(pk=identificador).first()
        return resposta.status if resposta else "inexistente"

    return "tipo_desconhecido"


@shared_task(bind=True, acks_late=True, max_retries=8)
def deliver_author_photo(self, entrega_id: str) -> str:
    """Segunda etapa do envio do autor: a foto, so quando o no pede.

    Task propria e nao um trecho de `publish_content` porque o artigo ja esta
    publicado quando isto roda: uma falha de upload nao pode marcar a
    publicacao como falha nem fazer o texto ser reenviado.
    """
    from apps.integrations.errors import SiteTransientError
    from apps.integrations.fotos import entregar_foto
    from apps.integrations.models import AuthorPhotoDelivery

    entrega = AuthorPhotoDelivery.objects.filter(pk=entrega_id).first()
    if entrega is None:
        return "inexistente"

    if entrega.status == AuthorPhotoDelivery.Status.SENT:
        return "ja_entregue"

    try:
        entregar_foto(entrega)
    except SiteTransientError as exc:
        espera = getattr(exc, "retry_after", None) or 300
        raise self.retry(exc=exc, countdown=min(espera, 21_600)) from exc

    return entrega.status


@shared_task
def check_publication_buffer() -> int:
    """Avisa quando a reserva de conteudo pronto esta baixa. Roda a cada 15 min.

    Nao dispara producao sozinho: geracao consome GPU e trabalho humano de
    revisao. Quem decide produzir e uma pessoa.
    """
    from apps.integrations.models import Site
    from apps.integrations.scheduling import contar_reserva, reserva_esta_baixa

    avisos = 0
    for site in Site.objects.filter(publishing_paused=False):
        if reserva_esta_baixa(site):
            logger.warning(
                "Reserva baixa em %s: %s pronto(s), minimo %s.",
                site,
                contar_reserva(site),
                site.schedule.buffer_threshold,
            )
            avisos += 1
    return avisos


@shared_task
def fetch_pending_questions(site_id: str | None = None) -> int:
    """Importa perguntas novas dos sites.

    Confirma o recebimento imediatamente. Sem a confirmacao, cada ciclo
    reimportaria as mesmas perguntas: a unica coisa que as remove do estado
    pendente e a publicacao da resposta, que so acontece apos revisao humana —
    potencialmente dias depois. O resultado seria a mesma resposta gerada
    repetidamente, gastando GPU.
    """
    from datetime import timedelta

    from apps.content.models import Question
    from apps.integrations.client import SiteClient
    from apps.integrations.errors import SiteError
    from apps.integrations.models import Site

    sites = Site.objects.filter(pk=site_id) if site_id else Site.objects.all()
    total = 0

    for site in sites:
        if not site.suporta("qa"):
            continue

        try:
            dados = SiteClient(site).pending_questions()
        except SiteError as exc:
            logger.warning("Falha ao coletar perguntas de %s: %s", site, exc)
            continue

        importadas = []
        for item in dados.get("pending_questions", []):
            _, criada = Question.objects.get_or_create(
                site=site,
                remote_id=str(item["id"]),
                defaults={
                    "question_text": item.get("question_text", "")[:500],
                    # Sem consentimento registrado, nenhuma identificacao e
                    # guardada. O nome nao e necessario para produzir o texto.
                    "author_pseudonym": _pseudonimizar(item) if item.get("consent_at") else "",
                    "consent_at": item.get("consent_at") or None,
                    "submitted_at": item.get("submitted_at") or timezone.now(),
                    "retention_until": timezone.now() + timedelta(days=90),
                },
            )
            importadas.append(str(item["id"]))
            if criada:
                total += 1

        if importadas and site.suporta("qa"):
            try:
                SiteClient(site).acknowledge_questions(importadas)
            except SiteError as exc:
                logger.warning("Falha ao confirmar perguntas em %s: %s", site, exc)

    if total:
        logger.info("Importadas %s pergunta(s) nova(s).", total)
    return total


def _pseudonimizar(item: dict) -> str:
    """Primeiro nome apenas, mesmo com consentimento.

    Guardar o nome completo nao acrescenta nada ao conteudo produzido.
    """
    nome = (item.get("author_name") or "").strip()
    return nome.split(" ")[0][:80] if nome else ""


@shared_task
def purge_expired_questions() -> int:
    """Apaga o texto e a identificacao das perguntas vencidas.

    Apaga o conteudo mas preserva a linha: sem ela, a proxima coleta
    reimportaria a mesma pergunta como se fosse nova.
    """
    from apps.content.models import Question

    vencidas = Question.objects.filter(retention_until__lte=timezone.now(), purged_at__isnull=True)
    total = vencidas.update(question_text="", author_pseudonym="", purged_at=timezone.now())

    if total:
        logger.info("Expurgadas %s pergunta(s) vencida(s).", total)
    return total


@shared_task
def fetch_seo_context(site_id: str) -> int:
    """Atualiza o espelho local do que o site ja publicou."""
    from apps.integrations.client import SiteClient
    from apps.integrations.errors import SiteError
    from apps.integrations.models import Site, SitePost

    site = Site.objects.filter(pk=site_id).first()
    if site is None:
        return 0

    try:
        dados = SiteClient(site).seo_context()
    except SiteError as exc:
        logger.warning("Falha ao obter contexto de %s: %s", site, exc)
        return 0

    total = 0
    for item in dados.get("published_posts", []):
        _, criado = SitePost.objects.update_or_create(
            site=site,
            remote_id=str(item["remote_id"]),
            defaults={
                "title": item.get("title", "")[:300],
                "url": item.get("url", "")[:500],
                "published_at": item.get("published_at") or None,
                "primary_keyword": item.get("primary_keyword", "")[:120],
                "word_count": item.get("word_count", 0),
                "synced_at": timezone.now(),
            },
        )
        if criado:
            total += 1

    return total


@shared_task
def generate_publication_slots() -> int:
    """Mantem os horarios futuros preenchidos."""
    from apps.integrations.models import PublicationSchedule
    from apps.integrations.scheduling import gerar_horarios

    total = 0
    for schedule in PublicationSchedule.objects.filter(is_active=True).select_related("site"):
        total += len(gerar_horarios(schedule))

    return total
