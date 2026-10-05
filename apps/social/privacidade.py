"""O pedido de exclusao de dados de quem conectou uma rede.

Chega pelo nucleo (apps/accounts/privacidade.py, via extensoes), uma vez por
cliente. `apagar=False` (a pessoa so tirou o app): desconecta. `apagar=True`:
desconecta e apaga o que veio da conta dela — o historico importado, os
comentarios lidos e o gasto com anuncios. Os posts que o PubliBot escreveu
para o cliente ficam (sao do cliente), sem o vinculo com a conta da pessoa.
"""

from __future__ import annotations

from django.db import transaction

from apps.social.models import Anuncio, Comentario, Destino, Post


def excluir(rede: str, usuario: str, *, apagar: bool) -> int:
    total = 0
    with transaction.atomic():
        for destino in Destino.objects.filter(rede=rede, usuario_remoto=usuario):
            if apagar:
                total += Comentario.objects.filter(post__destino=destino).delete()[0]
                total += Anuncio.objects.filter(destino=destino).delete()[0]
                total += destino.posts.filter(motivo=Post.Motivo.HISTORICO).delete()[0]
                destino.referencias.all().delete()
            destino.credenciais = None
            destino.conta_id = destino.conta_nome = destino.usuario_remoto = ""
            destino.anuncios_conta_id = destino.anuncios_conta_nome = ""
            destino.expira_em = None
            destino.save()
            total += 1
    return total
