"""O material do artigo que vai para o modelo escrever o post — escolhido por
algoritmo, nao pela IA.

A "empatia" do post nao e pedida no vazio: o PubliBot entrega ao modelo as
frases do artigo que mais se parecem com as DORES do negocio e com o PUBLICO
da conta ("e o meu caso"), e as frases com numero ("nossa, que incrivel"). O
modelo so reescreve o que esta aqui, no formato da rede; a conferencia depois
confere que cada numero do post esta no artigo.
"""

from __future__ import annotations

import logging
import re

import numpy as np

from apps.social import fontes

logger = logging.getLogger("publibot.social")

_NUMERO = re.compile(r"\d")
POR_LISTA = 5
TAMANHO_DA_FRASE = 320


def _curta(frase: str) -> str:
    return frase if len(frase) <= TAMANHO_DA_FRASE else frase[: TAMANHO_DA_FRASE - 1] + "…"


def _normalizados(vetores) -> np.ndarray:
    v = np.asarray(vetores, dtype=float)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def montar(artigo: fontes.ArtigoParaRedes, *, publico: str, negocio: dict) -> dict:
    """{"identificacao": [...], "achados": [...], "dores": [...], "secoes": [...]}"""
    frases = artigo.frases[:200]
    dores = [d for d in negocio.get("dores", []) if d][:15]
    secoes = [titulo for titulo, _texto in artigo.secoes]
    com_numero = [f for f in frases if _NUMERO.search(f)]
    if not frases:
        return {"identificacao": [], "achados": [], "dores": dores[:3], "secoes": secoes}

    try:
        vf = _normalizados(fontes.vetores(frases))
        alvos = [a for a in [*dores, publico] if a]
        va = _normalizados(fontes.vetores(alvos, consulta=True)) if alvos else None
        vt = _normalizados(fontes.vetores([f"{artigo.titulo}. {artigo.resumo}"], consulta=True))
    except Exception as exc:  # sem vetores: o comeco de cada secao e as frases com numero
        logger.info("Material do post sem vetores: %s", exc)
        primeiras = []
        for _titulo, texto in artigo.secoes:
            primeira = next((f for f in frases if f and f in texto), "")
            if primeira:
                primeiras.append(primeira)
        return {
            "identificacao": [_curta(f) for f in primeiras[:POR_LISTA]],
            "achados": [_curta(f) for f in com_numero[:POR_LISTA]],
            "dores": dores[:3],
            "secoes": secoes,
        }

    # "E o meu caso": a frase mais parecida com alguma dor ou com o publico.
    if va is not None:
        perto = (vf @ va.T).max(axis=1)
        identificacao = [frases[i] for i in np.argsort(-perto)[:POR_LISTA]]
        # As dores que o artigo toca (as mais parecidas com o titulo e o resumo).
        dores_do_artigo = (
            [dores[i] for i in np.argsort(-(va[: len(dores)] @ vt[0]))[:3]] if dores else []
        )
    else:
        identificacao, dores_do_artigo = frases[:POR_LISTA], []

    # "Que incrivel": frases com numero, as mais centrais para o tema primeiro.
    indices = [i for i, f in enumerate(frases) if _NUMERO.search(f)]
    achados = [frases[i] for i in sorted(indices, key=lambda i: -float(vf[i] @ vt[0]))[:POR_LISTA]]

    return {
        "identificacao": [_curta(f) for f in identificacao],
        "achados": [_curta(f) for f in achados],
        "dores": dores_do_artigo,
        "secoes": secoes,
    }


_NUMERO_NO_TEXTO = re.compile(r"\d[\d.,]*\d|\d")


def _formas(numero: str) -> set[str]:
    """25,9 · 25.9 · 1.234 · 1234: o mesmo numero escrito de jeitos diferentes."""
    limpo = numero.strip(".,")
    formas = {limpo, limpo.replace(".", ""), limpo.replace(",", "."), limpo.replace(".", ",")}
    inteiro = limpo.replace(".", "")
    if inteiro.isdigit() and len(inteiro) > 3:
        formas.add(f"{int(inteiro):,}".replace(",", "."))  # 2300 -> 2.300
    return formas


def numeros_fora_do_artigo(texto: str, artigo_texto: str) -> list[str]:
    """Numeros do post que o artigo nao tem (de 2 algarismos para cima: "3
    sinais" nao e dado). Um numero inventado e o erro mais grave de um post."""
    fora = []
    for numero in _NUMERO_NO_TEXTO.findall(texto or ""):
        if len(re.sub(r"\D", "", numero)) < 2:
            continue
        if not any(forma in artigo_texto for forma in _formas(numero)):
            fora.append(numero.strip(".,"))
    return list(dict.fromkeys(fora))
