"""O resultado de cada post: cliques (contados pelo link do PubliBot),
conversoes provaveis (o site conta; a jornada passa pelo artigo e a entrada
foi por rede social) e engajamento (pela API, onde a rede deixa).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.social import experimentos, fontes
from apps.social.models import Destino, Post
from apps.social.redes import rede
from apps.social.redes.base import ErroDaRede

logger = logging.getLogger("publibot.social")

JANELA = timedelta(days=30)


def medir(post: Post, *, http=None) -> dict:
    metricas = dict(post.metricas or {})
    artigo = fontes.artigo(post.artigo_id) if post.artigo_id else None
    if artigo is not None and post.publicado_em:
        metricas["conversoes"] = fontes.conversoes_pelas_redes(
            artigo.remote_id, post.publicado_em.date()
        )
    r = rede(post.destino.rede)
    if r.publicador is not None and post.id_remoto and post.destino.conectado:
        try:
            metricas.update(r.publicador(post.destino, http=http).metricas(post))
        except ErroDaRede as exc:
            logger.info("Resultado do post %s pela API indisponivel: %s", post.pk, exc)
    metricas["cliques"] = post.cliques
    metricas["coletado_em"] = timezone.now().isoformat()
    post.metricas = metricas
    post.save(update_fields=["metricas", "atualizado_em"])
    return metricas


def rodada(*, http=None) -> int:
    """Uma vez por dia: mede os posts dos ultimos 30 dias, le comentarios e
    atualiza o placar das abordagens."""
    from apps.social import comentarios

    desde = timezone.now() - JANELA
    medidos = 0
    for post in Post.objects.filter(
        situacao=Post.Situacao.PUBLICADO, publicado_em__gte=desde
    ).select_related("destino"):
        medir(post, http=http)
        try:
            comentarios.ler(post, http=http)
        except ErroDaRede as exc:
            logger.info("Comentarios do post %s nao lidos: %s", post.pk, exc)
        medidos += 1
    for destino in Destino.objects.all():
        experimentos.avaliar(destino)
    return medidos
