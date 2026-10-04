"""Aprovar, agendar, publicar — pela API da rede ou pelo "Copiar para postar".

O post aprovado fica com um horario (o proximo livre da conta). A tarefa de
5 em 5 minutos publica o que venceu, nas contas conectadas. Conta sem API (o
Google antes de liberar o acesso, ou quem nao quis conectar) fica com o post
pronto para copiar; a pessoa posta e cola o endereco do post, e dali em
diante a medicao e a mesma.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.social.escolha import proximo_horario
from apps.social.models import Post
from apps.social.redes import rede
from apps.social.redes.base import ErroDaRede, ImagemPublica, NaoConectado

logger = logging.getLogger("publibot.social")

MAXIMO_DE_TENTATIVAS = 3


def aprovar(post: Post, *, por, quando=None) -> Post:
    post.situacao = Post.Situacao.APROVADO
    post.aprovado_por = por
    post.aprovado_em = timezone.now()
    post.agendado_para = quando or proximo_horario(post.destino)
    post.erro = ""
    post.save(
        update_fields=[
            "situacao",
            "aprovado_por",
            "aprovado_em",
            "agendado_para",
            "erro",
            "atualizado_em",
        ]
    )
    # A outra versao do mesmo artigo (A/B) sai da fila de revisao.
    irmas = (
        Post.objects.filter(situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.RASCUNHO])
        .filter(models_mesma_familia(post))
        .exclude(pk=post.pk)
    )
    irmas.update(situacao=Post.Situacao.DESCARTADO, atualizado_em=timezone.now())
    return post


def models_mesma_familia(post: Post):
    from django.db.models import Q

    raiz = post.variante_de_id or post.pk
    return Q(pk=raiz) | Q(variante_de_id=raiz)


def texto_para_copiar(post: Post) -> str:
    """O que a pessoa cola na rede, com o link no lugar certo."""
    f = rede(post.destino.rede).formato
    partes = [post.texto]
    link = (post.extras or {}).get("link", "")
    if f.link == "texto" and link:
        partes.append(link)
    return "\n\n".join(p for p in partes if p)


def publicar(post: Post, *, http=None) -> Post:
    """Publica pela API. Conta nao conectada: NaoConectado (fica para copiar).
    Falha da rede: volta para APROVADO (tenta na proxima volta) ate o maximo
    de tentativas, e entao FALHOU."""
    r = rede(post.destino.rede)
    if r.publicador is None:
        raise NaoConectado("esta rede ainda nao publica pela API.")
    publicador = r.publicador(post.destino, http=http)
    imagens = [
        ImagemPublica(url=i.get("url", ""), caminho=i.get("caminho", "")) for i in post.imagens
    ]
    try:
        publicado = publicador.publicar(post, texto_para_copiar(post), imagens)
    except NaoConectado:
        _voltar(post, Post.Situacao.APROVADO, contar=False)
        raise
    except ErroDaRede as exc:
        post.erro = str(exc)[:2000]
        final = post.tentativas + 1 >= MAXIMO_DE_TENTATIVAS
        _voltar(post, Post.Situacao.FALHOU if final else Post.Situacao.APROVADO, contar=True)
        raise
    post.tentativas += 1
    marcar_publicado(post, url=publicado.url, id_remoto=publicado.id_remoto)
    comentario = (post.extras or {}).get("primeiro_comentario")
    link = (post.extras or {}).get("link")
    if r.formato.link == "comentario" and link:
        try:
            publicador.comentar(post, f"{comentario} {link}".strip())
        except ErroDaRede as exc:
            post.erro = f"publicado, mas o comentario com o link falhou: {exc}"[:2000]
            post.save(update_fields=["erro", "atualizado_em"])
    return post


def _voltar(post: Post, situacao: str, *, contar: bool) -> None:
    post.situacao = situacao
    if contar:
        post.tentativas += 1
    post.save(update_fields=["situacao", "tentativas", "erro", "atualizado_em"])


def marcar_publicado(post: Post, *, url: str = "", id_remoto: str = "") -> Post:
    post.situacao = Post.Situacao.PUBLICADO
    post.publicado_em = timezone.now()
    post.url_remota = url[:500]
    post.id_remoto = id_remoto[:300]
    post.erro = ""
    post.save(
        update_fields=[
            "situacao",
            "publicado_em",
            "url_remota",
            "id_remoto",
            "erro",
            "tentativas",
            "atualizado_em",
        ]
    )
    return post


def publicar_vencidos(*, http=None) -> int:
    """Os aprovados com horario vencido, nas contas conectadas. Cada post e
    "reservado" (APROVADO -> PUBLICANDO numa so instrucao) antes de ir a rede:
    dois workers nunca publicam o mesmo post duas vezes."""
    feitos = 0
    vencidos = Post.objects.filter(
        situacao=Post.Situacao.APROVADO, agendado_para__lte=timezone.now()
    ).select_related("destino", "abordagem")
    for post in vencidos:
        if not post.destino.conectado:
            continue  # fica para copiar: a tela mostra "na hora de postar"
        reservado = Post.objects.filter(pk=post.pk, situacao=Post.Situacao.APROVADO).update(
            situacao=Post.Situacao.PUBLICANDO
        )
        if not reservado:
            continue
        post.situacao = Post.Situacao.PUBLICANDO
        try:
            publicar(post, http=http)
            feitos += 1
        except NaoConectado as exc:
            post.destino.ultimo_erro = str(exc)[:2000]
            post.destino.save(update_fields=["ultimo_erro"])
        except ErroDaRede:
            logger.warning("Post %s nao publicado (tentativa %s).", post.pk, post.tentativas)
    return feitos
