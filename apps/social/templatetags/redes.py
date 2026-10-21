"""O botao "Levar as redes" nas telas do nucleo (`social/_botao_redes.html`)."""

from django import template

register = template.Library()


# A sugestao depende de sinais que mudam devagar (conversoes, Search Console):
# guardada por um tempo, a lista de pautas nao refaz a conta a cada visita.
SUGESTAO_VALE_SEGUNDOS = 3 * 3600


@register.simple_tag
def situacao_nas_redes(artigo) -> dict:
    """{"pedido": esperando publicar, "posts": posts do artigo (fora os
    descartados), "sugestao": por que vale as redes (so no ar)}."""
    from django.core.cache import cache
    from django.db import connection

    from apps.social.escolha import esperando_publicar, sugestao_do_artigo
    from apps.social.models import Post

    sugestao = None
    if artigo.status == "published" and artigo.published_url:
        chave = f"redes:sugestao:{connection.schema_name}:{artigo.pk}"
        sugestao = cache.get(chave)
        if sugestao is None:
            sugestao = sugestao_do_artigo(artigo.pk) or {}
            cache.set(chave, sugestao, SUGESTAO_VALE_SEGUNDOS)
    return {
        "pedido": esperando_publicar(artigo.pk),
        "posts": Post.objects.filter(artigo_id=artigo.pk)
        .exclude(situacao=Post.Situacao.DESCARTADO)
        .count(),
        "sugestao": sugestao,
    }
