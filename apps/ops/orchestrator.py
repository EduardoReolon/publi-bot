"""Avanco de trabalhos de varios passos, com retomada apos interrupcao.

A especificacao original previa retomada via `chain()` do Celery. Isso nao
funciona, por tres razoes concretas:

* A cadeia e materializada no despacho e viaja no cabecalho da mensagem. Se um
  elo esgota as tentativas, morre por perda do worker ou leva SIGKILL, **o
  restante e descartado** sem mecanismo de retomada.
* Manter uma tentativa viva por dias exigiria ETA de dias, e mensagens com ETA
  ficam na memoria do worker que as reservou — um restart apaga a cadeia.
* A cadeia nao e inspecionavel: o painel nao consegue mostrar "parou no passo 3
  de 4".

Aqui o broker e apenas transporte. Cada passo le o estado do banco, faz seu
trabalho, grava o resultado e devolve o controle. Um trabalho interrompido
retoma exatamente de onde parou porque o banco sabe onde ele parou.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.ops.models import GenerationJob

logger = logging.getLogger("publibot.ops")

# Quanto tempo uma execucao pode segurar o trabalho sem dar sinal de vida.
# Maior que a inferencia mais longa esperada; o varredor recupera o que passar.
DURACAO_DA_RESERVA = timedelta(hours=2)


class PassoAdiado(Exception):
    """O passo nao pode rodar agora (sem capacidade, endpoint fora do ar).

    Nao e falha: o trabalho volta a fila e tenta de novo. A distincao importa
    porque contar isso como erro esgotaria as tentativas de um trabalho que so
    estava esperando a GPU ligar.
    """

    def __init__(self, motivo: str, tentar_em_segundos: int = 300):
        super().__init__(motivo)
        self.tentar_em_segundos = tentar_em_segundos


@dataclass(frozen=True)
class Passo:
    numero: int
    nome: str
    executar: Callable[[GenerationJob], dict]


class Fluxo:
    """Sequencia de passos de um tipo de trabalho."""

    def __init__(self, kind: str, passos: list[Passo]):
        self.kind = kind
        self.passos = sorted(passos, key=lambda p: p.numero)

    @property
    def total(self) -> int:
        return len(self.passos)

    def passo(self, numero: int) -> Passo | None:
        for p in self.passos:
            if p.numero == numero:
                return p
        return None


_FLUXOS: dict[str, Fluxo] = {}


def registrar_fluxo(fluxo: Fluxo) -> None:
    _FLUXOS[fluxo.kind] = fluxo


def obter_fluxo(kind: str) -> Fluxo:
    fluxo = _FLUXOS.get(kind)
    if fluxo is None:
        raise KeyError(f"nenhum fluxo registrado para {kind!r}")
    return fluxo


def criar_job(*, kind: str, target_object_id=None) -> GenerationJob:
    fluxo = obter_fluxo(kind)
    return GenerationJob.objects.create(
        kind=kind,
        target_object_id=target_object_id,
        total_steps=fluxo.total,
        status=GenerationJob.Status.PENDING,
    )


def _reservar(job_id) -> GenerationJob | None:
    """Toma o trabalho para esta execucao, se ninguem mais o tiver.

    `select_for_update(skip_locked=True)` faz duas execucoes concorrentes nao
    se atropelarem: a segunda simplesmente nao encontra a linha e desiste, em
    vez de esperar.
    """
    agora = timezone.now()
    token = uuid.uuid4()

    with transaction.atomic():
        job = GenerationJob.objects.select_for_update(skip_locked=True).filter(pk=job_id).first()
        if job is None:
            return None

        if job.status in (GenerationJob.Status.DONE, GenerationJob.Status.FAILED):
            return None

        if job.lease_expires_at and job.lease_expires_at > agora:
            logger.info("Job %s ja esta reservado ate %s", job.pk, job.lease_expires_at)
            return None

        job.lease_token = token
        job.lease_expires_at = agora + DURACAO_DA_RESERVA
        job.status = GenerationJob.Status.RUNNING
        job.attempt_count += 1
        job.save(
            update_fields=[
                "lease_token",
                "lease_expires_at",
                "status",
                "attempt_count",
                "updated_at",
            ]
        )
        return job


def avancar(job_id) -> str:
    """Executa o proximo passo pendente de um trabalho.

    Devolve a situacao resultante. Chamada repetidamente ate o trabalho
    concluir — cada chamada avanca no maximo um passo, e cada passo re-entra
    pelo banco.
    """
    job = _reservar(job_id)
    if job is None:
        return "ignorado"

    fluxo = obter_fluxo(job.kind)

    if job.current_step >= fluxo.total:
        return _concluir(job)

    passo = fluxo.passo(job.current_step)
    if passo is None:
        return _falhar(job, f"passo {job.current_step} nao existe no fluxo {job.kind!r}")

    logger.info("Job %s: executando passo %s (%s)", job.pk, passo.numero, passo.nome)

    try:
        resultado = passo.executar(job)
    except PassoAdiado as adiamento:
        return _adiar(job, adiamento)
    except Exception as exc:
        logger.exception("Job %s falhou no passo %s", job.pk, passo.numero)
        return _falhar(job, f"passo {passo.numero} ({passo.nome}): {exc}")

    return _concluir_passo(job, passo, resultado or {})


def _concluir_passo(job: GenerationJob, passo: Passo, resultado: dict) -> str:
    """Grava o resultado e avanca o contador, numa transacao.

    Gravar o resultado e incrementar o passo precisam acontecer juntos: se o
    processo morresse entre as duas operacoes, o passo seria refeito e a
    inferencia paga duas vezes.
    """
    with transaction.atomic():
        atual = GenerationJob.objects.select_for_update().get(pk=job.pk)
        payloads = dict(atual.step_payloads or {})
        payloads[str(passo.numero)] = resultado
        atual.step_payloads = payloads
        atual.current_step = passo.numero + 1
        atual.last_error = ""
        atual.lease_token = None
        atual.lease_expires_at = None

        if atual.current_step >= atual.total_steps:
            atual.status = GenerationJob.Status.DONE
            atual.finished_at = timezone.now()
        else:
            atual.status = GenerationJob.Status.PENDING

        atual.save()
        return atual.status


def _concluir(job: GenerationJob) -> str:
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.DONE,
        finished_at=timezone.now(),
        lease_token=None,
        lease_expires_at=None,
    )
    return GenerationJob.Status.DONE


def _adiar(job: GenerationJob, adiamento: PassoAdiado) -> str:
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.WAITING_CAPACITY,
        next_attempt_at=timezone.now() + timedelta(seconds=adiamento.tentar_em_segundos),
        last_error=str(adiamento),
        lease_token=None,
        lease_expires_at=None,
        # Adiamento nao e tentativa gasta: o trabalho estava so esperando.
        attempt_count=job.attempt_count - 1,
    )
    return GenerationJob.Status.WAITING_CAPACITY


def _falhar(job: GenerationJob, mensagem: str) -> str:
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.FAILED,
        last_error=mensagem[:4000],
        finished_at=timezone.now(),
        lease_token=None,
        lease_expires_at=None,
    )
    return GenerationJob.Status.FAILED


def jobs_para_retomar(limite: int = 50) -> list[GenerationJob]:
    """Trabalhos prontos para avancar.

    Inclui os de reserva expirada: e assim que a queda de um worker se torna um
    caso comum em vez de um trabalho perdido. A comparacao NUNCA e por
    igualdade de instante — sempre `<=` — para que um atraso do varredor nao
    faca um trabalho ser pulado para sempre.
    """
    agora = timezone.now()
    from django.db.models import Q

    prontos = Q(status=GenerationJob.Status.PENDING) & (
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=agora)
    )
    aguardando = Q(status=GenerationJob.Status.WAITING_CAPACITY, next_attempt_at__lte=agora)
    abandonados = Q(status=GenerationJob.Status.RUNNING, lease_expires_at__lte=agora)

    return list(
        GenerationJob.objects.filter(prontos | aguardando | abandonados).order_by("created_at")[
            :limite
        ]
    )
