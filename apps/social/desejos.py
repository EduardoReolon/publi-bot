"""O que o publico quer, para os pedidos a outra IA (iscas e propostas).

Do Instagram nao da para saber o que as pessoas procuram; da para ter um
vislumbre pelo que elas fazem: o que perguntam e contam nos comentarios, que
posts da conta engajaram mais, o que engaja no nicho e o que outras contas do
nicho oferecem em troca de comentario. Do Google, sim: o que buscam (Radar) e
as buscas que ja trazem gente ao site (Search Console). Tudo por algoritmo,
sem modelo: o pedido so leva o recorte.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.utils import timezone

from apps.social import experimentos, fontes
from apps.social.models import Comentario, ConfiguracaoSocial, Post, ReferenciaDoNicho

LIMITE = 12
DIAS_DE_COMENTARIOS = 120
# "comente QUERO", "comenta 'EU QUERO'", "digite GUIA nos comentarios".
_ISCA_NA_LEGENDA = re.compile(
    r"(?i:comente|comenta|digite|escreva|deixe)\s+(?i:a palavra\s+)?[\"'“”]?"
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9 ]{1,20}?)[\"'“”]?"
    r"(?=[\s.,!:;]|$)"
)


def _resumo(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def iscas_do_nicho(limite: int = 8) -> list[str]:
    """O que outras contas do nicho oferecem em troca de comentario (pela
    legenda dos posts de referencia): "PALAVRA — comeco da legenda (curtidas)"."""
    achados, vistos = [], set()
    for ref in ReferenciaDoNicho.objects.order_by("-comentarios")[:300]:
        achado = _ISCA_NA_LEGENDA.search(ref.legenda or "")
        if not achado:
            continue
        palavra = achado.group(1).strip()
        trecho = (ref.legenda or "")[max(0, achado.start() - 120) : achado.end() + 80]
        chave = (palavra.lower(), trecho[:40])
        if chave in vistos:
            continue
        vistos.add(chave)
        achados.append(f"{palavra} — “{_resumo(trecho, 200)}” ({ref.comentarios} comentarios)")
        if len(achados) >= limite:
            break
    return achados


def secao(destino=None, *, nicho: bool = True) -> list[str]:
    """As linhas do bloco "O que o publico quer" (vazio o que nao houver)."""
    partes = ["", "## O que o publico quer (sinais reais, nao opiniao)"]
    google = fontes.o_que_procuram(LIMITE)
    if google["grupos"]:
        partes.append("Buscas no Google (assunto e volume mensal somado):")
        partes += [f"- {rotulo} ({volume}/mes)" for rotulo, volume in google["grupos"]]
    if google["perguntas"]:
        partes.append("Perguntas reais (Google 'as pessoas tambem perguntam', site, YouTube):")
        partes += [f"- {_resumo(p, 160)}" for p in google["perguntas"]]
    if google["consultas"]:
        partes.append("Buscas que ja trazem gente ao site (Search Console, impressoes):")
        partes += [f"- {c} ({n})" for c, n in google["consultas"]]

    comentarios = Comentario.objects.filter(
        tipo__in=[Comentario.Tipo.PERGUNTA, Comentario.Tipo.RELATO, Comentario.Tipo.RECLAMACAO],
        escrito_em__gte=timezone.now() - timedelta(days=DIAS_DE_COMENTARIOS),
    )
    if destino is not None:
        comentarios = comentarios.filter(post__destino=destino)
    comentarios = list(comentarios.order_by("-escrito_em")[:LIMITE])
    if comentarios:
        partes.append("O que perguntam e contam nos comentarios dos posts (sem nomes):")
        partes += [f"- [{c.get_tipo_display()}] {_resumo(c.texto, 200)}" for c in comentarios]

    posts = Post.objects.filter(situacao=Post.Situacao.PUBLICADO, impulsionado=False)
    if destino is not None:
        posts = posts.filter(destino=destino)
    medidos = []
    for post in posts.order_by("-publicado_em")[:200]:
        v = experimentos.valor(post, ConfiguracaoSocial.Metrica.TAXA)
        if v is not None:
            medidos.append((v, post))
    melhores = sorted(medidos, key=lambda par: par[0], reverse=True)[:5]
    if melhores:
        partes.append("Posts da conta que mais engajaram (taxa sobre o alcance):")
        partes += [
            f"- {100 * v:.1f}%: “{_resumo(p.gancho or p.texto, 140)}”"
            + (f" (com isca: {p.isca.nome})" if p.isca_id else "")
            for v, p in melhores
        ]

    if nicho:
        ofertas = iscas_do_nicho()
        if ofertas:
            partes.append("O que outras contas do nicho oferecem em troca de comentario:")
            partes += [f"- {o}" for o in ofertas]
    if len(partes) == 2:
        partes.append("(ainda sem dados: rode o Radar e conecte as contas)")
    return partes
