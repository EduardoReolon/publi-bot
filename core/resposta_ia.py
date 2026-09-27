"""Le a resposta que a pessoa colou de outra IA, em blocos com rotulo.

Os prompts de "copiar para outra IA" pedem a resposta num formato fixo:

    TEMA: uma linha
    DORES:
    - item
    - item

Modelo grande obedece quase sempre, mas enfeita: poe o rotulo em negrito
(`**TEMA:**`), como titulo (`## TEMA`), com acento ou em minusculas, e as
vezes cerca tudo com ``` . Aqui se aceita tudo isso. O que vier fora de um
rotulo conhecido e ignorado — inclusive a explicacao que o modelo gosta de
escrever antes ou depois.
"""

from __future__ import annotations

import re
import unicodedata

MARCADOR_DE_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")


def _chave(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.upper().split())


def _como_rotulo(linha: str, conhecidos: dict[str, str]) -> tuple[str, str] | None:
    """(rotulo, resto da linha) se a linha abre um bloco; senao None.

    Abre bloco: "ROTULO: texto", "**ROTULO:** texto", "## Rotulo". Sem os dois
    pontos, so como titulo (#), para uma frase comum que comece com a mesma
    palavra nao ser tomada por rotulo.
    """
    bruta = linha.strip()
    limpa = re.sub(r"[*_]", "", bruta.lstrip("#")).strip()
    if ":" in limpa:
        cabeca, resto = limpa.split(":", 1)
    elif bruta.startswith("#"):
        cabeca, resto = limpa, ""
    else:
        return None
    rotulo = conhecidos.get(_chave(cabeca))
    return (rotulo, resto.strip()) if rotulo else None


# O prompt pede "FIM" no final; o resto fecha o bloco quando o modelo comenta
# a propria resposta depois dela.
FECHAM = {"FIM", "OBSERVACAO", "OBSERVACOES", "NOTA", "NOTAS"}


def ler_blocos(resposta: str, rotulos: list[str]) -> dict[str, str]:
    """{ROTULO: texto do bloco}, so para os rotulos pedidos (sem acento, maiusculo)."""
    conhecidos = {_chave(r): r for r in rotulos}
    blocos: dict[str, list[str]] = {}
    atual = None
    for linha in (resposta or "").splitlines():
        if linha.strip().startswith("```"):
            continue
        cabeca = re.sub(r"[*_#]", "", linha).split(":", 1)[0]
        if _chave(cabeca) in FECHAM:
            atual = None
            continue
        abertura = _como_rotulo(linha, conhecidos)
        if abertura is not None:
            atual, resto = abertura
            blocos[atual] = [resto] if resto else []
            continue
        if atual is not None:
            blocos[atual].append(linha)
    return {r: "\n".join(linhas).strip() for r, linhas in blocos.items()}


def itens(bloco: str) -> list[str]:
    """As linhas de um bloco em lista, sem marcador, negrito nem linha vazia."""
    saida = []
    for linha in (bloco or "").splitlines():
        texto = MARCADOR_DE_ITEM.sub("", linha).strip().strip("*_").strip()
        if texto:
            saida.append(texto)
    return saida
