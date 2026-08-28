"""Pontos de entrada da ingestao de documentos."""

from __future__ import annotations

import logging

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
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(document.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))
    logger.info("Documento %s enfileirado para conversao (job %s).", document.pk, job.pk)
    return job
