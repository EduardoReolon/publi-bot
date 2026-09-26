"""Pontos de entrada da ingestao de documentos."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.knowledge.models import Document
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job
from apps.ops.tasks import advance_generation_job

logger = logging.getLogger("publibot.knowledge")


def iniciar_ingestao(document: Document) -> GenerationJob:
    """Coloca o documento na fila de conversao.

    Marcar como QUEUED antes de despachar e o que faz a lista de documentos
    dizer a verdade no intervalo entre o clique e o worker pegar o trabalho.
    """
    Document.objects.filter(pk=document.pk).update(status=Document.Status.QUEUED, failure_reason="")

    # Os trechos ja indexados foram recortados do texto ANTIGO. Reconverter
    # troca o texto — e o motivo mais comum de reconverter e justamente o texto
    # anterior estar errado, lido sem analise de layout. Deixa-los ativos
    # manteria no indice um recorte que nao corresponde mais ao documento, e a
    # busca continuaria devolvendo o conteudo ruim.
    #
    # Desativar, e nao apagar: o texto que a pessoa selecionou continua visivel,
    # para ela comparar com a nova conversao.
    desativados = document.chunks.filter(is_active=True).update(is_active=False)
    if desativados:
        logger.info(
            "Documento %s: %s trecho(s) saem do indice ate a nova curadoria.",
            document.pk,
            desativados,
        )

    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(document.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))
    logger.info("Documento %s enfileirado para conversao (job %s).", document.pk, job.pk)
    return job


@shared_task
def buscar_fontes_da_pauta(topic_id: str) -> int:
    """Busca candidatos a fonte para a pauta. Despachada de dentro do tenant."""
    from apps.content.models import Topic
    from apps.knowledge.fontes_web import buscar_fontes
    from apps.radar.custos import TetoAtingido
    from apps.radar.provedores import ProvedorIndisponivel

    pauta = Topic.objects.filter(pk=topic_id).first()
    if pauta is None:
        return 0
    try:
        return len(buscar_fontes(pauta))
    except (ProvedorIndisponivel, TetoAtingido) as exc:
        logger.warning("Busca de fontes da pauta %s nao completou: %s", topic_id, exc)
        return 0


def pauta_sem_fontes(topic, *, marcar: bool = True) -> None:
    """Marca a pauta como aguardando fontes e, se ligado, busca candidatas.

    Chamado quando a geracao para por falta de embasamento. A busca vai para
    a fila: a geracao ja falhou e nao deve esperar por ela.

    `marcar=False` quando o artigo ja existe (a falta apareceu no
    planejamento): ali o caminho de volta e replanejar o artigo, e a pauta
    continua "usada".
    """
    from apps.content.models import Topic
    from apps.radar.models import ConfiguracaoDoRadar

    if marcar:
        Topic.objects.filter(pk=topic.pk).update(status=Topic.Status.WAITING_SOURCES)
    if ConfiguracaoDoRadar.carregar().buscar_fontes:
        transaction.on_commit(lambda: buscar_fontes_da_pauta.delay(str(topic.pk)))
