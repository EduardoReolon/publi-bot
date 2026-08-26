"""Tasks de operacao."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("publibot.ops")


@shared_task(bind=True, acks_late=True, max_retries=3)
def advance_generation_job(self, job_id: str) -> str:
    """Avanca um trabalho em um passo e reagenda se ainda houver o que fazer.

    Recebe apenas o id. A task nao carrega estado: tudo vem do banco, que e o
    que torna a retomada apos interrupcao possivel.
    """
    from apps.ops.models import GenerationJob
    from apps.ops.orchestrator import avancar

    situacao = avancar(job_id)

    # Reencadeia enquanto houver trabalho. Nao e `chain()` do Celery de
    # proposito: cada passo reentra pelo banco, entao uma interrupcao aqui nao
    # descarta os passos seguintes.
    if situacao == GenerationJob.Status.PENDING:
        advance_generation_job.delay(job_id)

    return situacao


@shared_task
def sweep_stalled_jobs(limite: int = 50) -> int:
    """Redespacha trabalhos parados.

    E o que transforma "o worker caiu" e "a GPU ficou dias desligada" em casos
    comuns em vez de trabalho perdido. Roda a cada poucos minutos pelo beat.
    """
    from apps.ops.orchestrator import jobs_para_retomar

    jobs = jobs_para_retomar(limite=limite)
    for job in jobs:
        advance_generation_job.delay(str(job.pk))

    if jobs:
        logger.info("Varredor redespachou %s trabalho(s).", len(jobs))
    return len(jobs)


@shared_task
def release_expired_leases() -> int:
    """Libera reservas de capacidade vencidas.

    Sem isto, a queda de um worker que segurava uma reserva deixaria a conexao
    inutilizavel ate a expiracao — e, no caso de uma reserva longa, por horas.
    """
    from apps.inference.leases import liberar_expiradas

    liberadas = liberar_expiradas()
    if liberadas:
        logger.info("Liberadas %s reserva(s) expirada(s).", liberadas)
    return liberadas
