"""Oportunidades: o que o publico procura e o site ainda nao oferece.

Uma LENTE sobre os mesmos grupos do radar. A nota das pautas premia o que
esta perto do negocio; esta premia o contrario — o que esta perto do PUBLICO
(das dores dele) e longe do que o site ja faz —, e soma duas coisas que a
pauta nao olha:

* **crescimento**: o volume dos ultimos 3 meses contra os MESMOS 3 meses do
  ano anterior (comparar meses vizinhos faria todo tema sazonal parecer
  novidade no pico). O teste de Mann-Kendall sobre as diferencas ano a ano diz
  se a subida e tendencia ou ruido;
* **valor comercial**: o custo por clique do Google Ads. Se empresas pagam caro
  pelo clique de quem busca aquilo, ha dinheiro sendo gasto para atender essa
  demanda. Vem de graca, junto com o volume.

O nome do tema sai do c-TF-IDF (a tecnica do BERTopic): os termos que mais
distinguem o grupo dos outros. Nenhum modelo de linguagem decide ou pontua; o
LLM, quando ha um no ar, so DESCREVE o que o algoritmo achou
(`descrever_oportunidade`).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from itertools import pairwise

import numpy as np
from django.utils import timezone

from apps.radar.agrupamento import _distancia, _escala, _vetor, pontuar, vetor_do_negocio
from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda, Oportunidade, SinalDeDemanda

logger = logging.getLogger("publibot.radar")

PESOS = {
    "demanda": 0.25,
    "crescimento": 0.20,
    "comercial": 0.20,
    "novidade": 0.15,
    "publico": 0.10,
    "diversidade": 0.10,
}
# Abaixo disto o tema esta perto demais do que o site ja faz: e pauta, nao
# oportunidade.
NOVIDADE_MINIMA = 0.4
# Custo por clique (US$) a partir do qual o valor comercial e maximo.
CPC_ALTO = 3.0

# Palavras que nao distinguem tema nenhum. Curta de proposito: o c-TF-IDF ja
# derruba o que aparece em todo grupo; a lista so tira o obvio.
_VAZIAS = set(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela
    para pra com sem sobre entre e ou que qual quais quanto quanta como onde
    quando porque por que se ser sao e esta estao tem ter mais menos muito
    muita meu minha seu sua isso isto esse essa este esta ao aos voce voces
    eu ele ela eles elas nao sim ja ate tambem so
    """.split()
)


# ---------------------------------------------------------------------------
# Serie mensal e crescimento
# ---------------------------------------------------------------------------
def serie_do_grupo(sinais) -> list[tuple[int, int, int]]:
    """Volume mensal somado: todos os sinais, todas as regioes."""
    soma: Counter = Counter()
    for sinal in sinais:
        for metrica in ((sinal.extra or {}).get("metricas") or {}).values():
            for ano, mes, volume in metrica.get("meses") or []:
                soma[(int(ano), int(mes))] += int(volume or 0)
    return [(a, m, v) for (a, m), v in sorted(soma.items())]


def mann_kendall(valores: list[float]) -> float:
    """Estatistica z do teste de tendencia de Mann-Kendall (sem correcao de
    empates, que aqui nao muda a decisao). Positivo = subindo."""
    n = len(valores)
    if n < 4:
        return 0.0
    s = sum(
        (valores[j] > valores[i]) - (valores[j] < valores[i])
        for i in range(n - 1)
        for j in range(i + 1, n)
    )
    variancia = n * (n - 1) * (2 * n + 5) / 18
    if s > 0:
        return (s - 1) / math.sqrt(variancia)
    if s < 0:
        return (s + 1) / math.sqrt(variancia)
    return 0.0


def crescimento(serie: list[tuple[int, int, int]]) -> dict:
    """Ano a ano nos ultimos 3 meses, e se a tendencia e significativa.

    Sem 15 meses de historico nao ha com o que comparar: devolve vazio, e a
    parcela fica neutra em vez de inventar um numero.
    """
    por_mes = {(a, m): v for a, m, v in serie}
    if len(por_mes) < 15:
        return {}
    ultimos = sorted(por_mes)[-3:]
    atual = sum(por_mes[k] for k in ultimos)
    anterior = sum(por_mes.get((a - 1, m), 0) for a, m in ultimos)
    yoy = (atual - anterior) / max(anterior, 10)

    diferencas = [
        por_mes[(a, m)] - por_mes[(a - 1, m)] for a, m in sorted(por_mes) if (a - 1, m) in por_mes
    ]
    z = mann_kendall(diferencas) if diferencas else 0.0
    # Subida consistente das diferencas OU diferencas todas positivas (a serie
    # do ano todo acima da do ano anterior) contam como tendencia.
    consistente = len(diferencas) >= 6 and sum(d > 0 for d in diferencas) >= 0.8 * len(diferencas)
    return {
        "yoy": round(yoy, 3),
        "z": round(z, 2),
        "significativo": bool(yoy > 0 and (z > 1.645 or consistente)),
    }


# ---------------------------------------------------------------------------
# c-TF-IDF: os termos que distinguem cada grupo
# ---------------------------------------------------------------------------
def _termos(texto: str) -> list[str]:
    from apps.radar.locais import normalizar

    palavras = [
        p
        for p in re.findall(r"[a-z0-9]+", normalizar(texto))
        if len(p) > 2 and p not in _VAZIAS and not p.isdigit()
    ]
    return palavras + [f"{a} {b}" for a, b in pairwise(palavras)]


def termos_distintivos(textos_por_grupo: dict, *, quantos: int = 5) -> dict:
    """c-TF-IDF (Grootendorst, 2022): TF dentro do grupo x IDF entre grupos.

    `textos_por_grupo` e {id: [textos]}. O IDF e log(1 + A / f), com A a
    media de termos por grupo e f a frequencia do termo em TODOS os grupos:
    termo que aparece em tudo nao distingue nada.
    """
    contagens = {
        g: Counter(t for texto in textos for t in _termos(texto))
        for g, textos in textos_por_grupo.items()
    }
    total_por_termo: Counter = Counter()
    for contagem in contagens.values():
        total_por_termo.update(contagem)
    media = (sum(sum(c.values()) for c in contagens.values()) / len(contagens)) if contagens else 0
    saida = {}
    for grupo, contagem in contagens.items():
        tamanho = sum(contagem.values()) or 1
        pontos = {
            termo: (n / tamanho) * math.log(1 + media / total_por_termo[termo])
            for termo, n in contagem.items()
        }
        escolhidos: list[str] = []
        for termo, _ in sorted(pontos.items(), key=lambda x: -x[1]):
            # Bigrama que ja contem um unigrama escolhido (ou o contrario) e
            # repeticao, nao outro termo.
            if any(termo in e or e in termo for e in escolhidos):
                continue
            escolhidos.append(termo)
            if len(escolhidos) == quantos:
                break
        saida[grupo] = escolhidos
    return saida


# ---------------------------------------------------------------------------
# Nota
# ---------------------------------------------------------------------------
def _cpc_e_competicao(sinais) -> tuple[float | None, float | None]:
    cpcs, competicoes = [], []
    for sinal in sinais:
        for metrica in ((sinal.extra or {}).get("metricas") or {}).values():
            if metrica.get("cpc") is not None:
                cpcs.append(float(metrica["cpc"]))
            if metrica.get("competicao") is not None:
                competicoes.append(float(metrica["competicao"]))
    return (
        max(cpcs) if cpcs else None,
        (sum(competicoes) / len(competicoes)) if competicoes else None,
    )


def _vetores_das_dores() -> list[np.ndarray]:
    return [_vetor(d) for d in ConfiguracaoDoRadar.carregar().lista_de_dores]


def avaliar(grupo: GrupoDeDemanda, sinais, *, dores: list[np.ndarray], negocio) -> dict | None:
    """As parcelas e a nota, ou None se o grupo nao e oportunidade."""
    from apps.radar.coleta import SO_REFORCAM

    if not any(s.fonte not in SO_REFORCAM for s in sinais):
        return None  # so o que o dono digitou: nao e evidencia de nada
    centroide = np.asarray(grupo.centroide, dtype=np.float32)
    aderencia = _escala(_distancia(centroide, negocio), 0.12, 0.30) if negocio is not None else 0.0
    novidade = 1.0 - aderencia
    if novidade < NOVIDADE_MINIMA:
        return None
    publico = max(_escala(_distancia(centroide, d), 0.12, 0.30) for d in dores) if dores else 0.5

    serie = serie_do_grupo(sinais)
    cresc = crescimento(serie)
    if cresc:
        parcela_cresc = _escala(-cresc["yoy"], -0.5, 0.0) * (1.0 if cresc["significativo"] else 0.6)
    else:
        parcela_cresc = 0.3  # sem historico: neutra, nem premia nem derruba

    cpc, competicao = _cpc_e_competicao(sinais)
    comercial = 0.0
    if cpc is not None:
        comercial += 0.7 * min(1.0, cpc / CPC_ALTO)
    if competicao is not None:
        comercial += 0.3 * min(1.0, competicao / 100)

    parcelas = {
        "demanda": grupo.parcelas.get("demanda", 0.0),
        "crescimento": round(parcela_cresc, 3),
        "comercial": round(comercial, 3),
        "novidade": round(novidade, 3),
        "publico": round(publico, 3),
        "diversidade": grupo.parcelas.get("diversidade", 0.0),
    }
    nota = 100 * sum(PESOS[k] * v for k, v in parcelas.items())
    return {
        "nota": round(nota, 1),
        "parcelas": parcelas,
        "crescimento": {**cresc, "meses": [list(x) for x in serie[-24:]]},
        "cpc": cpc,
    }


def atualizar_oportunidades() -> int:
    """Reavalia todos os grupos vivos. Devolve quantas oportunidades ha.

    A decisao da pessoa (arquivar, virar semente, testar) fica: so a nota e os
    numeros sao atualizados.
    """
    from apps.radar.agrupamento import vetores_do_que_ja_foi_escrito

    grupos = list(
        GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.NOVO).exclude(
            centroide__isnull=True
        )
    )
    if not grupos:
        return 0
    negocio = vetor_do_negocio()
    ja_escrito = vetores_do_que_ja_foi_escrito()
    dores = _vetores_das_dores()
    from apps.radar.coleta import _que_convertem

    convertem = _que_convertem()

    sinais_por_grupo = {
        g.pk: list(g.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO)) for g in grupos
    }
    termos = termos_distintivos({g.pk: [s.texto for s in sinais_por_grupo[g.pk]] for g in grupos})

    vivas = 0
    for grupo in grupos:
        pontuar(grupo, vetor_do_negocio=negocio, ja_escrito=ja_escrito, que_convertem=convertem)
        avaliacao = avaliar(grupo, sinais_por_grupo[grupo.pk], dores=dores, negocio=negocio)
        existente = Oportunidade.objects.filter(grupo=grupo).first()
        if avaliacao is None:
            # Deixou de ser oportunidade (ficou perto do negocio): some da tela,
            # a nao ser que a pessoa ja tenha decidido algo sobre ela.
            if existente and existente.situacao == Oportunidade.Situacao.NOVA:
                existente.delete()
            continue
        oportunidade = existente or Oportunidade(grupo=grupo)
        oportunidade.nota = avaliacao["nota"]
        oportunidade.parcelas = avaliacao["parcelas"]
        oportunidade.crescimento = avaliacao["crescimento"]
        oportunidade.cpc = avaliacao["cpc"]
        oportunidade.termos = termos.get(grupo.pk, [])
        oportunidade.save()
        vivas += 1
    return vivas


# ---------------------------------------------------------------------------
# Descricao por LLM (opcional)
# ---------------------------------------------------------------------------
ESQUEMA_DA_DESCRICAO = {
    "type": "object",
    "properties": {
        "problema": {"type": "string"},
        "servico": {"type": "string"},
        "perguntas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["problema", "servico", "perguntas"],
}


def descrever_oportunidade(oportunidade: Oportunidade) -> None:
    """Pede ao modelo um paragrafo sobre a oportunidade.

    Levanta `PassoAdiado` quando a placa esta ocupada ou fora do ar e
    `SemModeloConfigurado` sem conexao: quem chama decide tentar depois.
    """
    import json

    from apps.content.inference import executar_prompt
    from apps.integrations.models import Site

    sinais = list(
        oportunidade.grupo.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO)[:25]
    )
    linhas = "\n".join(
        f"- [{s.get_fonte_display()}] {s.texto}"
        + (f" ({s.volume} buscas/mes)" if s.volume is not None else "")
        for s in sinais
    )
    cresc = oportunidade.crescimento or {}
    if "yoy" in cresc:
        tendencia = f"{cresc['yoy'] * 100:+.0f}% ano a ano" + (
            " (tendencia consistente)" if cresc.get("significativo") else " (sem tendencia clara)"
        )
    else:
        tendencia = "sem historico suficiente"
    site = Site.objects.first()
    resultado = executar_prompt(
        key="opportunity_brief",
        variaveis={
            "negocio": (getattr(site, "niche", "") or "")
            + "\n"
            + ", ".join(ConfiguracaoDoRadar.carregar().lista_de_sementes[:30]),
            "tema": oportunidade.grupo.rotulo,
            "termos": ", ".join(oportunidade.termos),
            "sinais": linhas,
            "tendencia": tendencia,
            "cpc": f"US$ {oportunidade.cpc:.2f}" if oportunidade.cpc is not None else "sem dado",
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        json_schema=ESQUEMA_DA_DESCRICAO,
    )
    try:
        dados = json.loads(resultado.texto)
    except ValueError:
        logger.warning("Descricao de oportunidade nao veio em JSON: %s", resultado.texto[:200])
        return
    oportunidade.descricao = {
        "problema": str(dados.get("problema", ""))[:1500],
        "servico": str(dados.get("servico", ""))[:1500],
        "perguntas": [str(p)[:300] for p in (dados.get("perguntas") or [])][:5],
    }
    oportunidade.descricao_em = timezone.now()
    oportunidade.save(update_fields=["descricao", "descricao_em"])


def descrever_pendentes(limite: int = 5, nota_minima: float = 40) -> int:
    """As melhores ainda sem descricao. Para no primeiro 'placa ocupada'."""
    from apps.content.inference import SemModeloConfigurado
    from apps.ops.orchestrator import PassoAdiado

    feitas = 0
    pendentes = Oportunidade.objects.filter(
        situacao=Oportunidade.Situacao.NOVA, descricao_em__isnull=True, nota__gte=nota_minima
    ).order_by("-nota")[:limite]
    for oportunidade in pendentes:
        try:
            descrever_oportunidade(oportunidade)
        except (PassoAdiado, SemModeloConfigurado) as exc:
            logger.info("Descricao de oportunidades adiada: %s", exc)
            break
        feitas += 1
    return feitas
