"""O botao "Levar as redes" nas telas do nucleo (`social/_botao_redes.html`)."""

from django import template

register = template.Library()


@register.simple_tag
def situacao_nas_redes(artigo) -> dict:
    """{"pedido": esperando publicar, "posts": posts do artigo (fora os descartados)}."""
    from apps.social.escolha import esperando_publicar
    from apps.social.models import Post

    return {
        "pedido": esperando_publicar(artigo.pk),
        "posts": Post.objects.filter(artigo_id=artigo.pk)
        .exclude(situacao=Post.Situacao.DESCARTADO)
        .count(),
    }
