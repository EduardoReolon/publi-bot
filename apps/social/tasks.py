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
