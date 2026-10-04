"""O que funciona em cada conta: o PubliBot testa abordagens e aprende.

Como convencer e o que mais vai mudar, entao a escolha da abordagem nao e uma
regra fixa: e um "bandido de varios bracos" (Thompson sampling), o mesmo
metodo que plataformas de anuncio usam para dividir o orcamento entre
criativos. Cada abordagem tem um placar (posts que funcionaram x nao) por
conta; o sorteio favorece a que vem ganhando sem parar de testar as outras —
abordagem nova, sem placar, tem a mesma chance de aparecer.

"Funcionou" = acima da mediana da propria conta, na medida escolhida na
configuracao (cliques, conversoes ou engajamento), depois de 7 dias no ar.
"""

from __future__ import annotations

import random
import statistics
from datetime import timedelta

from django.utils import timezone

from apps.social.models import Abordagem, ConfiguracaoSocial, Destino, Post

DIAS_PARA_MEDIR = 7
MINIMO_PARA_COMPARAR = 3
# O placar da mesma rede em outras contas conta menos que o da propria conta.
PESO_DA_REDE = 0.5


def valor(post: Post, metrica: str) -> float | None:
    if metrica == ConfiguracaoSocial.Metrica.CLIQUES:
        return float(post.cliques)
    m = post.metricas or {}
    if metrica == ConfiguracaoSocial.Metrica.CONVERSOES:
        return float(m["conversoes"]) if "conversoes" in m else None
    partes = [m.get(k) for k in ("curtidas", "comentarios", "compartilhamentos", "salvos")]
    partes = [p for p in partes if isinstance(p, int | float)]
    return float(sum(partes)) if partes else None


def avaliar(destino: Destino) -> int:
    """Marca `sucesso` nos posts com 7 dias ou mais no ar. Devolve quantos mudou."""
    metrica = ConfiguracaoSocial.carregar().metrica
    limite = timezone.now() - timedelta(days=DIAS_PARA_MEDIR)
    posts = list(destino.posts.filter(situacao=Post.Situacao.PUBLICADO, publicado_em__lte=limite))
    medidos = [(p, v) for p in posts if (v := valor(p, metrica)) is not None]
    if len(medidos) < MINIMO_PARA_COMPARAR:
        return 0
    mediana = statistics.median(v for _p, v in medidos)
    mudados = 0
    for post, v in medidos:
        sucesso = v > mediana
        if post.sucesso != sucesso:
            post.sucesso = sucesso
            post.save(update_fields=["sucesso"])
            mudados += 1
    return mudados


def placar(destino: Destino) -> dict:
    """{abordagem_id: {"sucessos", "fracassos", "sucessos_rede", "fracassos_rede"}}"""
    saida: dict = {}
    medidos = Post.objects.filter(
        destino__rede=destino.rede, sucesso__isnull=False, abordagem__isnull=False
    ).values_list("abordagem_id", "destino_id", "sucesso")
    for abordagem_id, destino_id, sucesso in medidos:
        item = saida.setdefault(
            abordagem_id,
            {"sucessos": 0, "fracassos": 0, "sucessos_rede": 0, "fracassos_rede": 0},
        )
        chave = "sucessos" if sucesso else "fracassos"
        item[chave if destino_id == destino.pk else f"{chave}_rede"] += 1
    return saida


def escolher(destino: Destino, n: int = 1, *, rng: random.Random | None = None) -> list[Abordagem]:
    """As `n` abordagens deste post, por sorteio ponderado pelo placar."""
    rng = rng or random.Random()  # noqa: S311 - sorteio de abordagem, nao segredo
    candidatas = [a for a in Abordagem.objects.filter(ativa=True) if a.vale_para(destino.rede)]
    if not candidatas:
        return []
    pontos = placar(destino)
    sorteio = []
    for abordagem in candidatas:
        p = pontos.get(abordagem.pk, {})
        alfa = 1 + p.get("sucessos", 0) + PESO_DA_REDE * p.get("sucessos_rede", 0)
        beta = 1 + p.get("fracassos", 0) + PESO_DA_REDE * p.get("fracassos_rede", 0)
        sorteio.append((rng.betavariate(alfa, beta), abordagem))
    sorteio.sort(key=lambda par: -par[0])
    return [abordagem for _nota, abordagem in sorteio[:n]]


def quadro(destino: Destino) -> list[dict]:
    """Para a tela "O que funciona": cada abordagem, com placar e taxa."""
    pontos = placar(destino)
    linhas = []
    for abordagem in Abordagem.objects.all():
        if not abordagem.vale_para(destino.rede):
            continue
        p = pontos.get(abordagem.pk, {})
        s, f = p.get("sucessos", 0), p.get("fracassos", 0)
        linhas.append(
            {
                "abordagem": abordagem,
                "sucessos": s,
                "fracassos": f,
                "medidos": s + f,
                "taxa": round(100 * s / (s + f)) if s + f else None,
                "publicados": destino.posts.filter(
                    abordagem=abordagem, situacao=Post.Situacao.PUBLICADO
                ).count(),
            }
        )
    linhas.sort(key=lambda x: (x["taxa"] is None, -(x["taxa"] or 0), -x["publicados"]))
    return linhas
