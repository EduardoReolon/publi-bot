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


# O achado com numero vai INTEIRO e com o contexto: a secao onde esta e a frase
# anterior. Sozinho, "chega a 40%" nao diz 40% de que, de quem, em que situacao,
# e o post usava o numero em outro sentido.
TAMANHO_DO_ACHADO = 600
TAMANHO_DO_CONTEXTO = 220


def _contextos(artigo: fontes.ArtigoParaRedes) -> dict[str, tuple[str, str]]:
    """{frase: (secao, frase anterior)}."""
    saida: dict[str, tuple[str, str]] = {}
    for titulo, texto in artigo.secoes:
        frases = fontes.dividir_em_frases(texto, minimo=1)
        for i, frase in enumerate(frases):
            saida.setdefault(frase, (titulo, frases[i - 1] if i else ""))
    return saida


def _com_contexto(frases: list[str], artigo: fontes.ArtigoParaRedes) -> list[str]:
    contextos = _contextos(artigo)
    saida = []
    for frase in dict.fromkeys(frases):
        texto = frase if len(frase) <= TAMANHO_DO_ACHADO else frase[: TAMANHO_DO_ACHADO - 1] + "…"
        secao, anterior = contextos.get(frase, ("", ""))
        partes = [f'secao "{secao}"'] if secao else []
        if anterior:
            antes = (
                anterior
                if len(anterior) <= TAMANHO_DO_CONTEXTO
                else "…" + anterior[-(TAMANHO_DO_CONTEXTO - 1) :]
            )
            partes.append(f'logo antes: "{antes}"')
        saida.append(f"{texto} [{'; '.join(partes)}]" if partes else texto)
    return saida


def _normalizados(vetores) -> np.ndarray:
    v = np.asarray(vetores, dtype=float)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def montar(
    artigo: fontes.ArtigoParaRedes, *, publico: str, negocio: dict, sem_vetores: bool = False
) -> dict:
    """{"identificacao": [...], "achados": [...], "dores": [...], "secoes": [...]}

    `sem_vetores`: o caminho rapido (dentro de uma requisicao da tela): o comeco
    de cada secao e as frases com numero, sem vetorizar o artigo."""
    frases = artigo.frases[:200]
    dores = [d for d in negocio.get("dores", []) if d][:15]
    secoes = [titulo for titulo, _texto in artigo.secoes]
    com_numero = [f for f in frases if _NUMERO.search(f)]
    if not frases:
        return {"identificacao": [], "achados": [], "dores": dores[:3], "secoes": secoes}

    try:
        if sem_vetores:
            raise RuntimeError("caminho rapido, pedido pela tela")
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
            "achados": _com_contexto(com_numero[:POR_LISTA], artigo),
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
        "achados": _com_contexto(achados, artigo),
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
