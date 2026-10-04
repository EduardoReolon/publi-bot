"""Comentarios: perguntas viram Perguntas do PubliBot; a resposta aprovada volta
como resposta ao comentario.

Pergunta ou nao, decidido por algoritmo: termina em "?" ou comeca como
pergunta ("como", "quanto", "posso", "e normal"...). O resto e classificado
por palavras (elogio, reclamacao, relato) so para a tela agrupar.

Nunca responde sozinho: a resposta e a mesma da Pergunta, que passa pela
revisao (e em nome do cliente; regras do conselho profissional, tom).
"""

from __future__ import annotations

import logging
import re
import unicodedata

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.social import fontes
from apps.social.models import Comentario, Post
from apps.social.redes import rede
from apps.social.redes.base import ErroDaRede, SemSuporte

logger = logging.getLogger("publibot.social")

_INICIO_DE_PERGUNTA = re.compile(
    r"^(como|quanto|quantos|quantas|qual|quais|quando|onde|por que|porque|pq|"
    r"pode|posso|podemos|e normal|eh normal|tem como|da pra|vale a pena|"
    r"alguem sabe|voces|o que|existe|serve)\b"
)
_ELOGIO = re.compile(r"\b(parabens|otimo|excelente|adorei|amei|muito bom|top|obrigad)", re.I)
_RECLAMACAO = re.compile(
    r"\b(pessimo|horrivel|absurdo|demora|nao gostei|reclama|caro demais)", re.I
)
_RELATO = re.compile(
    r"\b(comigo|eu tive|eu tenho|minha mae|meu pai|meu filho|minha filha|aconteceu)", re.I
)
MINIMO_DE_PALAVRAS = 4
RESPOSTA_MAXIMA = 900


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def classificar(texto: str) -> str:
    limpo = _sem_acento(texto).strip()
    # Marcacao de amigo ("@fulana") sozinha nao e conversa.
    sem_marcacao = re.sub(r"@\w+", "", limpo).strip()
    if "?" in sem_marcacao or _INICIO_DE_PERGUNTA.search(sem_marcacao):
        if len(sem_marcacao.split()) >= MINIMO_DE_PALAVRAS:
            return Comentario.Tipo.PERGUNTA
    if _RECLAMACAO.search(sem_marcacao):
        return Comentario.Tipo.RECLAMACAO
    if _RELATO.search(sem_marcacao):
        return Comentario.Tipo.RELATO
    if _ELOGIO.search(sem_marcacao):
        return Comentario.Tipo.ELOGIO
    return Comentario.Tipo.OUTRO


def ler(post: Post, *, http=None) -> int:
    """Le os comentarios novos do post. Devolve quantos entraram."""
    r = rede(post.destino.rede)
    if r.publicador is None or not post.id_remoto or not post.destino.conectado:
        return 0
    try:
        lidos = r.publicador(post.destino, http=http).ler_comentarios(post)
    except SemSuporte:
        return 0
    novos = 0
    for lido in lidos:
        if lido.do_dono or not lido.texto.strip():
            continue
        comentario, criado = Comentario.objects.get_or_create(
            post=post,
            id_remoto=lido.id_remoto[:300],
            defaults={
                "texto": lido.texto[:5000],
                "autor": (lido.autor or "").split(" ")[0][:80],
                "escrito_em": parse_datetime(lido.escrito_em or "") or timezone.now(),
                "tipo": classificar(lido.texto),
            },
        )
        if not criado:
            continue
        novos += 1
        if comentario.tipo == Comentario.Tipo.PERGUNTA:
            comentario.pergunta_id = fontes.criar_pergunta(
                comentario.texto, f"rede:{post.destino.rede}:{comentario.id_remoto}"
            )
            comentario.save(update_fields=["pergunta_id"])
    return novos


def resposta_curta(texto: str, url: str) -> str:
    """O comeco da resposta aprovada (frases inteiras) e o link dela."""
    frases = re.split(r"(?<=[.!?])\s+", " ".join((texto or "").split()))
    saida = ""
    for frase in frases:
        if len(saida) + len(frase) + 1 > RESPOSTA_MAXIMA - len(url) - 2:
            break
        saida = f"{saida} {frase}".strip()
    return f"{saida}\n{url}".strip() if url else saida


def responder_aprovadas(*, http=None) -> int:
    """Responde, no proprio comentario, as perguntas cuja resposta foi aprovada."""
    pendentes = list(
        Comentario.objects.filter(
            tipo=Comentario.Tipo.PERGUNTA, pergunta_id__isnull=False, respondido_em__isnull=True
        ).select_related("post__destino")
    )
    if not pendentes:
        return 0
    prontas = fontes.respostas([str(c.pergunta_id) for c in pendentes])
    feitas = 0
    for comentario in pendentes:
        resposta = prontas.get(str(comentario.pergunta_id))
        if resposta is None:
            continue
        post = comentario.post
        r = rede(post.destino.rede)
        texto = resposta_curta(resposta["texto"], resposta.get("url") or post.artigo_url)
        try:
            id_remoto = r.publicador(post.destino, http=http).responder(
                post, comentario.id_remoto, texto
            )
        except ErroDaRede as exc:
            comentario.erro = str(exc)[:2000]
            comentario.save(update_fields=["erro"])
            continue
        comentario.resposta = texto
        comentario.respondido_em = timezone.now()
        comentario.id_da_resposta = (id_remoto or "")[:300]
        comentario.erro = ""
        comentario.save(update_fields=["resposta", "respondido_em", "id_da_resposta", "erro"])
        feitas += 1
    return feitas
