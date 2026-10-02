"""O corte da busca pelos rotulos de outra IA.

Julgar a olho onde a lista "deixa de ser relevante" cansa e e impreciso. Aqui a
outra IA so rotula cada trecho — relevante, irrelevante ou lixo — sem ver a
distancia (para julgar o texto, e nao o numero). O corte e conta: o ponto que
melhor separa relevante de irrelevante, somando os rotulos de todas as consultas
ja testadas.

Lixo fica FORA da conta: legenda, DOI ou capa de revista ficam perto de qualquer
consulta do tema (sobra quase so o titulo do documento no vetor), entao aparecem
em qualquer distancia e nao dizem nada sobre onde cortar. O lixo e tratado a
parte: tirado da busca.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

MAXIMO_DE_CARACTERES_DO_TRECHO = 700
PADRAO_DE_ROTULO = re.compile(
    r"\bT(\d{1,3})\**\s*[:=\-\u2013\u2014]\s*\**\s*([RIL])\b", re.IGNORECASE
)


def pedido(consulta: str, linhas: list[dict]) -> str:
    """O texto para colar na outra IA. As linhas vem de `_medir_consulta`."""
    trechos = []
    for numero, linha in enumerate(linhas, start=1):
        texto = " ".join((linha["conteudo"] or "").split())[:MAXIMO_DE_CARACTERES_DO_TRECHO]
        secao = f" | secao: {linha['heading']}" if linha.get("heading") else ""
        trechos.append(f"T{numero} | fonte: {linha['titulo']}{secao}\n{texto}")
    return (
        "Estou calibrando a busca de um sistema que escreve artigos a partir de um "
        "acervo de documentos. Para a CONSULTA abaixo, o sistema achou os trechos "
        "listados. Rotule CADA trecho com uma letra:\n\n"
        "R = relevante: um redator usaria este trecho como evidencia ou informacao "
        "para escrever sobre a consulta (mesmo em outra lingua, mesmo que trate de "
        "so um aspecto do tema).\n"
        "I = irrelevante: texto de verdade, mas de outro assunto, tangente demais, ou "
        "generico a ponto de nao sustentar nada sobre a consulta.\n"
        "L = lixo: nao e conteudo — metadados, capa ou cabecalho de revista, "
        "como citar, autores e afiliacoes, palavras-chave soltas, tabela sem texto "
        "explicativo, legenda, formula sem explicacao.\n\n"
        "Julgue so o texto de cada trecho, um por um. A ordem da lista nao importa, "
        "e nao sugira linha de corte: so os rotulos. Um trecho com um pouco de lixo "
        "mas com conteudo util e R ou I, nao L.\n\n"
        "Responda SO com uma linha por trecho, neste formato, sem comentarios:\n"
        "T1: R\nT2: L\nT3: I\n\n"
        f"CONSULTA: {consulta}\n\n" + "\n\n".join(trechos)
    )


def ler(resposta: str, quantos: int) -> dict[int, str]:
    """{numero do trecho: rotulo} do que veio na resposta (ignora o resto)."""
    rotulos = {}
    for numero, rotulo in PADRAO_DE_ROTULO.findall(resposta or ""):
        n = int(numero)
        if 1 <= n <= quantos:
            rotulos[n] = rotulo.upper()
    return rotulos


def gravar(consulta: str, trechos: list, rotulos: dict[int, str]) -> int:
    """Grava (ou troca) os rotulos. `trechos` sao os SuperChunk anotados com
    `distancia`, na ordem do pedido (None onde o trecho nao existe mais)."""
    from apps.knowledge.models import RotuloDeCalibracao

    gravados = 0
    for numero, trecho in enumerate(trechos, start=1):
        # Trecho que sumiu desde o teste (reindexado, apagado) fica sem rotulo.
        if numero not in rotulos or trecho is None:
            continue
        RotuloDeCalibracao.objects.update_or_create(
            consulta=consulta[:500],
            trecho=trecho,
            defaults={
                "distancia": float(trecho.distancia),
                "rotulo": rotulos[numero],
                "modelo": settings.EMBEDDING_MODEL,
            },
        )
        gravados += 1
    return gravados


@dataclass
class Sugestao:
    corte: float
    precisao: float
    cobertura: float
    relevantes: int
    irrelevantes: int
    lixo: int
    consultas: int


def sugerir_corte() -> Sugestao | None:
    """O corte que melhor separa R de I (maior F1), com os rotulos do modelo em
    uso. Sem pelo menos um R e um I, nao ha o que separar."""
    from apps.knowledge.models import RotuloDeCalibracao

    rotulos = RotuloDeCalibracao.objects.filter(
        modelo=settings.EMBEDDING_MODEL, trecho__is_active=True
    )
    pontos = sorted((r.distancia, r.rotulo == "R") for r in rotulos if r.rotulo in ("R", "I"))
    relevantes = sum(1 for _, r in pontos if r)
    if not relevantes or relevantes == len(pontos):
        return None

    melhor = None
    dentro_r = dentro_i = 0
    for indice, (distancia, relevante) in enumerate(pontos):
        if relevante:
            dentro_r += 1
        else:
            dentro_i += 1
        # Empate de distancia: so corta depois do ultimo ponto igual.
        if indice + 1 < len(pontos) and pontos[indice + 1][0] == distancia:
            continue
        if not dentro_r:
            continue
        precisao = dentro_r / (dentro_r + dentro_i)
        cobertura = dentro_r / relevantes
        f1 = 2 * precisao * cobertura / (precisao + cobertura)
        if melhor is None or f1 > melhor[0]:
            melhor = (f1, distancia, precisao, cobertura)

    _, corte, precisao, cobertura = melhor
    return Sugestao(
        corte=round(corte + 0.0005, 4),
        precisao=precisao,
        cobertura=cobertura,
        relevantes=relevantes,
        irrelevantes=len(pontos) - relevantes,
        lixo=rotulos.filter(rotulo="L").count(),
        consultas=rotulos.values("consulta").distinct().count(),
    )


def lixo_marcado():
    """Trechos ativos que a outra IA marcou como lixo."""
    from apps.knowledge.models import SuperChunk

    return SuperChunk.objects.filter(is_active=True, rotulos__rotulo="L").distinct()
