"""Clareza do post: quem nunca leu o artigo entende em 2 segundos?

Posts que so repetem frases do artigo saem confusos: a frase faz sentido
dentro do texto, e fora dele vira abstracao ("quando profissionais sem
vivencia solida no nucleo do seu negocio..."). Tres camadas:

1. **Instrucao para quem escreve** (`instrucao`): uma ideia so, dita com as
   palavras do publico; a capa diz o problema concreto; cada lamina continua a
   anterior; cada lamina tem um VISUAL (a cena que a imagem mostra).
2. **Conferencia por algoritmo** (`avisos`): lamina que copia frase do artigo
   (9+ palavras iguais), titulo longo, texto dificil de ler (indice de
   legibilidade de Flesch adaptado ao portugues), lamina sem visual. Cada aviso
   volta para quem escreve, que tenta de novo.
3. **Revisor** (`revisar`, prompt `social_revisor`): outro passo do modelo le o
   post SEM o artigo, diz em uma frase o que entendeu e da nota de clareza.
   Nota baixa volta para quem escreve, com o que ele nao entendeu.
"""

from __future__ import annotations

import json
import logging
import re

from apps.social import fontes

logger = logging.getLogger("publibot.social")

COPIA_MINIMA = 9  # palavras seguidas iguais ao artigo
TITULO_MAXIMO = 9  # palavras
LEGIBILIDADE_MINIMA = 40  # Flesch (pt): abaixo disto, texto para especialista
NOTA_MINIMA = 7  # do revisor, de 0 a 10

INSTRUCAO = (
    "CLAREZA (o mais importante): quem NUNCA leu o artigo, rolando a tela, tem de entender "
    "o post em 2 segundos. Antes de escrever, escolha UMA ideia so e diga-a em 'ideia' "
    "(uma frase que um adolescente entenderia). Tudo no post serve a essa ideia; o que "
    "nao serve fica de fora, mesmo que esteja no material. Escreva com as palavras do "
    "PUBLICO, nunca copie frase do artigo (so numeros, exatos): troque abstracao por "
    "situacao concreta — em vez de 'falta de vivencia no nucleo do negocio', 'o estagiario "
    "que nunca atendeu um cliente configurando o sistema'. Nada de jargao: o artigo pode "
    "ser tecnico, o post NAO — todo termo que o PUBLICO nao usaria no dia a dia (nome de "
    "ferramenta, sigla, unidade tecnica como 'ms', palavra em ingles) fica de fora ou vira "
    "a consequencia que ele sente ('sandbox' vira 'testar antes, sem mexer no que funciona'). "
    "Na duvida, pergunte-se: o publico descrito acima falaria isso numa conversa?\n"
)
INSTRUCAO_DAS_LAMINAS = (
    "LAMINAS: contam uma historia curta, cada uma continuando a anterior e RESPONDENDO o que "
    "ela deixou no ar: 1) a capa diz o PROBLEMA concreto, como a pessoa o vive, com uma "
    "imagem que ela reconhece (ate 8 palavras, sem rodeio; nada de rotulo generico como "
    "'Tarefa que nao termina'); 2) a SEGUNDA lamina e a segunda chance de prender (o "
    "Instagram a mostra de novo a quem nao arrastou): um fato do material, a consequencia "
    "que doi ou a pergunta que a pessoa ja se fez — nunca so 'Por que isso acontece?'; "
    "3-5) o que fazer, um passo por lamina, concreto e na linguagem do publico, e cada "
    "um explicando a lamina anterior; a ultima, a chamada. "
    "Cada lamina tem 'visual': a CENA que a imagem mostra, concreta e facil de desenhar "
    "(pessoas, objetos, lugar, acao — ex.: 'dono de oficina olhando uma pilha de papeis "
    "no balcao, cansado'), que explica a lamina mesmo sem ler; sem texto na imagem. "
    "{estilo}\n"
)


def instrucao(post, *, laminas: bool) -> str:
    """A instrucao de clareza para quem escreve (com a das laminas, se houver)."""
    from apps.social.identidade import ficha, instrucao_de_estilo

    destino = post.destino
    texto = INSTRUCAO
    if laminas:
        fixos = ficha(destino)
        if fixos:
            texto += (
                "PERSONAGENS E CENARIOS FIXOS DA CONTA (chame pelo NOME no 'visual' quando "
                "couber; nao invente outros personagens fixos):\n" + fixos + "\n"
            )
        texto += INSTRUCAO_DAS_LAMINAS.format(estilo=instrucao_de_estilo(destino, post))
    return texto


# -- Algoritmo -------------------------------------------------------------------------
def _palavras(texto: str) -> list[str]:
    from apps.social.iscas import normalizar

    return normalizar(texto).split()


def copia_do_artigo(trecho: str, artigo_texto: str, minimo: int = COPIA_MINIMA) -> bool:
    """O trecho tem `minimo` palavras seguidas iguais as do artigo?"""
    palavras = _palavras(trecho)
    if len(palavras) < minimo:
        return False
    artigo = " " + " ".join(_palavras(artigo_texto)) + " "
    return any(
        " " + " ".join(palavras[i : i + minimo]) + " " in artigo
        for i in range(len(palavras) - minimo + 1)
    )


_VOGAIS = re.compile(r"[aeiouyáéíóúâêôãõàü]+", re.IGNORECASE)


def _silabas(palavra: str) -> int:
    return max(1, len(_VOGAIS.findall(palavra)))


def legibilidade(texto: str) -> float | None:
    """Indice de Flesch adaptado ao portugues (Martins et al., 1996): 100 e
    muito facil, abaixo de 50 e dificil. None com texto curto demais."""
    frases = [f for f in re.split(r"[.!?…\n]+", texto) if f.strip()]
    palavras = re.findall(r"[A-Za-zÀ-ÿ]+", texto)
    if len(palavras) < 15 or not frases:
        return None
    silabas = sum(_silabas(p) for p in palavras)
    return 248.835 - 1.015 * (len(palavras) / len(frases)) - 84.6 * (silabas / len(palavras))


def avisos(extras: dict, artigo_texto: str, *, laminas: bool) -> list[str]:
    """O que deixa o post confuso, por algoritmo (cada aviso volta para quem
    escreve, que corrige)."""
    saida = []
    itens = extras.get("laminas") or []
    copiadas = [
        str(n)
        for n, lamina in enumerate(itens, start=1)
        if copia_do_artigo(f"{lamina.get('titulo', '')} {lamina.get('texto', '')}", artigo_texto)
    ]
    if extras.get("gancho") and copia_do_artigo(extras["gancho"], artigo_texto):
        copiadas.insert(0, "a primeira linha")
    if copiadas:
        saida.append(
            f"copia frase do artigo ({', '.join(copiadas)}): diga a ideia com as palavras do "
            "publico, concreta"
        )
    longos = [
        str(n)
        for n, lamina in enumerate(itens, start=1)
        if len(lamina.get("titulo", "").split()) > TITULO_MAXIMO
    ]
    if longos:
        saida.append(
            f"titulo longo na(s) lamina(s) {', '.join(longos)}: ate {TITULO_MAXIMO} palavras"
        )
    corrido = ". ".join(
        f"{lamina.get('titulo', '')}. {lamina.get('texto', '')}" for lamina in itens
    ) or extras.get("gancho", "")
    indice = legibilidade(corrido)
    if indice is not None and indice < LEGIBILIDADE_MINIMA:
        saida.append(
            f"texto dificil de ler (indice {indice:.0f} de 100): frases mais curtas e "
            "palavras do dia a dia"
        )
    if laminas and itens:
        sem = [str(n) for n, lamina in enumerate(itens, start=1) if not lamina.get("visual")]
        if sem:
            saida.append(f"lamina(s) sem 'visual' (a cena da imagem): {', '.join(sem)}")
    return saida


# -- Revisor (modelo, sem o artigo) ------------------------------------------------------
ESQUEMA_DO_REVISOR = {
    "type": "object",
    "properties": {
        "entendi": {"type": "string"},
        "nota": {"type": "number"},
        "problemas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entendi", "nota"],
}


def _como_o_leitor_ve(texto: str, extras: dict) -> str:
    partes = [f"Legenda: {texto}"]
    for n, lamina in enumerate(extras.get("laminas") or [], start=1):
        partes.append(
            f"Lamina {n}: {lamina.get('titulo', '')} — {lamina.get('texto', '')}"
            + (f" [imagem: {lamina['visual']}]" if lamina.get("visual") else "")
        )
    return "\n".join(partes)


def revisar(post, texto: str, extras: dict) -> list[str]:
    """O revisor le o post como quem rola a tela, sem o artigo. Devolve os
    problemas se a nota ficar abaixo do minimo (vazio: claro, ou revisor fora)."""
    try:
        bruto = fontes.executar(
            "social_revisor",
            {
                "publico": post.destino.publico or "o publico da conta",
                "post": _como_o_leitor_ve(texto, extras),
                "ideia": extras.get("ideia_central") or "(nao informada)",
            },
            json_schema=ESQUEMA_DO_REVISOR,
        )
        dados = json.loads(re.sub(r"^```\w*\n|```$", "", bruto.strip()).strip())
        nota = float(dados.get("nota") or 0)
    except Exception as exc:  # revisor e complemento: sem ele, segue o algoritmo
        logger.info("Revisor de clareza indisponivel para %s: %s", post.pk, exc)
        return []
    extras["clareza"] = {"nota": nota, "entendi": str(dados.get("entendi") or "")[:300]}
    if nota >= NOTA_MINIMA:
        return []
    problemas = [str(p)[:200] for p in dados.get("problemas") or [] if str(p).strip()][:4]
    return [
        f"um leitor que nao leu o artigo deu nota {nota:.0f}/10 de clareza e entendeu: "
        f"'{extras['clareza']['entendi']}'"
        + (f"; o que confundiu: {'; '.join(problemas)}" if problemas else "")
    ]


PROMPT_DO_REVISOR = {
    "descricao": "Le o post como quem rola a tela, sem o artigo, e da nota de clareza.",
    "variaveis": ["publico", "post", "ideia"],
    "temperatura": 0.2,
    "sistema": (
        "Voce e uma pessoa do publico abaixo rolando o Instagram rapido. Voce NUNCA leu o "
        "artigo de onde o post saiu. Leia o post como ele aparece (legenda, laminas e a "
        "descricao da imagem de cada uma) e diga, com franqueza:\n"
        "- 'entendi': em UMA frase, o que o post quer dizer, do jeito que voce entendeu;\n"
        "- 'nota': de 0 a 10, o quanto da para entender em 2 segundos, sem esforco (10 = "
        "bati o olho e entendi; 5 = precisei reler; 0 = nao entendi);\n"
        "- 'problemas': o que confundiu (palavra abstrata, frase longa, lamina que nao "
        "conversa com a anterior, imagem que nao ajuda), ate 4 itens curtos.\n"
        "Seja exigente: post que so faz sentido para quem leu o artigo vale no maximo 5. "
        "Termo que VOCE, como esse publico, nao usaria numa conversa (nome tecnico, sigla, "
        "unidade como 'ms', palavra em ingles) e um problema: cite-o, e com dois ou mais a "
        "nota e no maximo 5. Se a segunda lamina nao te da um motivo para arrastar (so "
        "repete a capa ou faz uma pergunta vaga), e um problema. Se uma lamina nao responde "
        "o que a anterior deixou no ar, e um problema.\n\n"
        'Responda SOMENTE com JSON: {"entendi": "...", "nota": 0, "problemas": ["..."]}'
    ),
    "usuario": (
        "Publico: {publico}\n"
        "A ideia que o autor queria passar (para comparar com o que voce entendeu): {ideia}\n\n"
        "O post:\n{post}"
    ),
}
