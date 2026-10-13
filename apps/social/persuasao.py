"""Os principios de persuasao de Cialdini, do jeito honesto.

Robert Cialdini (Influence, 1984; Pre-Suasion, 2016) descreve sete atalhos que
fazem as pessoas dizerem sim. Todos funcionam; quase todos tem uma versao
desonesta, que em saude e vedada (e queima a conta). Aqui cada um vem com o
uso legitimo e o que nunca fazer — e o post diz quais usou, para medir.

* Quem escreve recebe a lista e um **principio principal** por post, escolhido
  por algoritmo: o menos usado nas ultimas semanas da conta, entre os que o
  material permite (sem dado com numero, nao ha prova social; sem isca, a
  reciprocidade e o valor entregue no proprio post) — e assim todos sao
  testados.
* O post devolve `principios` (os que usou); o algoritmo confirma os
  evidentes (fonte na lamina = autoridade, isca = reciprocidade, enquete =
  compromisso, numero do estudo = prova social).
* "O que funciona" mostra, por conta, a medida mediana dos posts com cada
  principio contra os sem ele (`quadro`).
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

PRINCIPIOS = {
    "reciprocidade": {
        "nome": "Reciprocidade",
        "uso": (
            "entregue algo util ANTES de pedir qualquer coisa: a dica inteira, o checklist, o "
            "passo que a pessoa usa hoje; a isca (material em troca do comentario) e reciprocidade"
        ),
        "nunca": "presente com condicao escondida ou 'gratis' que nao e",
    },
    "compromisso": {
        "nome": "Compromisso e coerencia",
        "uso": (
            "peca um pequeno sim facil de dar: responder uma pergunta simples, escolher uma "
            "opcao, salvar o post — quem se compromete com pouco tende a seguir"
        ),
        "nunca": "pergunta capciosa ou que envergonha quem responde",
    },
    "prova_social": {
        "nome": "Prova social",
        "uso": (
            "mostre que outras pessoas vivem o mesmo, com o numero REAL do material ('38% dos "
            "adultos...') ou a pergunta que mais aparece nos comentarios"
        ),
        "nunca": "numero, depoimento ou 'todo mundo esta fazendo' inventado",
    },
    "autoridade": {
        "nome": "Autoridade",
        "uso": (
            "diga de onde vem o que o post afirma (o estudo, a instituicao, a fonte do artigo) "
            "e a experiencia de quem fala — sem titulo que a pessoa nao tem"
        ),
        "nunca": "'especialistas dizem' sem fonte, jaleco como enfeite, promessa de resultado",
    },
    "afinidade": {
        "nome": "Afinidade",
        "uso": (
            "fale como quem conhece a rotina do publico, com as palavras dele, a situacao "
            "que ele reconhece ('e o meu caso'); mostre o lado humano de quem atende"
        ),
        "nunca": "intimidade forcada ou elogio vazio",
    },
    "escassez": {
        "nome": "Escassez",
        "uso": (
            "so a verdadeira: o que POUCA GENTE SABE (o achado que contraria o senso comum), "
            "um prazo ou vaga que existe de fato (campanha do mes, turma com data)"
        ),
        "nunca": "'so hoje', 'ultimas vagas', contagem regressiva ou medo inventado",
    },
    "unidade": {
        "nome": "Unidade",
        "uso": (
            "fale de dentro do grupo do publico ('nos, que temos uma oficina...', 'quem e mae "
            "de primeira viagem sabe...'): identidade compartilhada, nao so semelhanca"
        ),
        "nunca": "'nos contra eles', atacar outro grupo",
    },
}

# Principios que o algoritmo reconhece no post pronto (alem dos declarados).
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?\s?%|\d{2,}")
_PEDIDO = re.compile(r"\b(coment|respond|salve|salva|vote|escolha|marque)\w*", re.I)
_NOS = re.compile(r"\b(nos|n[oó]s que|quem (e|tem|vive|cuida))\b", re.I)

JANELA_EM_POSTS = 20


def valido(chave: str) -> str:
    return chave if chave in PRINCIPIOS else ""


def candidatos(post, mat: dict | None = None) -> list[str]:
    """Os principios que o material deste post permite usar com honestidade."""
    saida = ["reciprocidade", "compromisso", "afinidade", "autoridade", "unidade", "escassez"]
    if (mat or {}).get("achados"):  # dado com numero no material
        saida.append("prova_social")
    return saida


def principal_da_vez(post, mat: dict | None = None) -> str:
    """O principio menos usado nos ultimos posts da conta (assim todos sao
    testados), entre os que o material permite."""
    from apps.social.models import Post

    recentes = (
        Post.objects.filter(destino=post.destino)
        .exclude(pk=post.pk)
        .exclude(motivo=Post.Motivo.HISTORICO)
        .order_by("-criado_em")[:JANELA_EM_POSTS]
    )
    usos = Counter(
        (extras or {}).get("principio", "") for extras in recentes.values_list("extras", flat=True)
    )
    opcoes = candidatos(post, mat)
    return min(opcoes, key=lambda p: (usos.get(p, 0), opcoes.index(p)))


def instrucao(post, mat: dict | None = None) -> str:
    """A parte de Cialdini para quem escreve: o principal deste post e a lista."""
    principal = (post.extras or {}).get("principio") or principal_da_vez(post, mat)
    info = PRINCIPIOS[principal]
    lista = "\n".join(
        f"- {chave}: {p['nome']} — use: {p['uso']}. Nunca: {p['nunca']}."
        for chave, p in PRINCIPIOS.items()
    )
    return (
        "PERSUASAO (Cialdini, sempre honesta): o post usa 1 a 3 destes principios. O "
        f"PRINCIPAL deste post e {info['nome'].upper()} ({info['uso']}); os outros, so se "
        "couberem naturalmente. Devolva em 'principios' as chaves dos que usou.\n"
        f"{lista}\n"
    )


def detectados(texto: str, extras: dict, *, isca: bool = False) -> set[str]:
    """O que da para confirmar por algoritmo no post pronto."""
    laminas = extras.get("laminas") or []
    tudo = " ".join([texto] + [f"{x.get('titulo', '')} {x.get('texto', '')}" for x in laminas])
    saida = set()
    if any(x.get("fonte") for x in laminas):
        saida.add("autoridade")
    if isca:
        saida.add("reciprocidade")
    if extras.get("enquete") or extras.get("formato") == "enquete" or _PEDIDO.search(tudo):
        saida.add("compromisso")
    if _NUMERO.search(tudo):
        saida.add("prova_social")
    if _NOS.search(tudo):
        saida.add("unidade")
    return saida


def registrar(post, texto: str, extras: dict, declarados) -> None:
    """Grava em `extras` o principal e os usados (declarados + detectados)."""
    usados = {valido(str(p).strip().lower()) for p in (declarados or [])} - {""}
    usados |= detectados(texto, extras, isca=bool(post.isca_id))
    principal = (post.extras or {}).get("principio") or ""
    if principal:
        extras["principio"] = principal
        usados.add(principal)
    extras["principios"] = sorted(usados)


def quadro(destino) -> list[dict]:
    """Por principio: a medida mediana dos posts publicados com ele x sem ele,
    nesta conta (indicio, nao prova: os posts diferem em tudo o mais)."""
    from apps.social import experimentos, parametros
    from apps.social.models import ConfiguracaoSocial, Post

    config = ConfiguracaoSocial.carregar()
    minimo = parametros.valor("alcance_minimo", config)
    medidos = []
    for post in destino.posts.filter(situacao=Post.Situacao.PUBLICADO).exclude(
        motivo=Post.Motivo.HISTORICO
    ):
        v = experimentos.valor(post, config.metrica, alcance_minimo=minimo)
        if v is not None:
            medidos.append((set((post.extras or {}).get("principios") or []), v, post.sucesso))
    linhas = []
    for chave, info in PRINCIPIOS.items():
        com = [v for usados, v, _s in medidos if chave in usados]
        sem = [v for usados, v, _s in medidos if chave not in usados]
        julgados = [s for usados, _v, s in medidos if chave in usados and s is not None]
        linha = {
            "chave": chave,
            "nome": info["nome"],
            "com": len(com),
            "sem": len(sem),
            "funcionou": round(100 * sum(julgados) / len(julgados)) if julgados else None,
            "diferenca": None,
        }
        if len(com) >= 3 and len(sem) >= 3 and statistics.median(sem) > 0:
            linha["diferenca"] = round(
                100 * (statistics.median(com) - statistics.median(sem)) / statistics.median(sem)
            )
        linhas.append(linha)
    return linhas
