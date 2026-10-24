"""A busca de uma rodada, na fila (pode levar alguns minutos com muitas cidades)."""

from __future__ import annotations

from celery import shared_task


@shared_task
def buscar_rodada(rodada_id: str, trabalho: str | None = None) -> str:
    from apps.estudos import coleta
    from apps.estudos.models import Rodada
    from apps.ops.acompanhamento import acompanhar

    with acompanhar(trabalho) as andamento:
        rodada = Rodada.objects.select_related("estudo").filter(pk=rodada_id).first()
        if rodada is None:
            return "sem rodada"
        try:
            coleta.executar(rodada)
        except Exception as exc:  # o erro fica na rodada, para a tela
            from apps.radar.custos import TetoAtingido
            from apps.radar.provedores import ProvedorIndisponivel

            Rodada.objects.filter(pk=rodada.pk).update(
                situacao=Rodada.Situacao.FALHOU, erro=str(exc)[:2000]
            )
            if not isinstance(exc, ProvedorIndisponivel | TetoAtingido | ValueError):
                raise
            andamento.falhou(str(exc))
            return "falhou"
        return "pronta"
