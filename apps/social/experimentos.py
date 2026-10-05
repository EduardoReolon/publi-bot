"""O que funciona em cada conta: o PubliBot testa abordagens e aprende.

Como convencer e o que mais vai mudar, entao a escolha da abordagem nao e uma
regra fixa: e um "bandido de varios bracos" (Thompson sampling), o mesmo
metodo que plataformas de anuncio usam para dividir o orcamento entre
criativos. Cada abordagem tem um placar (posts que funcionaram x nao); o
sorteio favorece a que vem ganhando sem parar de testar as outras.

Amostra pequena (o comeco de toda conta) e o problema central, entao:

* **ponto de partida informado** — o placar comeca com o que funcionou na
  mesma rede em outras contas deste cliente (peso 0,5) e em TODAS as contas
  do PubliBot (o "placar coletivo", peso ajustavel), em vez de do zero;
* **poucas abordagens no comeco** — na fase Comeco, so as N com melhor ponto
  de partida entram no sorteio (o resto entra quando houver placar);
* **medir proporcao, nao total** — com a medida "taxa", o post e julgado pelas
  interacoes que importam sobre quem o viu; post com alcance abaixo do minimo
  fica "inconclusivo" (nem bom nem ruim);
* **contra a propria conta** — "funcionou" e acima da mediana DA CONTA, entao
  publico pequeno ou de nicho nao e julgado pela regua de uma conta grande;
* **teste pago** — duas versoes do mesmo tema, com o mesmo valor e o mesmo
  publico: a que tiver taxa maior ganha, e o resultado vale mais que o de
  posts organicos soltos.
"""

from __future__ import annotations

import random
import statistics
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.social import parametros
from apps.social.models import Abordagem, ConfiguracaoSocial, Destino, Post

MINIMO_PARA_COMPARAR = 3
# O placar da mesma rede em outras contas do cliente conta menos que o da conta.
PESO_DA_REDE = 0.5
CHAVE_DO_COLETIVO = "social:placar-coletivo"
INTERACOES = ("salvos", "compartilhamentos", "comentarios")


def valor(post: Post, metrica: str, *, alcance_minimo: int = 0) -> float | None:
    """A medida do post. None: sem dado, ou alcance pequeno demais (inconclusivo)."""
    m = post.metricas or {}
    if metrica == ConfiguracaoSocial.Metrica.CLIQUES:
        return float(post.cliques)
    if metrica == ConfiguracaoSocial.Metrica.CONVERSOES:
        return float(m["conversoes"]) if "conversoes" in m else None
    if metrica == ConfiguracaoSocial.Metrica.TAXA:
        alcance = m.get("alcance")
        if not isinstance(alcance, int | float):
            # Rede sem alcance pela API (Google, perfil pessoal): cliques.
            return float(post.cliques)
        if alcance < max(alcance_minimo, 1):
            return None
        interacoes = sum(m.get(k, 0) or 0 for k in INTERACOES) + post.cliques
        return interacoes / alcance
    partes = [m.get(k) for k in ("curtidas", "comentarios", "compartilhamentos", "salvos")]
    partes = [p for p in partes if isinstance(p, int | float)]
    return float(sum(partes)) if partes else None


def avaliar(destino: Destino) -> int:
    """Marca `sucesso` nos posts organicos com tempo suficiente no ar, e julga os
    testes pagos. Devolve quantos mudou."""
    config = ConfiguracaoSocial.carregar()
    limite = timezone.now() - timedelta(days=parametros.valor("dias_para_medir", config))
    minimo = parametros.valor("alcance_minimo", config)
    posts = list(
        destino.posts.filter(
            situacao=Post.Situacao.PUBLICADO, publicado_em__lte=limite, impulsionado=False
        )
    )
    medidos = [
        (p, v) for p in posts if (v := valor(p, config.metrica, alcance_minimo=minimo)) is not None
    ]
    mudados = 0
    if len(medidos) >= MINIMO_PARA_COMPARAR:
        mediana = statistics.median(v for _p, v in medidos)
        for post, v in medidos:
            mudados += _marcar(post, v > mediana)
    return mudados + avaliar_testes(destino, config)


def _marcar(post: Post, sucesso: bool | None) -> int:
    if post.sucesso == sucesso:
        return 0
    post.sucesso = sucesso
    post.save(update_fields=["sucesso"])
    return 1


def avaliar_testes(destino: Destino, config: ConfiguracaoSocial) -> int:
    """Teste pago: as duas versoes (A/B) impulsionadas, depois dos dias do
    teste. Ganha a de taxa maior (empate: ninguem ganha)."""
    dias = parametros.valor("dias_de_teste", config)
    minimo = parametros.valor("alcance_minimo", config)
    mudados = 0
    for b in destino.posts.filter(impulsionado=True, variante_de__isnull=False).select_related(
        "variante_de"
    ):
        a = b.variante_de
        if not a.impulsionado or not (a.impulso_em and b.impulso_em):
            continue
        fim = max(a.impulso_em, b.impulso_em) + timedelta(days=dias)
        if fim > timezone.now():
            continue
        va = valor(a, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo)
        vb = valor(b, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo)
        if va is None or vb is None or va == vb:
            continue
        mudados += _marcar(a, va > vb) + _marcar(b, vb > va)
    return mudados


# -- Placar ------------------------------------------------------------------------------
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


def placar_deste_cliente() -> dict:
    """{rede: {nome da abordagem: [sucessos, fracassos]}} — para somar entre clientes.
    Pelo NOME: as sementes tem o mesmo nome em todo cliente."""
    saida: dict = {}
    for rede, nome, sucesso in Post.objects.filter(
        sucesso__isnull=False, abordagem__isnull=False
    ).values_list("destino__rede", "abordagem__nome", "sucesso"):
        par = saida.setdefault(rede, {}).setdefault(nome, [0, 0])
        par[0 if sucesso else 1] += 1
    return saida


def consolidar_coletivo(placares: list[dict]) -> dict:
    """Soma os placares de todos os clientes e guarda (cache, 3 dias)."""
    total: dict = {}
    for placar_ in placares:
        for rede, nomes in placar_.items():
            for nome, (s, f) in nomes.items():
                par = total.setdefault(rede, {}).setdefault(nome, [0, 0])
                par[0] += s
                par[1] += f
    cache.set(CHAVE_DO_COLETIVO, total, timeout=3 * 86400)
    return total


def coletivo(rede: str) -> dict:
    return (cache.get(CHAVE_DO_COLETIVO) or {}).get(rede, {})


def _alfa_beta(abordagem: Abordagem, pontos: dict, comum: dict, peso_coletivo: float):
    p = pontos.get(abordagem.pk, {})
    s_col, f_col = comum.get(abordagem.nome, [0, 0])
    alfa = (
        1 + p.get("sucessos", 0) + PESO_DA_REDE * p.get("sucessos_rede", 0) + peso_coletivo * s_col
    )
    beta = (
        1
        + p.get("fracassos", 0)
        + PESO_DA_REDE * p.get("fracassos_rede", 0)
        + peso_coletivo * f_col
    )
    return alfa, beta


def escolher(destino: Destino, n: int = 1, *, rng: random.Random | None = None) -> list[Abordagem]:
    """As `n` abordagens deste post, por sorteio ponderado pelo placar (com o
    ponto de partida das outras contas). No Comeco, so as mais promissoras."""
    from apps.social.estrategia import fase

    rng = rng or random.Random()  # noqa: S311 - sorteio de abordagem, nao segredo
    candidatas = [a for a in Abordagem.objects.filter(ativa=True) if a.vale_para(destino.rede)]
    if not candidatas:
        return []
    config = ConfiguracaoSocial.carregar()
    pontos = placar(destino)
    comum = coletivo(destino.rede)
    peso = parametros.valor("peso_coletivo", config)
    pares = {a.pk: _alfa_beta(a, pontos, comum, peso) for a in candidatas}
    if fase(destino) == "comeco":
        limite = max(parametros.valor("abordagens_no_comeco", config), n)
        # Melhor ponto de partida primeiro (media da beta); empate: ordem do cadastro.
        candidatas = sorted(candidatas, key=lambda a: -pares[a.pk][0] / sum(pares[a.pk]))[:limite]
    sorteio = [(rng.betavariate(*pares[a.pk]), a) for a in candidatas]
    sorteio.sort(key=lambda par: -par[0])
    return [abordagem for _nota, abordagem in sorteio[:n]]


def quadro(destino: Destino) -> list[dict]:
    """Para a tela: cada abordagem, com placar da conta e o coletivo."""
    pontos = placar(destino)
    comum = coletivo(destino.rede)
    linhas = []
    for abordagem in Abordagem.objects.all():
        if not abordagem.vale_para(destino.rede):
            continue
        p = pontos.get(abordagem.pk, {})
        s, f = p.get("sucessos", 0), p.get("fracassos", 0)
        sc, fc = comum.get(abordagem.nome, [0, 0])
        linhas.append(
            {
                "abordagem": abordagem,
                "sucessos": s,
                "fracassos": f,
                "medidos": s + f,
                "taxa": round(100 * s / (s + f)) if s + f else None,
                "coletivo": round(100 * sc / (sc + fc)) if sc + fc else None,
                "coletivo_medidos": sc + fc,
                "publicados": destino.posts.filter(
                    abordagem=abordagem, situacao=Post.Situacao.PUBLICADO
                ).count(),
            }
        )
    linhas.sort(key=lambda x: (x["taxa"] is None, -(x["taxa"] or 0), -x["publicados"]))
    return linhas
