"""Tarefas do radar.

O beat da o batimento (de hora em hora); a intensidade de cada tenant decide
se a rodada e devida. Mudar a frequencia de um cliente nao exige implantacao.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("publibot.radar")


@shared_task
def tick_radar() -> int:
    """Roda o radar nos tenants em que a rodada esta devida."""
    from apps.accounts.varredura import para_cada_tenant

    return para_cada_tenant(_rodar_se_devido, "tick_radar")


def _rodar_se_devido() -> int:
    from apps.radar.coleta import executar_rodada, rodada_devida
    from apps.radar.models import ConfiguracaoDoRadar

    if not rodada_devida(ConfiguracaoDoRadar.carregar()):
        return 0
    executar_rodada()
    return 1


@shared_task
def colher_fila() -> int:
    """Colhe as tarefas da fila padrao da DataForSEO e avanca as rodadas."""
    from apps.accounts.varredura import para_cada_tenant

    return para_cada_tenant(_colher_se_houver, "colher_fila")


def _colher_se_houver() -> int:
    from apps.radar.coleta import colher_fila as colher
    from apps.radar.models import RodadaDoRadar, TarefaNaFila

    # O batimento passa em todos os tenants; a maioria nao tem nada na fila.
    if not (
        TarefaNaFila.objects.filter(situacao=TarefaNaFila.Situacao.AGUARDANDO).exists()
        or RodadaDoRadar.objects.filter(situacao=RodadaDoRadar.Situacao.AGUARDANDO).exists()
    ):
        return 0
    return colher()


@shared_task
def rodar_radar() -> str:
    """Rodada pedida na tela. Despachada de dentro do tenant."""
    from apps.radar.coleta import executar_rodada
    from apps.radar.models import RodadaDoRadar

    rodada = executar_rodada(origem=RodadaDoRadar.Origem.MANUAL)
    return rodada.situacao
