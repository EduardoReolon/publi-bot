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


@shared_task
def conferir_fontes_vencidas() -> int:
    """Uma vez por dia: artigos no ar que citam fonte vencida, ou dado publico
    que ganhou periodo mais novo na instituicao."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.radar.atualizacoes import pelas_fontes, pelos_dados

    return para_cada_tenant(lambda: pelas_fontes() + pelos_dados(), "conferir_fontes_vencidas")


@shared_task
def descrever_oportunidades() -> int:
    """Pede ao modelo a descricao das melhores oportunidades ainda sem ela.

    De hora em hora: com a placa fora do ar ou ocupada, simplesmente tenta na
    proxima. A oportunidade existe e e decidida sem a descricao.
    """
    from apps.accounts.varredura import para_cada_tenant
    from apps.radar.oportunidades import descrever_pendentes

    return para_cada_tenant(descrever_pendentes, "descrever_oportunidades")


@shared_task
def descrever_uma_oportunidade(pk: str) -> bool:
    """Pedida na tela. Despachada de dentro do tenant."""
    from apps.content.inference import SemModeloConfigurado
    from apps.ops.orchestrator import PassoAdiado
    from apps.radar.models import Oportunidade
    from apps.radar.oportunidades import descrever_oportunidade

    oportunidade = Oportunidade.objects.filter(pk=pk).first()
    if oportunidade is None:
        return False
    try:
        descrever_oportunidade(oportunidade)
    except (PassoAdiado, SemModeloConfigurado) as exc:
        logger.info("Descricao adiada: %s", exc)
        return False
    return True


@shared_task(bind=True, max_retries=12)
def sugerir_sementes(self, com_modelo: bool = True) -> int:
    """Pedida na tela: sugestoes pela pagina do site e, se possivel, pelo modelo.

    A pagina (algoritmo) sai na hora. O modelo, com a placa ocupada ou fora do
    ar, e tentado de novo mais tarde, sem repetir a parte da pagina.
    """
    from django.utils import timezone

    from apps.content.inference import SemModeloConfigurado
    from apps.integrations.errors import SiteError
    from apps.integrations.models import Site
    from apps.integrations.tasks import atualizar_contexto
    from apps.ops.orchestrator import PassoAdiado
    from apps.radar.sugestoes import sugerir_pela_pagina, sugerir_pelo_modelo

    novas = 0
    if self.request.retries == 0:
        site = Site.objects.first()
        antigo = site and (
            site.context_synced_at is None
            or timezone.now() - site.context_synced_at > timezone.timedelta(days=1)
        )
        if antigo and site.api_key_ciphertext:
            try:
                atualizar_contexto(site)
            except SiteError as exc:
                logger.warning("Contexto do site indisponivel: %s", exc)
        novas += sugerir_pela_pagina()
    if com_modelo:
        try:
            novas += sugerir_pelo_modelo()
        except PassoAdiado as exc:
            raise self.retry(
                countdown=exc.tentar_em_segundos or 600, kwargs={"com_modelo": True}
            ) from exc
        except SemModeloConfigurado as exc:
            logger.info("Sugestao pelo modelo pulada: %s", exc)
    return novas


@shared_task
def procurar_links_quebrados() -> int:
    """De hora em hora, poucas paginas por tenant: links quebrados do assunto."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.radar.links_quebrados import verificar_um_lote

    return para_cada_tenant(verificar_um_lote, "procurar_links_quebrados")


@shared_task
def depois_de_publicar(pk: str) -> None:
    """Despachada de dentro do tenant quando um artigo e publicado."""
    from apps.content.models import Article
    from apps.radar.publicados import depois_de_publicar as rever

    artigo = Article.objects.filter(pk=pk).first()
    if artigo is not None:
        rever(artigo)


@shared_task
def conferir_indexacao() -> int:
    """Uma vez por dia: os artigos no ar estao no Google?"""
    from apps.accounts.varredura import para_cada_tenant
    from apps.radar.indexacao import conferir_pendentes

    return para_cada_tenant(conferir_pendentes, "conferir_indexacao")
