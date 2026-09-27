"""Sugestao de palavras-semente e de dores do publico.

Tres origens, da que sempre funciona para a opcional:

1. **A pagina do site** (algoritmo, sem LLM). E o KeyBERT (Grootendorst,
   2020): candidatos de 1 a 3 palavras tirados do texto, comparados por
   embedding com o texto inteiro; os mais parecidos com o todo sao os temas
   centrais. O MMR (Carbonell e Goldstein, 1998) troca um pouco de semelhanca
   por diversidade, para as sugestoes nao serem cinco variacoes do mesmo tema.
   Usa o modelo de embedding que ja roda na CPU.
2. **Os temas fortes do radar**: grupo com boa nota, perto do negocio e longe
   de todas as sementes atuais e um tema que o radar achou sozinho — merece
   virar semente (e ser buscado por si).
3. **O modelo de linguagem**, quando ha um no ar: le a pagina e os titulos e
   sugere sementes e, principalmente, DORES — o problema na voz do publico, que
   o texto do site (escrito na voz do negocio) nao contem.

Nada entra sozinho na configuracao: tudo fica como sugestao, e a pessoa aceita
ou recusa. Recusada, nao volta.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter

import numpy as np

from apps.radar.locais import normalizar
from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda, SementeSugerida

logger = logging.getLogger("publibot.radar")

# Palavra que nao pode abrir nem fechar um candidato ("de gestao" nao e tema).
_VAZIAS = set(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela
    pelos pelas para pra com sem sobre entre e ou que qual quais como onde
    quando se ser sao esta estao tem ter mais menos muito muita seu sua seus
    suas nosso nossa nossos nossas voce voces isso isto esse essa este ao aos
    ja ate tambem so todo toda todos todas cada mesmo bem aqui clique saiba
    conheca veja entre contato whatsapp telefone email home inicio menu
    """.split()
)
LAMBDA_DO_MMR = 0.6


# ---------------------------------------------------------------------------
# 1. KeyBERT + MMR sobre a pagina do site
# ---------------------------------------------------------------------------
def candidatos(texto: str, *, maximo: int = 150) -> list[str]:
    """N-gramas de 1 a 3 palavras, sem palavra vazia nas pontas.

    Os mais frequentes, com preferencia a dois e tres palavras: "gestao de
    carteira" diz mais que "gestao".
    """
    contagem: Counter = Counter()
    for frase in re.split(r"[.!?;:\n|•·()\[\]]+", texto):
        palavras = re.findall(r"[A-Za-zÀ-ÿ0-9-]+", frase)
        for tamanho in (1, 2, 3):
            for i in range(len(palavras) - tamanho + 1):
                trecho = palavras[i : i + tamanho]
                pontas = (normalizar(trecho[0]), normalizar(trecho[-1]))
                if pontas[0] in _VAZIAS or pontas[1] in _VAZIAS:
                    continue
                if any(len(p) < 3 and p.lower() not in {"de", "da", "do"} for p in trecho):
                    continue
                if any(p.isdigit() for p in trecho):
                    continue
                contagem[" ".join(trecho).lower()] += 1
    ordenados = sorted(contagem.items(), key=lambda x: (-(x[1] * len(x[0].split())), x[0]))
    return [c for c, _ in ordenados[:maximo]]


def _normalizados(matriz: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(matriz, axis=-1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


def mmr(doc: np.ndarray, cands: np.ndarray, *, quantos: int, lambda_: float) -> list[int]:
    """Maximal Marginal Relevance: relevancia ao documento menos redundancia."""
    doc = _normalizados(doc[None, :])[0]
    cands = _normalizados(cands)
    relevancia = cands @ doc
    escolhidos: list[int] = []
    restantes = list(range(len(cands)))
    while restantes and len(escolhidos) < quantos:
        if escolhidos:
            redundancia = (cands[restantes] @ cands[escolhidos].T).max(axis=1)
        else:
            redundancia = np.zeros(len(restantes))
        pontos = lambda_ * relevancia[restantes] - (1 - lambda_) * redundancia
        melhor = restantes[int(np.argmax(pontos))]
        escolhidos.append(melhor)
        restantes.remove(melhor)
    return escolhidos


def palavras_chave(texto: str, *, quantas: int = 12, excluir: list[str] = ()) -> list[str]:
    from apps.knowledge.embeddings import get_embedding_client

    lista = candidatos(texto)
    ja_existem = {normalizar(e) for e in excluir}
    lista = [c for c in lista if normalizar(c) not in ja_existem]
    if not lista:
        return []
    cliente = get_embedding_client()
    doc = np.asarray(cliente.embed_passage([texto[:4000]])[0], dtype=np.float32)
    cands = np.asarray([cliente.embed_query(c) for c in lista], dtype=np.float32)
    return [lista[i] for i in mmr(doc, cands, quantos=quantas, lambda_=LAMBDA_DO_MMR)]


def _texto_do_site() -> tuple[str, list[str]]:
    from apps.content.models import Article
    from apps.integrations.models import Site, SitePost

    site = Site.objects.first()
    pagina = (site.home_content_text if site else "") or ""
    titulos = list(SitePost.objects.values_list("title", flat=True)[:200]) + list(
        Article.objects.values_list("title", flat=True)[:100]
    )
    return pagina, [t for t in titulos if t]


def _registrar(texto: str, tipo: str, origem: str, **evidencia) -> SementeSugerida | None:
    texto = " ".join(texto.split())[:200]
    if len(texto) < 3:
        return None
    config = ConfiguracaoDoRadar.carregar()
    atuais = (
        config.lista_de_sementes if tipo == SementeSugerida.Tipo.SEMENTE else config.lista_de_dores
    )
    chave = normalizar(texto)
    if chave in {normalizar(a) for a in atuais}:
        return None
    sugestao, criada = SementeSugerida.objects.get_or_create(
        chave=chave,
        tipo=tipo,
        defaults={"texto": texto, "origem": origem, "evidencia": evidencia},
    )
    return sugestao if criada else None


def sugerir_pela_pagina(quantas: int = 12) -> int:
    pagina, titulos = _texto_do_site()
    texto = "\n".join([pagina, *titulos]).strip()
    if not texto:
        return 0
    config = ConfiguracaoDoRadar.carregar()
    novas = 0
    for termo in palavras_chave(texto, quantas=quantas, excluir=config.lista_de_sementes):
        if _registrar(termo, SementeSugerida.Tipo.SEMENTE, SementeSugerida.Origem.PAGINA):
            novas += 1
    return novas


# ---------------------------------------------------------------------------
# 2. Temas fortes do radar, longe das sementes atuais
# ---------------------------------------------------------------------------
NOTA_PARA_SEMENTE = 55
DISTANCIA_DAS_SEMENTES = 0.25


def sugerir_pelo_radar(limite: int = 5) -> int:
    """Grupos fortes, do negocio, que nenhuma semente cobre."""
    from apps.radar.agrupamento import _distancia, _vetor

    config = ConfiguracaoDoRadar.carregar()
    sementes = [_vetor(s) for s in config.lista_de_sementes]
    novas = 0
    grupos = GrupoDeDemanda.objects.exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO).filter(
        nota__gte=NOTA_PARA_SEMENTE
    )
    for grupo in grupos.order_by("-nota")[:50]:
        if grupo.parcelas.get("aderencia", 0) < 0.5 or grupo.centroide is None:
            continue
        centroide = np.asarray(grupo.centroide, dtype=np.float32)
        if sementes and min(_distancia(centroide, s) for s in sementes) < DISTANCIA_DAS_SEMENTES:
            continue
        if _registrar(
            grupo.rotulo,
            SementeSugerida.Tipo.SEMENTE,
            SementeSugerida.Origem.RADAR,
            nota=grupo.nota,
            volume=grupo.volume_total,
        ):
            novas += 1
            if novas >= limite:
                break
    return novas


# ---------------------------------------------------------------------------
# 3. Modelo de linguagem (opcional)
# ---------------------------------------------------------------------------
ESQUEMA = {
    "type": "object",
    "properties": {
        "sementes": {"type": "array", "items": {"type": "string"}},
        "dores": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sementes", "dores"],
}


def sugerir_pelo_modelo() -> int:
    """Levanta `PassoAdiado`/`SemModeloConfigurado` sem placa: quem chama
    decide tentar depois."""
    from apps.content.inference import executar_prompt
    from apps.integrations.models import Site

    pagina, titulos = _texto_do_site()
    site = Site.objects.first()
    nicho = getattr(site, "niche", "") or ""
    if not (pagina or titulos or nicho):
        return 0
    resultado = executar_prompt(
        key="seed_suggestion",
        variaveis={
            "nicho": nicho or "(nao informado)",
            "pagina": pagina[:6000] or "(o site nao devolveu a pagina inicial)",
            "publicados": "\n".join(f"- {t}" for t in titulos[:80]) or "(nenhum)",
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        json_schema=ESQUEMA,
    )
    try:
        dados = json.loads(resultado.texto)
    except ValueError:
        logger.warning("Sugestao de sementes nao veio em JSON: %s", resultado.texto[:200])
        return 0
    novas = 0
    for texto in (dados.get("sementes") or [])[:15]:
        if _registrar(str(texto), SementeSugerida.Tipo.SEMENTE, SementeSugerida.Origem.MODELO):
            novas += 1
    for texto in (dados.get("dores") or [])[:10]:
        if _registrar(str(texto), SementeSugerida.Tipo.DOR, SementeSugerida.Origem.MODELO):
            novas += 1
    return novas


def aceitar(sugestao: SementeSugerida) -> None:
    config = ConfiguracaoDoRadar.carregar()
    if sugestao.tipo == SementeSugerida.Tipo.SEMENTE:
        config.sementes = (config.sementes.rstrip() + "\n" + sugestao.texto).strip()
        config.save(update_fields=["sementes"])
    else:
        config.dores = (config.dores.rstrip() + "\n" + sugestao.texto).strip()
        config.save(update_fields=["dores"])
    sugestao.situacao = SementeSugerida.Situacao.ACEITA
    sugestao.save(update_fields=["situacao"])
