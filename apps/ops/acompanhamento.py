"""Tarefas avulsas (fora do orquestrador) na tela de Operacao.

O orquestrador cuida dos trabalhos de varios passos (artigo, capa, PDF). As
tarefas das redes — escrever o post, gerar imagens, animar as cenas, montar o video,
perguntar a outra IA — sao tarefas Celery simples, e ate aqui nao apareciam em
lugar nenhum: a pessoa clicava e nao sabia se estava na fila, rodando ou
esperando a placa.

Agora cada uma vira um `GenerationJob` do tipo TAREFA, desde o clique:

    na_fila(gerar_cenas, post_id, rotulo="Cenas do video", alvo=post.pk)

cria o registro (Aguardando) e despacha a tarefa com `trabalho=<id>`; dentro
dela, `acompanhar(trabalho)` marca Executando, Aguardando capacidade (quando a
tarefa pede para tentar de novo), Concluido ou Falhou, com o motivo.

O varredor do orquestrador ignora este tipo (`jobs_para_retomar`): quem
retoma e o proprio Celery. "Redespachar" na tela manda a mesma tarefa de novo.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from apps.ops.models import GenerationJob

TIPO = GenerationJob.Kind.TAREFA


def na_fila(tarefa, *args, rotulo: str, alvo=None, **kwargs) -> str:
    """Registra e despacha. Devolve o id do registro."""
    job = GenerationJob.objects.create(
        kind=TIPO,
        target_object_id=alvo,
        total_steps=1,
        step_payloads={
            "rotulo": rotulo,
            "tarefa": tarefa.name,
            "args": [str(a) if a is not None else None for a in args],
            "kwargs": kwargs,
        },
    )
    tarefa.delay(*args, **kwargs, trabalho=str(job.pk))
    return str(job.pk)


def redespachar(job: GenerationJob) -> None:
    """A mesma tarefa, com os mesmos argumentos, de novo na fila."""
    from celery import current_app

    dados = job.step_payloads or {}
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.PENDING, last_error="", next_attempt_at=None
    )
    current_app.send_task(
        dados["tarefa"],
        args=dados.get("args") or [],
        kwargs={**(dados.get("kwargs") or {}), "trabalho": str(job.pk)},
    )


class Conferindo(Exception):
    """O `exc` de um `retry` que so volta para conferir um trabalho em curso
    (ex.: a animacao no worker): o registro fica "Executando", com o motivo."""


class _Marcador:
    def __init__(self) -> None:
        self.falha = ""

    def falhou(self, motivo: str) -> None:
        """A tarefa terminou sem fazer o que devia (ex.: virou aviso no post)."""
        self.falha = motivo


def _atualizar(trabalho, **campos) -> None:
    GenerationJob.objects.filter(pk=trabalho).update(**campos)


@contextmanager
def acompanhar(trabalho: str | None):
    """Marca o andamento do registro `trabalho` (nada, se ele e None)."""
    marcador = _Marcador()
    if not trabalho:
        yield marcador
        return
    from celery.exceptions import Retry

    _atualizar(
        trabalho,
        status=GenerationJob.Status.RUNNING,
        attempt_count=F("attempt_count") + 1,
        next_attempt_at=None,
    )
    try:
        yield marcador
    except Retry as pedido:
        espera = pedido.when if isinstance(pedido.when, int | float) else 600
        _atualizar(
            trabalho,
            status=(
                GenerationJob.Status.RUNNING
                if isinstance(pedido.exc, Conferindo)
                else GenerationJob.Status.WAITING_CAPACITY
            ),
            last_error=str(pedido.exc or pedido.message or "tentando de novo")[:2000],
            next_attempt_at=timezone.now() + timedelta(seconds=espera),
        )
        raise
    except Exception as exc:
        _atualizar(
            trabalho,
            status=GenerationJob.Status.FAILED,
            last_error=f"{type(exc).__name__}: {exc}"[:2000],
            finished_at=timezone.now(),
        )
        raise
    if marcador.falha:
        _atualizar(
            trabalho,
            status=GenerationJob.Status.FAILED,
            last_error=marcador.falha[:2000],
            finished_at=timezone.now(),
        )
    else:
        _atualizar(
            trabalho,
            status=GenerationJob.Status.DONE,
            current_step=1,
            last_error="",
            finished_at=timezone.now(),
        )
