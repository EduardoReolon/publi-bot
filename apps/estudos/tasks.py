"""A busca de uma rodada, na fila (pode levar alguns minutos com muitas cidades)."""

from __future__ import annotations

from celery import shared_task

# Na fila padrao: confere de 2 em 2 minutos, ate 24 h (o prazo da DataForSEO).
CONFERIR_A_CADA = 120


@shared_task(bind=True, max_retries=720)
def buscar_rodada(self, rodada_id: str, trabalho: str | None = None) -> str:
    from apps.estudos import coleta
    from apps.estudos.models import Rodada
    from apps.ops.acompanhamento import Conferindo, acompanhar

    with acompanhar(trabalho) as andamento:
        rodada = Rodada.objects.select_related("estudo").filter(pk=rodada_id).first()
        if rodada is None:
            return "sem rodada"
        esperando = False
        try:
            if rodada.fila:
                esperando = coleta.avancar_na_fila(rodada) == "esperando"
            else:
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
        if esperando:
            raise self.retry(
                exc=Conferindo("na fila padrao da DataForSEO (confere de 2 em 2 minutos)"),
                countdown=CONFERIR_A_CADA,
            )
        return "pronta"


@shared_task
def criar_tenant_do_estudo(
    tenant_id: str, dados: dict, copiar_contas: bool = False, trabalho: str | None = None
) -> str:
    """Cria o schema do tenant novo (o mesmo provisionamento do cadastro) e
    preenche la o Negocio, o Radar e o Guia com o que o estudo achou."""
    from django_tenants.utils import schema_context

    from apps.accounts.models import Tenant
    from apps.accounts.tasks import provision_tenant
    from apps.estudos import passagem
    from apps.ops.acompanhamento import acompanhar

    with acompanhar(trabalho):
        provision_tenant(tenant_id)
        tenant = Tenant.objects.get(pk=tenant_id)
        with schema_context(tenant.schema_name):
            passagem.semear(dados, copiar_contas=copiar_contas)
        return tenant.schema_name
