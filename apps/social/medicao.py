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
# A Meta permite 30 hashtags diferentes por semana por conta: 10 deixa folga.
LIMITE_DE_HASHTAGS = 10


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
        acompanhar_conta(destino, http=http)
        experimentos.avaliar(destino)
    return medidos


def acompanhar_conta(destino: Destino, *, http=None) -> None:
    """Seguidores (todo dia) e referencias do nicho (uma vez por semana)."""
    from apps.social.estrategia import registrar_seguidores

    r = rede(destino.rede)
    if r.publicador is None or not destino.conectado:
        return
    publicador = r.publicador(destino, http=http)
    try:
        n = publicador.seguidores()
        if n is not None:
            registrar_seguidores(destino, n)
    except ErroDaRede as exc:
        logger.info("Seguidores de %s indisponiveis: %s", destino, exc)
    if destino.hashtags_de_referencia and (
        destino.referencias_em is None
        or destino.referencias_em <= timezone.now() - timedelta(days=7)
    ):
        coletar_referencias(destino, publicador)


def coletar_referencias(destino: Destino, publicador) -> int:
    from django.utils.dateparse import parse_datetime

    from apps.social.models import ReferenciaDoNicho

    novas = 0
    hashtags = [h.strip().lstrip("#") for h in destino.hashtags_de_referencia.split(",")]
    for hashtag in [h for h in hashtags if h][:LIMITE_DE_HASHTAGS]:
        try:
            posts = publicador.referencias(hashtag)
        except ErroDaRede as exc:
            logger.info("Referencias de #%s indisponiveis: %s", hashtag, exc)
            continue
        for item in posts:
            _ref, nova = ReferenciaDoNicho.objects.update_or_create(
                destino=destino,
                id_remoto=item["id_remoto"][:120],
                defaults={
                    "hashtag": hashtag[:80],
                    "legenda": item["legenda"][:3000],
                    "formato": item["formato"][:30],
                    "curtidas": item["curtidas"],
                    "comentarios": item["comentarios"],
                    "link": item["link"][:500],
                    "publicado_em": parse_datetime(item.get("publicado_em") or ""),
                    "coletado_em": timezone.now(),
                },
            )
            novas += nova
    destino.referencias_em = timezone.now()
    destino.save(update_fields=["referencias_em"])
    return novas
