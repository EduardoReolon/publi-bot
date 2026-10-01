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


def iniciar_geracao(*, kind: str, target_object_id, payload: dict | None = None) -> GenerationJob:
    """Cria o trabalho e agenda o primeiro passo apos o COMMIT.

    `on_commit` importa: sem ele o worker pode buscar o trabalho antes de ele
    existir para outras conexoes e falhar com DoesNotExist — uma corrida
    intermitente e desagradavel de diagnosticar.
    """
    job = criar_job(kind=kind, target_object_id=target_object_id)
    if payload:
        job.step_payloads = payload
        job.save(update_fields=["step_payloads"])
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))
    logger.info("Trabalho %s criado (%s) para %s", job.pk, kind, target_object_id)
    return job


def gerar_artigo(topic, *, fluxo: str = "") -> GenerationJob:
    """`fluxo`: "" (A, fontes curadas) ou "pesquisa" (B). Vai no trabalho."""
    return iniciar_geracao(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=str(topic.pk),
        payload={"fluxo": fluxo},
    )


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


@shared_task(bind=True, max_retries=20)
def gerar_b_quando_pronta(self, topic_id: str) -> bool:
    """Fluxo B pedido antes da pesquisa terminar: gera quando ela fica pronta
    (os resumos vetorizados). Despachada de dentro do tenant."""
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.knowledge.pesquisa import pronta_para_gerar
    from apps.knowledge.referencias import registrar

    pauta = Topic.objects.filter(pk=topic_id).first()
    if pauta is None or fluxos.B not in fluxos.ligados():
        return False
    if fluxos.artigo_do_fluxo(pauta, fluxos.B) or fluxos.em_andamento(pauta, fluxos.B):
        return False
    motivo = pronta_para_gerar(pauta)
    if motivo:
        if "vetoriz" in motivo:
            raise self.retry(countdown=120)
        registrar(pauta, "pesquisa", gerar_depois=False, erro_ao_gerar=motivo)
        return False
    gerar_artigo(pauta, fluxo=fluxos.B)
    registrar(pauta, "pesquisa", gerar_depois=False)
    return True
