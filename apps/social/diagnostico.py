"""Diagnostico de uma conta, por algoritmo: o que funcionou e o que nao.

Feito para o primeiro minuto depois de conectar uma conta que ja existe (o
historico importado) e para mostrar ao dono da conta. Sem modelo de
linguagem: medianas, comparacoes e regras, cada ponto com o numero que o
sustenta e o tamanho da amostra.

Cuidados de estatistica com amostra pequena:

* compara MEDIANAS (um post viral nao distorce o grupo);
* so aponta diferenca entre grupos (formato, dia, horario, legenda) com pelo
  menos `MINIMO_POR_GRUPO` posts em cada um e diferenca de pelo menos
  `DIFERENCA_RELEVANTE` (30%); abaixo disso, nao diz nada;
* fala em "indicio", nao em causa: dia e formato andam junto com tema.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

from django.utils import timezone

from apps.social import experimentos, parametros
from apps.social.models import Comentario, ConfiguracaoSocial, Destino, Post

MINIMO_DE_POSTS = 6
MINIMO_POR_GRUPO = 3
DIFERENCA_RELEVANTE = 1.3
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
FORMATOS = {
    "CAROUSEL_ALBUM": "carrossel",
    "IMAGE": "imagem unica",
    "VIDEO": "video/reels",
    "REELS": "video/reels",
}
_HASHTAG = re.compile(r"#\w+")


@dataclass
class Ponto:
    texto: str
    evidencia: str = ""


@dataclass
class Diagnostico:
    destino: Destino
    suficiente: bool = False
    medida: str = ""  # "taxa" (sobre o alcance) ou "interacoes" (sem alcance)
    resumo: dict = field(default_factory=dict)
    bons: list[Ponto] = field(default_factory=list)
    ruins: list[Ponto] = field(default_factory=list)
    fazer: list[str] = field(default_factory=list)
    grupos: dict = field(default_factory=dict)
    melhores: list = field(default_factory=list)
    piores: list = field(default_factory=list)
    anuncios: dict = field(default_factory=dict)
    comentarios: dict = field(default_factory=dict)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%".replace(".", ",")


def _brl(x) -> str:
    return f"R$ {Decimal(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _medida(post: Post, minimo: int) -> float | None:
    m = post.metricas or {}
    if isinstance(m.get("alcance"), int | float):
        return experimentos.valor(post, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo)
    return None


def _interacoes(post: Post) -> float | None:
    m = post.metricas or {}
    partes = [m.get(k) for k in ("curtidas", "comentarios", "compartilhamentos", "salvos")]
    partes = [p for p in partes if isinstance(p, int | float)]
    return float(sum(partes)) if partes else None


def _comparar(nome: str, grupos: dict[str, list[float]], formato) -> tuple[dict, Ponto | None]:
    """Medianas por grupo e, se a diferenca for clara, o ponto a dizer."""
    tabela = {
        g: {"n": len(v), "mediana": statistics.median(v)}
        for g, v in grupos.items()
        if len(v) >= MINIMO_POR_GRUPO
    }
    if len(tabela) < 2:
        return tabela, None
    melhor = max(tabela, key=lambda g: tabela[g]["mediana"])
    pior = min(tabela, key=lambda g: tabela[g]["mediana"])
    a, b = tabela[melhor]["mediana"], tabela[pior]["mediana"]
    if b <= 0 or a / b < DIFERENCA_RELEVANTE:
        return tabela, None
    return tabela, Ponto(
        f"{nome}: {melhor} rende {a / b:.1f}x mais que {pior} (indicio).",
        f"mediana {formato(a)} ({tabela[melhor]['n']} posts) x {formato(b)} "
        f"({tabela[pior]['n']} posts)",
    )


def _faixa_do_horario(hora: int) -> str:
    if 5 <= hora < 12:
        return "manha"
    if 12 <= hora < 18:
        return "tarde"
    if 18 <= hora < 24:
        return "noite"
    return "madrugada"


def _faixa_da_legenda(texto: str) -> str:
    n = len(texto or "")
    if n < 300:
        return "legenda curta (<300)"
    if n <= 1000:
        return "legenda media (300-1000)"
    return "legenda longa (>1000)"


def _faixa_de_hashtags(texto: str) -> str:
    n = len(_HASHTAG.findall(texto or ""))
    if n == 0:
        return "sem hashtag"
    if n <= 5:
        return "1 a 5 hashtags"
    if n <= 15:
        return "6 a 15 hashtags"
    return "mais de 15 hashtags"


def montar(destino: Destino) -> Diagnostico:
    config = ConfiguracaoSocial.carregar()
    minimo = parametros.valor("alcance_minimo", config)
    posts = list(
        destino.posts.filter(situacao=Post.Situacao.PUBLICADO, publicado_em__isnull=False).order_by(
            "publicado_em"
        )
    )
    d = Diagnostico(destino=destino)
    agora = timezone.now()
    d.resumo = {
        "posts": len(posts),
        "importados": sum(1 for p in posts if p.motivo == Post.Motivo.HISTORICO),
        "desde": posts[0].publicado_em if posts else None,
        "ate": posts[-1].publicado_em if posts else None,
        "seguidores": destino.seguidores,
    }
    if len(posts) < MINIMO_DE_POSTS:
        _anuncios(d, destino, {})
        return d
    d.suficiente = True

    # A medida: taxa sobre o alcance quando a rede da o alcance; senao, interacoes.
    taxas = {p.pk: v for p in posts if (v := _medida(p, minimo)) is not None}
    if len(taxas) >= MINIMO_DE_POSTS:
        d.medida, valores, formato = "taxa", taxas, _pct
    else:
        d.medida = "interacoes"
        valores = {p.pk: v for p in posts if (v := _interacoes(p)) is not None}
        formato = lambda x: f"{x:.0f}"  # noqa: E731
    medidos = [p for p in posts if p.pk in valores]
    mediana = statistics.median(valores.values()) if valores else 0
    d.resumo["mediana"] = formato(mediana)
    d.resumo["medidos"] = len(medidos)

    _ritmo(d, posts, agora)
    if d.medida == "taxa":
        referencia = (
            parametros.valor(f"taxa_{destino.rede}", config) / 100
            if destino.rede
            in (
                "instagram",
                "linkedin",
            )
            else None
        )
        if referencia:
            if mediana >= referencia:
                d.bons.append(
                    Ponto(
                        "A conta engaja bem quem a ve.",
                        f"taxa mediana {_pct(mediana)}; referencia de mercado ~{_pct(referencia)}",
                    )
                )
            elif mediana < referencia / 2:
                d.ruins.append(
                    Ponto(
                        "Pouca gente que ve os posts interage (salva, compartilha, comenta).",
                        f"taxa mediana {_pct(mediana)}; referencia de mercado ~{_pct(referencia)}",
                    )
                )
                d.fazer.append(
                    "Ganchos de identificacao ('e o meu caso') e conteudo que se salva "
                    "(listas, passo a passo): o PubliBot testa abordagens e mede qual sobe a taxa."
                )

    # Melhores e piores.
    ordenados = sorted(medidos, key=lambda p: valores[p.pk], reverse=True)
    d.melhores = [(p, formato(valores[p.pk])) for p in ordenados[:3]]
    d.piores = [(p, formato(valores[p.pk])) for p in ordenados[-3:][::-1]]

    # Grupos: formato, dia, horario, legenda, hashtags, pergunta.
    divisoes = {
        "Formato": lambda p: FORMATOS.get((p.extras or {}).get("formato", ""), ""),
        "Dia da semana": lambda p: DIAS[timezone.localtime(p.publicado_em).weekday()],
        "Horario": lambda p: _faixa_do_horario(timezone.localtime(p.publicado_em).hour),
        "Tamanho da legenda": lambda p: _faixa_da_legenda(p.texto),
        "Hashtags": lambda p: _faixa_de_hashtags(p.texto),
        "Pergunta na legenda": lambda p: (
            "com pergunta ao publico" if "?" in (p.texto or "") else "sem pergunta"
        ),
    }
    for nome, chave in divisoes.items():
        grupos: dict[str, list[float]] = {}
        for p in medidos:
            g = chave(p)
            if g:
                grupos.setdefault(g, []).append(valores[p.pk])
        tabela, ponto = _comparar(nome, grupos, formato)
        d.grupos[nome] = {
            g: {"n": t["n"], "mediana": formato(t["mediana"])}
            for g, t in sorted(tabela.items(), key=lambda x: -x[1]["mediana"])
        }
        if ponto is not None:
            d.bons.append(ponto)
            melhor = next(iter(d.grupos[nome]))
            uso = sum(1 for p in medidos if chave(p) == melhor) / len(medidos)
            if nome == "Formato" and uso < 0.3:
                d.ruins.append(
                    Ponto(
                        f"O formato que mais funciona ({melhor}) e pouco usado.",
                        f"so {_pct(uso)} dos posts",
                    )
                )
                d.fazer.append(f"Usar mais {melhor} (o PubliBot ja faz carrossel com laminas).")
            if nome in {"Dia da semana", "Horario"}:
                d.fazer.append(
                    f"Concentrar os posts em {melhor} ({nome.lower()}): ajuste os dias e "
                    "horarios da conta em Configurar."
                )

    _tendencia(d, medidos, valores, formato)
    _comentarios(d, destino)
    _anuncios(d, destino, valores, mediana)
    return d


def _ritmo(d: Diagnostico, posts: list[Post], agora) -> None:
    datas = [p.publicado_em for p in posts]
    intervalos = [(b - a).total_seconds() / 86400 for a, b in pairwise(datas)]
    recentes = [x for x in datas if x >= agora - timedelta(days=90)]
    por_semana = len(recentes) / (90 / 7)
    d.resumo["por_semana"] = f"{por_semana:.1f}".replace(".", ",")
    d.resumo["dias_sem_postar"] = (agora - datas[-1]).days
    if intervalos:
        maior = max(intervalos)
        i = intervalos.index(maior)
        d.resumo["maior_pausa"] = round(maior)
        if maior >= 30:
            d.ruins.append(
                Ponto(
                    f"Pausa longa: {round(maior)} dias sem postar.",
                    f"de {timezone.localtime(datas[i]):%d/%m/%Y} a "
                    f"{timezone.localtime(datas[i + 1]):%d/%m/%Y}",
                )
            )
    if por_semana >= 2:
        d.bons.append(Ponto("Ritmo bom de publicacao.", f"{d.resumo['por_semana']} posts/semana"))
    elif por_semana < 1:
        d.ruins.append(
            Ponto(
                "Ritmo baixo: menos de um post por semana nos ultimos 90 dias.",
                f"{len(recentes)} posts em 90 dias",
            )
        )
        d.fazer.append(
            "Frequencia fixa (2 a 3 por semana): o PubliBot escreve a partir dos artigos e "
            "agenda nos dias e horarios da conta."
        )
    if len(intervalos) >= 6:
        media = statistics.mean(intervalos)
        if media > 0 and statistics.pstdev(intervalos) / media > 1.2:
            d.ruins.append(
                Ponto(
                    "Publicacao irregular (rajadas e sumicos).",
                    f"intervalo medio {media:.0f} dias, muito variavel",
                )
            )
    if d.resumo["dias_sem_postar"] >= 21:
        d.ruins.append(
            Ponto(
                f"Conta parada: o ultimo post foi ha {d.resumo['dias_sem_postar']} dias.",
                f"{timezone.localtime(datas[-1]):%d/%m/%Y}",
            )
        )


def _tendencia(d: Diagnostico, medidos: list[Post], valores: dict, formato) -> None:
    if len(medidos) < 2 * MINIMO_DE_POSTS:
        return
    metade = min(10, len(medidos) // 2)
    antes = statistics.median(valores[p.pk] for p in medidos[-2 * metade : -metade])
    depois = statistics.median(valores[p.pk] for p in medidos[-metade:])
    d.resumo["tendencia"] = {"antes": formato(antes), "depois": formato(depois), "n": metade}
    if antes > 0 and depois / antes >= DIFERENCA_RELEVANTE:
        d.bons.append(
            Ponto(
                "Resultado subindo nos posts mais recentes.",
                f"ultimos {metade}: {formato(depois)}; {metade} anteriores: {formato(antes)}",
            )
        )
    elif depois > 0 and antes / depois >= DIFERENCA_RELEVANTE:
        d.ruins.append(
            Ponto(
                "Resultado caindo nos posts mais recentes.",
                f"ultimos {metade}: {formato(depois)}; {metade} anteriores: {formato(antes)}",
            )
        )


def _comentarios(d: Diagnostico, destino: Destino) -> None:
    lidos = Comentario.objects.filter(post__destino=destino)
    total = lidos.count()
    if not total:
        return
    respondidos = lidos.filter(respondido_em__isnull=False).count()
    perguntas = lidos.filter(tipo=Comentario.Tipo.PERGUNTA)
    sem_resposta = perguntas.filter(respondido_em__isnull=True).count()
    d.comentarios = {
        "total": total,
        "respondidos": respondidos,
        "perguntas": perguntas.count(),
        "perguntas_sem_resposta": sem_resposta,
        "reclamacoes": lidos.filter(tipo=Comentario.Tipo.RECLAMACAO).count(),
    }
    taxa = respondidos / total
    if taxa >= 0.6:
        d.bons.append(
            Ponto("Conversa com quem comenta.", f"{_pct(taxa)} dos comentarios respondidos")
        )
    elif total >= 10:
        d.ruins.append(
            Ponto(
                "A maior parte dos comentarios fica sem resposta.",
                f"{respondidos} de {total} respondidos; {sem_resposta} pergunta(s) sem resposta",
            )
        )
        d.fazer.append(
            "Responder na primeira hora (conversa e o sinal que mais pesa para a rede "
            "mostrar o post): o PubliBot transforma perguntas em respostas para aprovar."
        )


def _anuncios(d: Diagnostico, destino: Destino, valores: dict, mediana: float = 0) -> None:
    anuncios = list(destino.anuncios.select_related("post"))
    if not anuncios:
        d.anuncios = {"tem": False}
        return
    gasto = sum((a.gasto for a in anuncios), Decimal("0"))
    alcance = sum(a.alcance or 0 for a in anuncios)
    cliques = sum(a.cliques or 0 for a in anuncios)
    d.anuncios = {
        "tem": True,
        "n": len(anuncios),
        "gasto": _brl(gasto),
        "cpm_alcance": _brl(gasto / alcance * 1000) if alcance else "",
        "custo_por_clique": _brl(gasto / cliques) if cliques else "",
        "sem_post": sum(1 for a in anuncios if a.post_id is None),
    }
    com_clique = [a for a in anuncios if (a.cliques or 0) >= 5 and a.gasto > 0]
    if len(com_clique) >= 2:
        ordem = sorted(com_clique, key=lambda a: a.gasto / a.cliques)
        melhor, pior = ordem[0], ordem[-1]
        d.anuncios["melhor"] = (melhor, _brl(melhor.gasto / melhor.cliques))
        d.anuncios["pior"] = (pior, _brl(pior.gasto / pior.cliques))
        razao = (pior.gasto / pior.cliques) / (melhor.gasto / melhor.cliques)
        if razao >= 2:
            d.ruins.append(
                Ponto(
                    f"O clique mais caro custou {razao:.1f}x o mais barato.",
                    f"'{pior.nome[:60]}' x '{melhor.nome[:60]}'",
                )
            )
    # Dinheiro em post que ja ia mal sozinho: o desperdicio mais comum.
    if valores and mediana:
        em_fracos = sum(
            (a.gasto for a in anuncios if a.post_id and valores.get(a.post_id, mediana) < mediana),
            Decimal("0"),
        )
        ligados = sum((a.gasto for a in anuncios if a.post_id in valores), Decimal("0"))
        if ligados > 0:
            fracao = float(em_fracos / ligados)
            d.anuncios["em_fracos"] = _pct(fracao)
            if fracao >= 0.5:
                d.ruins.append(
                    Ponto(
                        "A maior parte do impulso foi em posts que ja iam mal no organico.",
                        f"{_pct(fracao)} do gasto ligado a posts ({_brl(em_fracos)})",
                    )
                )
                d.fazer.append(
                    "Impulsionar so o que ja foi bem nas primeiras 48 horas: a pagina Estrategia "
                    "aponta qual, quanto e por quantos dias."
                )
            elif fracao <= 0.25:
                d.bons.append(
                    Ponto(
                        "O impulso foi, em geral, para posts que ja funcionavam.",
                        f"so {_pct(fracao)} do gasto em posts abaixo da mediana",
                    )
                )


def como_texto(d: Diagnostico) -> str:
    """O diagnostico em texto corrido, para mandar ao dono da conta."""
    linhas = [f"Diagnostico da conta {d.destino.nome} ({d.destino.rede_nome})"]
    r = d.resumo
    if r.get("desde"):
        linhas.append(
            f"{r['posts']} posts de {timezone.localtime(r['desde']):%d/%m/%Y} a "
            f"{timezone.localtime(r['ate']):%d/%m/%Y}; {r.get('por_semana', '?')} por semana "
            "nos ultimos 90 dias."
        )
    if d.bons:
        linhas += ["", "O que esta bom:"] + [f"- {p.texto} ({p.evidencia})" for p in d.bons]
    if d.ruins:
        linhas += ["", "O que melhorar:"] + [f"- {p.texto} ({p.evidencia})" for p in d.ruins]
    if d.fazer:
        linhas += ["", "O que fazer:"] + [f"- {x}" for x in dict.fromkeys(d.fazer)]
    if d.anuncios.get("tem"):
        linhas += [
            "",
            f"Anuncios: {d.anuncios['n']} anuncios, {d.anuncios['gasto']} no total"
            + (
                f"; custo por clique {d.anuncios['custo_por_clique']}"
                if d.anuncios.get("custo_por_clique")
                else ""
            )
            + ".",
        ]
    return "\n".join(linhas)


def anuncios_desatualizados(destino: Destino, dias: int = 3) -> bool:
    """Ha conta de anuncios ligada e a leitura pela API parou."""
    if not destino.anuncios_conta_id:
        return False
    ultimo = destino.anuncios_sincronizados_em
    return ultimo is None or ultimo < timezone.now() - timedelta(days=dias)
