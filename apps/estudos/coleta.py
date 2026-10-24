"""A busca de uma rodada na DataForSEO e as contas da area.

Dois modos, escolhidos a cada rodada:

* **estimativa** (barato): o volume de cada palavra no ESTADO (um pedido por
  estado da area, ate 1000 palavras cada) e, para cada cidade, a fatia dela na
  populacao do estado. A soma das cidades da area e o volume estimado da area.
  Supoe que a cidade busca na proporcao da populacao: serve para explorar e
  comparar palavras, nao para fechar orcamento.
* **real**: um pedido por cidade escolhida, com o volume que o Google mede
  nela. Cidade pequena volta muitas vezes sem volume (o Google so informa
  acima de um minimo): o PubliBot mostra "sem dado", e nao zero.

As ideias de palavras (a partir das sementes ou do site) saem no estado da
cidade do centro, ja com volume, e entram na estimativa das outras cidades.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.estudos import municipios
from apps.estudos.models import Estudo, Rodada

logger = logging.getLogger("publibot.estudos")

MAXIMO_DE_PALAVRAS = 1000


def area(estudo: Estudo) -> list[dict]:
    """As cidades do raio: municipio, distancia, populacao e se esta na area."""
    if not estudo.cidade:
        return []
    lista = municipios.no_raio(estudo.cidade, estudo.raio_km)
    pops = municipios.populacoes(m.codigo for m, _ in lista)
    fora = {int(c) for c in estudo.fora_da_area or []}
    return [
        {
            "municipio": m,
            "codigo": m.codigo,
            "distancia": round(d, 1),
            "populacao": pops.get(m.codigo),
            "dentro": m.codigo not in fora,
        }
        for m, d in lista
    ]


def cidades_da_area(estudo: Estudo) -> list[municipios.Municipio]:
    return [c["municipio"] for c in area(estudo) if c["dentro"]]


def custo_estimado(rodada: Rodada) -> Decimal:
    """O que a rodada deve custar, antes de buscar (para a tela confirmar)."""
    from apps.radar import custos

    estudo = rodada.estudo
    ufs = {m.uf for m in cidades_da_area(estudo)} or {0}
    volume = custos.ESTIMATIVAS[("dataforseo", "volume")]
    total = volume * len(ufs) if rodada.palavras or rodada.sementes or rodada.site else Decimal(0)
    if rodada.sementes or rodada.site:
        total += custos.ESTIMATIVAS[("dataforseo", "ideias")]
    if rodada.modo == Rodada.Modo.REAL:
        total += volume * len(rodada.cidades or [])
    return total


def _escalar(meses: list, fator: float) -> list:
    return [[a, m, round(v * fator)] for a, m, v in meses or []]


def _somar_meses(base: list, mais: list) -> list:
    soma = {(a, m): v for a, m, v in base}
    for a, m, v in mais:
        soma[(a, m)] = soma.get((a, m), 0) + v
    return [[a, m, v] for (a, m), v in sorted(soma.items())]


def executar(rodada: Rodada) -> Rodada:
    """Busca o que a rodada pede e grava o resultado (ou o erro)."""
    from apps.radar import provedores
    from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas

    estudo = rodada.estudo
    config, contas = ConfiguracaoDoRadar.carregar(), ContasExternas.carregar()
    inicio = timezone.now()
    finalidade = ChamadaExterna.Finalidade.ESTUDO
    cidades = cidades_da_area(estudo)
    if not cidades:
        raise ValueError("o estudo ainda nao tem cidade do centro.")
    centro = municipios.todos().get(estudo.cidade)
    ufs = sorted({m.uf for m in cidades}, key=lambda uf: uf != centro.uf)  # o do centro antes
    avisos = municipios.carregar_populacao(set(ufs))
    pops = municipios.populacoes(m.codigo for m in cidades)

    resultado: dict[str, dict] = {}
    palavras = list(dict.fromkeys(rodada.palavras or []))
    for uf in ufs:
        local = municipios.local_do_estado(uf)
        if local is None:
            avisos.append(f"o Google nao tem o estado {municipios.SIGLAS.get(uf, uf)} na lista")
            continue
        metricas: dict[str, dict] = {}
        if uf == centro.uf and (rodada.sementes or rodada.site):
            for palavra, m in provedores.ideias_dataforseo(
                sementes=rodada.sementes,
                site=rodada.site,
                config=config,
                contas=contas,
                finalidade=finalidade,
                local=local,
            ).items():
                metricas[palavra] = {**m, "origem": "ideia"}
            # As ideias entram na lista medida nos outros estados da area.
            palavras = list(dict.fromkeys([*palavras, *metricas]))[:MAXIMO_DE_PALAVRAS]
        faltam = [p for p in palavras if provedores.palavra_para_volume(p) not in metricas]
        if faltam:
            for palavra, m in provedores.metricas_dataforseo(
                faltam, config=config, contas=contas, finalidade=finalidade, local=local
            ).items():
                metricas[palavra] = {**m, "origem": "lista"}
        populacao_do_estado = municipios.populacao_do_estado(uf) or 0
        da_uf = [m for m in cidades if m.uf == uf]
        if not populacao_do_estado or not all(pops.get(m.codigo) for m in da_uf):
            avisos.append(
                f"sem a populacao de {municipios.SIGLAS.get(uf, uf)}: a estimativa dessas "
                "cidades ficou de fora"
            )
            continue
        for palavra, m in metricas.items():
            item = resultado.setdefault(
                palavra,
                {
                    "volume_estado": {},
                    "volume_area": 0,
                    "cidades": {},
                    "reais": {},
                    "meses_area": [],
                    "cpc": m.get("cpc"),
                    "competicao": m.get("competicao"),
                    "origem": m.get("origem", "lista"),
                },
            )
            if uf == centro.uf or item["cpc"] is None:
                item["cpc"], item["competicao"] = m.get("cpc"), m.get("competicao")
            volume = m.get("volume")
            item["volume_estado"][str(uf)] = volume
            if volume is None:
                continue
            fatia = 0.0
            for cidade in da_uf:
                parte = pops[cidade.codigo] / populacao_do_estado
                item["cidades"][str(cidade.codigo)] = round(volume * parte, 1)
                fatia += parte
            item["volume_area"] = round(item["volume_area"] + volume * fatia, 1)
            item["meses_area"] = _somar_meses(item["meses_area"], _escalar(m.get("meses"), fatia))

    if rodada.modo == Rodada.Modo.REAL and resultado:
        alvo = list(resultado)[:MAXIMO_DE_PALAVRAS]
        for codigo in rodada.cidades or []:
            cidade = municipios.todos().get(int(codigo))
            local = municipios.local_da_cidade(cidade) if cidade else None
            if local is None:
                avisos.append(f"o Google nao mede {cidade.rotulo if cidade else codigo} sozinha")
                continue
            reais = provedores.metricas_dataforseo(
                alvo, config=config, contas=contas, finalidade=finalidade, local=local
            )
            for palavra, item in resultado.items():
                medida = reais.get(palavra) or {}
                item["reais"][str(codigo)] = medida.get("volume")  # None = abaixo do minimo

    rodada.resultado = resultado
    rodada.avisos = avisos
    rodada.custo_usd = ChamadaExterna.objects.filter(
        finalidade=finalidade, criado_em__gte=inicio
    ).aggregate(t=Sum("custo_usd"))["t"] or Decimal(0)
    rodada.situacao = Rodada.Situacao.PRONTA
    rodada.erro = ""
    rodada.feita_em = timezone.now()
    rodada.save()
    return rodada


def consolidado(estudo: Estudo) -> dict[str, dict]:
    """O ultimo dado de cada palavra em todas as rodadas (a mais nova ganha; o
    valor real de uma cidade fica mesmo que uma rodada depois so estime)."""
    saida: dict[str, dict] = {}
    for rodada in estudo.rodadas.filter(situacao=Rodada.Situacao.PRONTA).order_by("numero"):
        for palavra, item in (rodada.resultado or {}).items():
            antes = saida.get(palavra, {})
            reais = {**antes.get("reais", {}), **item.get("reais", {})}
            saida[palavra] = {**item, "reais": reais, "rodada": rodada.numero}
    return saida
