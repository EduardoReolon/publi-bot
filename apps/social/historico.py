"""O passado da conta: posts de antes do PubliBot, com resultado e comentarios.

Ao conectar uma conta que ja existe, o PubliBot nao comeca do zero: importa os
posts (ate `MAXIMO`), o resultado de cada um (alcance, salvos,
compartilhamentos...) e os comentarios. Isso:

* calibra a regua da conta — "funcionou" e contra a mediana dela, e a mediana
  passa a ter historia desde o primeiro dia;
* alimenta o diagnostico (diagnostico.py): o que deu certo e errado antes;
* liga o gasto com anuncios aos posts que foram impulsionados (anuncios.py).

As APIs limitam chamadas por hora, entao a importacao anda em lotes: cada
rodada busca a lista inteira (barata) e o detalhe de ate `POR_RODADA` posts
que ainda nao tem; a medicao diaria continua de onde parou.

Comentarios antigos NAO viram Pergunta do PubliBot (a fila seria inundada de
perguntas de anos atras): ficam so para o diagnostico.
"""

from __future__ import annotations

import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.social.models import Destino, Post
from apps.social.redes import rede
from apps.social.redes.base import ErroDaRede, NaoConectado, SemSuporte

logger = logging.getLogger("publibot.social")

MAXIMO = 500
POR_RODADA = 60


def suporta(destino: Destino) -> bool:
    """A rede entrega os posts antigos pela API (hoje: Instagram)."""
    from apps.social.redes.base import Publicador

    r = rede(destino.rede)
    return r.publicador is not None and r.publicador.historico is not Publicador.historico


def _primeira_linha(texto: str) -> str:
    return next((linha.strip() for linha in (texto or "").splitlines() if linha.strip()), "")[:200]


def _endereco(url: str) -> str:
    """O link do post sem consulta nem barra final (o "Ja postei" pode vir com ?igsh=)."""
    return (url or "").split("?", 1)[0].rstrip("/").lower()


def importar(destino: Destino, *, http=None, limite: int = MAXIMO) -> dict:
    """Uma rodada: a lista dos posts e o detalhe de um lote. Pode repetir.
    `limite` menor (a rodada diaria) so olha os posts mais novos."""
    from apps.social import comentarios
    from apps.social.estrategia import registrar_seguidores

    r = rede(destino.rede)
    if r.publicador is None or not destino.conectado:
        raise NaoConectado(f"{destino.nome}: conecte a conta para importar o historico.")
    publicador = r.publicador(destino, http=http)
    try:
        lista = publicador.historico(limite=limite)
    except SemSuporte:
        raise
    except ErroDaRede as exc:
        destino.erro_de_sincronia = str(exc)[:2000]
        destino.save(update_fields=["erro_de_sincronia"])
        raise
    # Ja no PubliBot: publicado pela API (id) ou marcado com "Ja postei" (link).
    conhecidos = set(destino.posts.exclude(id_remoto="").values_list("id_remoto", flat=True))
    ja_postados = destino.posts.exclude(url_remota="").values_list("url_remota", flat=True)
    links = {_endereco(u) for u in ja_postados}
    novos = []
    for item in lista:
        if item["id_remoto"] in conhecidos or _endereco(item.get("link", "")) in links:
            continue
        conhecidos.add(item["id_remoto"])
        novos.append(
            Post(
                destino=destino,
                motivo=Post.Motivo.HISTORICO,
                situacao=Post.Situacao.PUBLICADO,
                texto=item.get("legenda", "")[:5000],
                publicado_em=parse_datetime(item.get("publicado_em") or "") or timezone.now(),
                url_remota=item.get("link", "")[:500],
                id_remoto=item["id_remoto"][:300],
                extras={
                    "importado": True,
                    "formato": item.get("formato", ""),
                    "gancho": _primeira_linha(item.get("legenda", "")),
                },
                metricas={
                    k: item[k] for k in ("curtidas", "comentarios") if item.get(k) is not None
                },
            )
        )
    Post.objects.bulk_create(novos)

    # O detalhe (alcance, salvos...) e os comentarios, em lote.
    pendentes = list(
        destino.posts.filter(motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True).order_by(
            "-publicado_em"
        )[:POR_RODADA]
    )
    sem_detalhe = lidos = 0
    for post in pendentes:
        extras = dict(post.extras or {})
        try:
            post.metricas = {**(post.metricas or {}), **publicador.metricas(post)}
        except ErroDaRede as exc:
            # Post de antes da conta virar profissional, ou de formato sem
            # insights: fica com curtidas e comentarios da lista.
            extras["sem_detalhe"] = str(exc)[:200]
            sem_detalhe += 1
        if (post.metricas or {}).get("comentarios"):
            try:
                lidos += comentarios.ler(post, http=http, criar_perguntas=False)
            except ErroDaRede as exc:
                logger.info("Comentarios antigos de %s nao lidos: %s", post.pk, exc)
        extras["detalhado"] = True
        post.extras = extras
        post.save(update_fields=["metricas", "extras", "atualizado_em"])

    try:
        n = publicador.seguidores()
        if n is not None:
            registrar_seguidores(destino, n)
    except ErroDaRede:
        pass
    faltam = destino.posts.filter(
        motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True
    ).count()
    destino.historico_importado_em = timezone.now()
    destino.sincronizado_em = timezone.now()
    destino.erro_de_sincronia = ""
    destino.save(update_fields=["historico_importado_em", "sincronizado_em", "erro_de_sincronia"])
    return {
        "posts": len(lista),
        "novos": len(novos),
        "detalhados": len(pendentes),
        "sem_detalhe": sem_detalhe,
        "comentarios": lidos,
        "faltam": faltam,
    }


# Na rodada diaria, sem lote pendente: so os posts mais novos (uma chamada),
# para entrar o que a pessoa postou direto na rede, fora do PubliBot.
NOVOS_POR_DIA = 25


def continuar(destino: Destino, *, http=None) -> None:
    """Na medicao diaria: segue a importacao (lote pendente) e traz os posts
    feitos fora do PubliBot desde ontem."""
    if destino.historico_importado_em is None or not suporta(destino):
        return
    pendente = destino.posts.filter(
        motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True
    ).exists()
    try:
        importar(destino, http=http, limite=MAXIMO if pendente else NOVOS_POR_DIA)
    except ErroDaRede as exc:
        logger.info("Historico de %s: %s", destino, exc)
