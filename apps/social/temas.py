"""Temas: o que atravessa varios artigos, e quanto o publico se interessa.

Cada artigo, sozinho, tem um angulo. Um carrossel do Instagram funciona
melhor com UMA ideia forte — e as ideias mais fortes costumam aparecer em mais
de um artigo ("cansaco sem causa" aparece no artigo de anemia, no de sono e
no de tireoide). Aqui, sem modelo de linguagem:

1. as frases de todos os artigos no ar viram vetores;
2. frases muito parecidas, de artigos DIFERENTES, formam um tema. "Muito
   parecida" e relativo ao proprio acervo (o vizinho mais proximo de outro
   artigo, entre os 40% mais proximos), e nao um numero fixo: o modelo de
   vetores, o assunto e o tamanho do acervo mudam a escala;
3. o titulo do tema e a frase mais central dele;
4. a nota soma sinais que NAO dependem de seguidores — o que o PubliBot ja
   sabe do publico antes de ele existir na rede:
   * perto das DORES do publico (Negocio);
   * perto do que se BUSCA no Google (grupos de demanda do Radar, com volume);
   * os artigos do tema trazem CONVERSOES no site;
   * AMPLITUDE: em quantos artigos aparece;
   * perto do que ENGAJA NO NICHO (posts de outras contas nas hashtags de
     referencia, com curtidas e comentarios).

Cada parcela vira posicao relativa entre os temas (0 a 1), e a nota e a media
ponderada pelos pesos ajustaveis (apps/social/parametros.py). Parcela sem
dado nenhum (sem Radar, sem referencias) fica de fora, em vez de puxar todos
para baixo.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from django.utils import timezone

from apps.social import fontes, parametros
from apps.social.models import ReferenciaDoNicho, Tema

logger = logging.getLogger("publibot.social")

ARTIGOS = 60
FRASES_POR_ARTIGO = 40
# 0,6: as 40% de frases com o vizinho (de outro artigo) mais proximo formam temas.
QUANTIL_DE_PARECIDO = 0.6
MINIMO_DE_ARTIGOS = 2
MAXIMO_DE_TEMAS = 30


def _norm(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    if v.ndim == 1:
        return v / (np.linalg.norm(v) + 1e-9)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def agrupar(frases: list[dict], vetores: np.ndarray) -> list[list[int]]:
    """Grupos de indices de frases parecidas, de artigos diferentes.

    Lider simples: cada frase entra no grupo de centro mais parecido, se
    passar do limiar; senao, abre um grupo. Ordem: as frases mais "centrais"
    primeiro (as que mais se parecem com outras), para os centros nascerem bons.
    """
    n = len(frases)
    if n < 2:
        return []
    sim = vetores @ vetores.T
    np.fill_diagonal(sim, -1)
    # O limiar sai do proprio acervo: para cada frase, a mais parecida de OUTRO
    # artigo; "parecido o bastante" e estar entre os pares mais proximos dessas.
    artigos = np.asarray([f["artigo_id"] for f in frases])
    outro = artigos[:, None] != artigos[None, :]
    vizinhos = np.where(outro, sim, -1).max(axis=1)
    vizinhos = vizinhos[vizinhos > -1]
    if not len(vizinhos):
        return []
    limiar = float(np.quantile(vizinhos, QUANTIL_DE_PARECIDO))
    centralidade = (sim > limiar).sum(axis=1)
    grupos: list[list[int]] = []
    centros: list[np.ndarray] = []
    for i in np.argsort(-centralidade):
        if centros:
            parecidos = np.asarray([c @ vetores[i] for c in centros])
            melhor = int(np.argmax(parecidos))
            if parecidos[melhor] >= limiar:
                grupos[melhor].append(int(i))
                centros[melhor] = _norm(vetores[grupos[melhor]].mean(axis=0))
                continue
        grupos.append([int(i)])
        centros.append(vetores[i])
    return [g for g in grupos if len({frases[i]["artigo_id"] for i in g}) >= MINIMO_DE_ARTIGOS]


def _posicao(valores: list[float]) -> list[float] | None:
    """Posicao relativa (0 a 1) de cada valor; None se nao ha dado nenhum."""
    if not valores or max(valores) <= 0:
        return None
    if len(valores) == 1:
        return [1.0]
    ordem = np.argsort(np.argsort(valores))
    return [float(o) / (len(valores) - 1) for o in ordem]


def _proximidade_ponderada(centro, vetores: np.ndarray, pesos: list[float]) -> float:
    """Soma dos pesos (volume, engajamento) dos itens perto do centro, cada um
    pesado pelo quanto esta acima da proximidade tipica."""
    if not len(pesos):
        return 0.0
    sims = vetores @ centro
    base = float(np.median(sims))
    teto = float(sims.max())
    if teto <= base:
        return 0.0
    fatores = np.clip((sims - base) / (teto - base + 1e-9), 0, 1) ** 2
    return float(sum(f * p for f, p in zip(fatores, pesos, strict=True)))


def recalcular() -> int:
    """Refaz os temas a partir dos artigos no ar. Devolve quantos temas ativos."""
    frases: list[dict] = []
    titulos: dict[str, str] = {}
    conversoes: dict[str, int] = {}
    for artigo_id in fontes.artigos_no_ar()[:ARTIGOS]:
        artigo = fontes.artigo(artigo_id)
        if artigo is None:
            continue
        titulos[artigo.id] = artigo.titulo
        conversoes[artigo.id] = fontes.sinais(artigo)["conversoes"]
        for frase in artigo.frases[:FRASES_POR_ARTIGO]:
            if len(frase.split()) >= 8:
                frases.append(
                    {"artigo_id": artigo.id, "artigo_titulo": artigo.titulo, "frase": frase}
                )
    if len({f["artigo_id"] for f in frases}) < MINIMO_DE_ARTIGOS:
        Tema.objects.filter(ativo=True).update(ativo=False)
        return 0
    vetores = _norm(fontes.vetores([f["frase"] for f in frases]))
    grupos = agrupar(frases, vetores)

    negocio = fontes.negocio()
    dores = negocio.get("dores") or []
    vetores_das_dores = _norm(fontes.vetores(dores, consulta=True)) if dores else None
    demanda = fontes.demanda()
    vetores_da_demanda = _norm([g["vetor"] for g in demanda]) if demanda else None
    referencias = list(ReferenciaDoNicho.objects.exclude(legenda=""))
    vetores_das_referencias = (
        _norm(fontes.vetores([r.legenda[:500] for r in referencias])) if referencias else None
    )

    candidatos = []
    for grupo in grupos:
        centro = _norm(vetores[grupo].mean(axis=0))
        titulo_i = max(grupo, key=lambda i: float(vetores[i] @ centro))
        artigos = list(dict.fromkeys(frases[i]["artigo_id"] for i in grupo))
        principal = max(artigos, key=lambda a: sum(frases[i]["artigo_id"] == a for i in grupo))
        sinais = {"explicacao": [f"aparece em {len(artigos)} artigos"]}
        sinais["amplitude"] = len(artigos)
        sinais["conversoes"] = sum(conversoes.get(a, 0) for a in artigos)
        if sinais["conversoes"]:
            sinais["explicacao"].append(f"{sinais['conversoes']} conversao(oes) nos artigos")
        sinais["dores"] = 0.0
        if vetores_das_dores is not None:
            perto = vetores_das_dores @ centro
            sinais["dores"] = float(perto.max())
            sinais["dor"] = dores[int(np.argmax(perto))]
            sinais["explicacao"].append(f'perto da dor "{sinais["dor"]}"')
        sinais["demanda"] = 0.0
        if vetores_da_demanda is not None:
            sinais["demanda"] = _proximidade_ponderada(
                centro, vetores_da_demanda, [math.log1p(g["volume"]) for g in demanda]
            )
            perto = int(np.argmax(vetores_da_demanda @ centro))
            sinais["busca"] = demanda[perto]["rotulo"]
            sinais["explicacao"].append(
                f'buscado: "{demanda[perto]["rotulo"]}" ({demanda[perto]["volume"]}/mes)'
            )
        sinais["referencias"] = 0.0
        if vetores_das_referencias is not None:
            sinais["referencias"] = _proximidade_ponderada(
                centro, vetores_das_referencias, [math.log1p(r.engajamento) for r in referencias]
            )
        candidatos.append(
            {
                "titulo": frases[titulo_i]["frase"][:400],
                "frases": [frases[i] for i in grupo][:12],
                "artigos": artigos,
                "principal": principal,
                "centro": centro,
                "sinais": sinais,
            }
        )

    pesos = {
        "dores": parametros.valor("peso_dores"),
        "demanda": parametros.valor("peso_demanda"),
        "conversoes": parametros.valor("peso_conversoes"),
        "amplitude": parametros.valor("peso_amplitude"),
        "referencias": parametros.valor("peso_referencias"),
    }
    posicoes = {chave: _posicao([c["sinais"][chave] for c in candidatos]) for chave in pesos}
    usados = {k: p for k, p in pesos.items() if posicoes[k] is not None and p > 0}
    total = sum(usados.values()) or 1.0
    for n, candidato in enumerate(candidatos):
        candidato["nota"] = sum(p * posicoes[k][n] for k, p in usados.items()) / total
        candidato["sinais"]["parcelas"] = {k: round(posicoes[k][n], 2) for k in usados}
    candidatos.sort(key=lambda c: -c["nota"])

    agora = timezone.now()
    Tema.objects.filter(ativo=True).update(ativo=False)
    # Temas velhos sem post nao servem para nada; os com post ficam (historico).
    Tema.objects.filter(ativo=False, posts__isnull=True).delete()
    for candidato in candidatos[:MAXIMO_DE_TEMAS]:
        Tema.objects.create(
            titulo=candidato["titulo"],
            frases=candidato["frases"],
            artigos=candidato["artigos"],
            artigo_principal=candidato["principal"],
            nota=round(candidato["nota"], 4),
            sinais=candidato["sinais"],
            centro=[round(float(x), 5) for x in candidato["centro"]],
            atualizado_em=agora,
        )
    return min(len(candidatos), MAXIMO_DE_TEMAS)


def material_do_tema(tema: Tema) -> dict:
    """O material do post de um tema: as frases dele (identificacao), as com
    numero (achados), a dor mais perto e os artigos de onde vieram."""
    import re

    frases = [f["frase"] for f in tema.frases]
    return {
        "identificacao": frases[:5],
        "achados": [f for f in frases if re.search(r"\d", f)][:5],
        "dores": [tema.sinais["dor"]] if tema.sinais.get("dor") else [],
        "secoes": list(dict.fromkeys(f["artigo_titulo"] for f in tema.frases)),
    }
