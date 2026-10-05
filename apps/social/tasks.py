from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("publibot.social")


@shared_task(bind=True, max_retries=12)
def escrever_post(self, post_id: str) -> str:
    """Escreve o post. Sem modelo agora (nao configurado, placa ocupada):
    tenta de novo em 10 minutos, ate 2 horas."""
    from apps.social import fontes
    from apps.social.models import Post
    from apps.social.redacao import escrever

    post = Post.objects.select_related("destino", "abordagem").filter(pk=post_id).first()
    if post is None or post.situacao not in {Post.Situacao.SUGERIDO, Post.Situacao.GERANDO}:
        return "nada a fazer"
    try:
        escrever(post)
    except fontes.modelo_indisponivel() as exc:
        post.situacao = Post.Situacao.SUGERIDO
        post.erro = f"esperando o modelo: {exc}"[:2000]
        post.save(update_fields=["situacao", "erro", "atualizado_em"])
        raise self.retry(exc=exc, countdown=600) from exc
    return post.situacao


@shared_task
def sugerir_posts() -> int:
    """Uma vez por dia: a escolha do dia em cada conta (com 'sugerir sozinho')."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.escolha import rodada

    return para_cada_tenant(rodada, "sugerir_posts")


@shared_task
def publicar_posts() -> int:
    """De 5 em 5 minutos: publica os aprovados que venceram, nas contas conectadas,
    e responde os comentarios cuja pergunta ja foi respondida."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.comentarios import responder_aprovadas
    from apps.social.publicacao import publicar_vencidos

    return para_cada_tenant(lambda: publicar_vencidos() + responder_aprovadas(), "publicar_posts")


@shared_task
def medir_posts() -> int:
    """Uma vez por dia: resultado, comentarios e placar das abordagens."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.medicao import rodada

    return para_cada_tenant(rodada, "medir_posts")


@shared_task
def recalcular_temas() -> int:
    """Uma vez por dia: os temas que atravessam os artigos, com a nota."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.temas import recalcular

    return para_cada_tenant(recalcular, "recalcular_temas")


@shared_task
def consolidar_placar_coletivo() -> int:
    """Uma vez por dia: o placar das abordagens somado entre TODOS os clientes
    (so contagens por rede e nome de abordagem; nenhum texto sai do cliente).
    E o ponto de partida de quem esta comecando."""
    from django_tenants.utils import schema_context

    from apps.accounts.varredura import schemas_ativos
    from apps.social.experimentos import consolidar_coletivo, placar_deste_cliente

    placares = []
    for schema in schemas_ativos():
        with schema_context(schema):
            try:
                placares.append(placar_deste_cliente())
            except Exception:
                logger.exception("Placar do tenant %s indisponivel.", schema)
    total = consolidar_coletivo(placares)
    return sum(s + f for nomes in total.values() for s, f in nomes.values())


@shared_task
def recalcular_temas_do_cliente(schema: str) -> int:
    """Os temas de UM cliente, agora (primeira visita a Estrategia sem temas)."""
    from django_tenants.utils import schema_context

    from apps.social.temas import recalcular

    with schema_context(schema):
        return recalcular()


@shared_task
def importar_historico(schema: str, destino_id: str) -> dict:
    """O passado de uma conta recem-conectada: posts, resultado, comentarios e
    o gasto com anuncios. Roda em lotes (a medicao diaria continua o resto)."""
    from django_tenants.utils import schema_context

    from apps.social import anuncios, historico
    from apps.social.models import Destino
    from apps.social.redes.base import ErroDaRede

    with schema_context(schema):
        destino = Destino.objects.filter(pk=destino_id).first()
        if destino is None or not historico.suporta(destino):
            return {}
        try:
            feito = historico.importar(destino)
        except ErroDaRede as exc:
            logger.info("Historico de %s nao importado: %s", destino, exc)
            return {"erro": str(exc)}
        if destino.anuncios_conta_id:
            feito["anuncios"] = anuncios.sincronizar(destino)
        return feito
