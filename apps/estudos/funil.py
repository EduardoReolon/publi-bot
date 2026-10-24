"""As contas sobre as buscas: tendencia, sazonalidade e o funil ate o cliente.

Tudo algoritmo, a partir do que a DataForSEO devolveu (volume, dois anos de
historico mensal, custo por clique em dolar):

* tendencia: os ultimos 12 meses contra os 12 anteriores;
* sazonalidade: o indice de cada mes do ano (1,0 = um mes medio), pela media
  dos anos que ha no historico; o pico e o mes de indice maior;
* funil: buscas x cliques por busca x contatos por clique x clientes por
  contato, com o custo dos cliques. Os percentuais sao do estudo (a pessoa
  ajusta); o que sai e ordem de grandeza, nao promessa.
"""

from __future__ import annotations

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def tendencia(meses: list) -> float | None:
    """Variacao (%) dos ultimos 12 meses sobre os 12 anteriores."""
    valores = [v for _, _, v in sorted(meses or [])]
    if len(valores) < 24:
        return None
    antes, depois = sum(valores[-24:-12]), sum(valores[-12:])
    return round((depois - antes) / antes * 100, 1) if antes else None


def sazonalidade(meses: list) -> list[float] | None:
    """12 indices (jan a dez); None com menos de 12 meses ou sem buscas."""
    if len(meses or []) < 12:
        return None
    por_mes: dict[int, list[int]] = {}
    for _, mes, valor in meses:
        por_mes.setdefault(int(mes), []).append(valor)
    medias = [sum(por_mes.get(m, [0])) / max(len(por_mes.get(m, [])), 1) for m in range(1, 13)]
    media = sum(medias) / 12
    return [round(v / media, 2) for v in medias] if media else None


def pico(meses: list) -> str:
    indices = sazonalidade(meses)
    if not indices or max(indices) < 1.15:  # menos de 15% acima da media: sem pico
        return ""
    return MESES[indices.index(max(indices))]


def _pct(valor) -> float:
    return float(valor or 0) / 100


def contas_da_palavra(estudo, item: dict) -> dict:
    """O funil de uma palavra, por mes medio, em reais."""
    buscas = float(item.get("volume_area") or 0)
    cliques = buscas * _pct(estudo.ctr)
    cpc = float(item.get("cpc") or 0) * float(estudo.cotacao_do_dolar)
    contatos = cliques * _pct(estudo.conversao)
    clientes = contatos * _pct(estudo.fechamento)
    return {
        "buscas": round(buscas),
        "cliques": round(cliques, 1),
        "cpc_reais": round(cpc, 2),
        "custo": round(cliques * cpc, 2),
        "contatos": round(contatos, 2),
        "clientes": round(clientes, 2),
    }


def funil(estudo, dados: dict[str, dict]) -> dict:
    """O total das palavras da campanha, por mes medio e mes a mes (pela
    sazonalidade de cada palavra)."""
    linhas = []
    por_mes = [0.0] * 12
    for palavra in estudo.campanha or []:
        item = dados.get(palavra)
        if not item:
            continue
        contas = contas_da_palavra(estudo, item)
        linhas.append({"palavra": palavra, **contas})
        indices = sazonalidade(item.get("meses_area")) or [1.0] * 12
        for i in range(12):
            por_mes[i] += contas["clientes"] * indices[i]
    total = {
        chave: round(sum(linha[chave] for linha in linhas), 2)
        for chave in ("buscas", "cliques", "custo", "contatos", "clientes")
    }
    valor = float(estudo.valor_do_cliente or 0)
    total["custo_por_contato"] = (
        round(total["custo"] / total["contatos"], 2) if total["contatos"] else None
    )
    total["custo_por_cliente"] = (
        round(total["custo"] / total["clientes"], 2) if total["clientes"] else None
    )
    total["retorno"] = round(total["clientes"] * valor - total["custo"], 2) if valor else None
    return {
        "linhas": linhas,
        "total": total,
        "por_mes": [{"mes": MESES[i], "clientes": round(por_mes[i], 2)} for i in range(12)],
    }
