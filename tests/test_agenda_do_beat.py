"""A agenda do beat: o que roda de hora em hora ou por dia vai em horario de
relogio. Um intervalo recomeca a contar a cada reinicio do beat (cada
implantacao), e a tarefa diaria que ainda nao tinha rodado nunca rodava."""

from __future__ import annotations

from celery.schedules import crontab
from django.conf import settings


def test_nada_de_hora_ou_dia_em_intervalo():
    longos = [
        nome
        for nome, entrada in settings.CELERY_BEAT_SCHEDULE.items()
        if isinstance(entrada["schedule"], (int, float)) and entrada["schedule"] >= 3600
    ]
    assert longos == []
    assert isinstance(settings.CELERY_BEAT_SCHEDULE["medir-posts"]["schedule"], crontab)
