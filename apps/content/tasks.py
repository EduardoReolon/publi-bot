"""Pontos de entrada da geracao de conteudo.

Sao funcoes finas de proposito: criam o trabalho e despacham o primeiro passo.
Toda a logica esta no fluxo (`flows.py`) e no orquestrador, para que a mesma
sequencia rode igual vinda de um clique na interface, de um comando do terminal
ou de uma tarefa agendada.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job
from apps.ops.tasks import advance_generation_job

logger = logging.getLogger("publibot.content")


def iniciar_geracao(*, kind: str, target_object_id) -> GenerationJob:
    """Cria o trabalho e agenda o primeiro passo apos o COMMIT.

    `on_commit` importa: sem ele o worker pode buscar o trabalho antes de ele
    existir para outras conexoes e falhar com DoesNotExist — uma corrida
    intermitente e desagradavel de diagnosticar.
    """
    job = criar_job(kind=kind, target_object_id=target_object_id)
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))
    logger.info("Trabalho %s criado (%s) para %s", job.pk, kind, target_object_id)
    return job


def gerar_artigo(topic) -> GenerationJob:
    return iniciar_geracao(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))


def responder_pergunta(question) -> GenerationJob:
    return iniciar_geracao(kind=GenerationJob.Kind.QA_ANSWER, target_object_id=str(question.pk))


@shared_task
def answer_pending_questions(limite: int = 20) -> int:
    """Enfileira resposta para as perguntas importadas que ainda nao tem uma.

    Existe para o Q&A nao depender de alguem clicar item a item: as perguntas
    chegam do site em lote, e a revisao humana continua obrigatoria no fim.
    """
    from apps.content.models import Question

    pendentes = Question.objects.filter(status=Question.Status.IMPORTED, answer__isnull=True)[
        :limite
    ]

    total = 0
    for pergunta in pendentes:
        responder_pergunta(pergunta)
        Question.objects.filter(pk=pergunta.pk).update(status=Question.Status.DRAFTING)
        total += 1

    if total:
        logger.info("%s pergunta(s) enfileirada(s) para resposta.", total)
    return total
