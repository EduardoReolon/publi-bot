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
    fila = "_fila" if rodada.fila else ""
    volume = custos.ESTIMATIVAS[("dataforseo", f"volume{fila}")]
    total = volume * len(ufs) if rodada.palavras or rodada.sementes or rodada.site else Decimal(0)
    ideias = custos.ESTIMATIVAS[("dataforseo", f"ideias{fila}")]
    total += ideias * (bool(rodada.sementes) + bool(rodada.site))
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


def _contexto(rodada: Rodada) -> dict:
    """A area da rodada: as cidades, o centro, os estados (o do centro antes) e
    a populacao (lida do IBGE se ainda nao estiver guardada)."""
    estudo = rodada.estudo
    cidades = cidades_da_area(estudo)
    if not cidades:
        raise ValueError("o estudo ainda nao tem cidade do centro.")
    centro = municipios.todos().get(estudo.cidade)
    ufs = sorted({m.uf for m in cidades}, key=lambda uf: uf != centro.uf)
    avisos = municipios.carregar_populacao(set(ufs))
    return {
        "cidades": cidades,
        "centro": centro,
        "ufs": ufs,
        "pops": municipios.populacoes(m.codigo for m in cidades),
        "avisos": avisos,
    }


def _pedidos_de_ideias(rodada: Rodada) -> list[dict]:
    """Sementes e site sao dois pedidos de ideias (um nao exclui o outro)."""
    return [p for p in ({"sementes": rodada.sementes}, {"site": rodada.site}) if any(p.values())]


def _faltam(palavras: list[str], ja: dict) -> list[str]:
    from apps.radar.provedores import palavra_para_volume

    return [p for p in palavras if palavra_para_volume(p) not in ja]


def _montar(rodada: Rodada, ctx: dict, por_uf: dict, reais: dict, inicio) -> Rodada:
    """O resultado a partir do que foi medido (ao vivo ou na fila):
    `por_uf` = {uf: {palavra: metricas com "origem"}}; `reais` = {codigo do
    IBGE: {palavra: metricas}} das cidades medidas uma a uma."""
    from apps.radar.models import ChamadaExterna

    avisos, pops, centro = ctx["avisos"], ctx["pops"], ctx["centro"]
    resultado: dict[str, dict] = {}
    for uf in ctx["ufs"]:
        if uf not in por_uf:
            continue
        populacao_do_estado = municipios.populacao_do_estado(uf) or 0
        da_uf = [m for m in ctx["cidades"] if m.uf == uf]
        if not populacao_do_estado or not all(pops.get(m.codigo) for m in da_uf):
            avisos.append(
                f"sem a populacao de {municipios.SIGLAS.get(uf, uf)}: a estimativa dessas "
                "cidades ficou de fora"
            )
            continue
        for palavra, m in por_uf[uf].items():
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
    for codigo, medidas in reais.items():
        for palavra, item in resultado.items():
            # None = abaixo do minimo que o Google informa
            item["reais"][str(codigo)] = (medidas.get(palavra) or {}).get("volume")

    rodada.resultado = resultado
    rodada.avisos = avisos
    rodada.custo_usd = ChamadaExterna.objects.filter(
        finalidade=ChamadaExterna.Finalidade.ESTUDO, criado_em__gte=inicio
    ).aggregate(t=Sum("custo_usd"))["t"] or Decimal(0)
    rodada.situacao = Rodada.Situacao.PRONTA
    rodada.erro = ""
    rodada.etapa = {}
    rodada.feita_em = timezone.now()
    rodada.save()
    return rodada


def _locais(rodada: Rodada, ctx: dict) -> tuple[dict, dict]:
    """O local do Google de cada estado e de cada cidade a medir (avisa o que
    o Google nao tem)."""
    estados = {}
    for uf in ctx["ufs"]:
        local = municipios.local_do_estado(uf)
        if local is None:
            ctx["avisos"].append(
                f"o Google nao tem o estado {municipios.SIGLAS.get(uf, uf)} na lista"
            )
        else:
            estados[uf] = local
    cidades = {}
    if rodada.modo == Rodada.Modo.REAL:
        for codigo in rodada.cidades or []:
            cidade = municipios.todos().get(int(codigo))
            local = municipios.local_da_cidade(cidade) if cidade else None
            if local is None:
                ctx["avisos"].append(
                    f"o Google nao mede {cidade.rotulo if cidade else codigo} sozinha"
                )
            else:
                cidades[int(codigo)] = local
    return estados, cidades


def executar(rodada: Rodada) -> Rodada:
    """Ao vivo: busca tudo agora e grava o resultado."""
    from apps.radar import provedores
    from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas

    config, contas = ConfiguracaoDoRadar.carregar(), ContasExternas.carregar()
    inicio = timezone.now()
    opcoes = {"config": config, "contas": contas, "finalidade": ChamadaExterna.Finalidade.ESTUDO}
    ctx = _contexto(rodada)
    estados, cidades = _locais(rodada, ctx)
    palavras = list(dict.fromkeys(rodada.palavras or []))
    por_uf: dict = {}
    for uf, local in estados.items():
        metricas: dict[str, dict] = {}
        if uf == ctx["centro"].uf:
            for pedido in _pedidos_de_ideias(rodada):
                for palavra, m in provedores.ideias_dataforseo(
                    **pedido, **opcoes, local=local
                ).items():
                    metricas.setdefault(palavra, {**m, "origem": "ideia"})
            # As ideias entram na lista medida nos outros estados da area.
            palavras = list(dict.fromkeys([*palavras, *metricas]))[:MAXIMO_DE_PALAVRAS]
        faltam = _faltam(palavras, metricas)
        if faltam:
            for palavra, m in provedores.metricas_dataforseo(faltam, **opcoes, local=local).items():
                metricas[palavra] = {**m, "origem": "lista"}
        por_uf[uf] = metricas
    alvo = list(dict.fromkeys(p for m in por_uf.values() for p in m))[:MAXIMO_DE_PALAVRAS]
    reais = {
        codigo: provedores.metricas_dataforseo(alvo, **opcoes, local=local)
        for codigo, local in cidades.items()
        if alvo
    }
    return _montar(rodada, ctx, por_uf, reais, inicio)


# -- Fila padrao (mais barata, volta em minutos) ---------------------------------------
# Duas etapas, guardadas em `Rodada.etapa`: 1) as ideias (sementes e site), no
# estado do centro; 2) o volume, em cada estado (as palavras + as ideias que
# faltam) e em cada cidade do modo real. A tarefa da rodada volta de tempos em
# tempos ate colher tudo; as contas no fim sao as mesmas do modo ao vivo.
def _postar(rodada, tarefas: list[tuple[str, dict, dict]], contas) -> list[str]:
    """Posta (tipo, corpo, contexto) e devolve os ids das tarefas na fila."""
    from apps.radar import fila
    from apps.radar.models import ChamadaExterna

    ids = []
    por_tipo: dict[str, list] = {}
    for tipo, corpo, contexto in tarefas:
        por_tipo.setdefault(tipo, []).append((corpo, {**contexto, "rodada": str(rodada.pk)}))
    for tipo, lote in por_tipo.items():
        ids += [
            str(t.pk)
            for t in fila.postar(
                tipo, lote, contas=contas, finalidade=ChamadaExterna.Finalidade.ESTUDO
            )
        ]
    return ids


def _colher(ids: list[str], contas) -> list | None:
    """Os resultados de todas as tarefas, ou None se alguma ainda nao voltou.
    Levanta `ProvedorIndisponivel` se uma falhou ou expirou."""
    from apps.radar import fila
    from apps.radar.models import TarefaNaFila
    from apps.radar.provedores import ProvedorIndisponivel, ler_metricas

    agora, prontas = timezone.now(), []
    for tarefa in TarefaNaFila.objects.filter(pk__in=ids):
        if tarefa.situacao == TarefaNaFila.Situacao.COLHIDA:
            prontas.append((tarefa, tarefa.contexto.get("metricas") or {}))
            continue
        if fila.vencida(tarefa, agora):
            tarefa.situacao = TarefaNaFila.Situacao.EXPIRADA
            tarefa.save(update_fields=["situacao"])
            raise ProvedorIndisponivel("a DataForSEO nao devolveu a tempo (24 h).")
        resultado = fila.colher(tarefa, contas)
        if resultado is None:
            return None
        # Guardado na propria tarefa: a proxima volta nao pede de novo.
        tarefa.contexto = {**tarefa.contexto, "metricas": ler_metricas(resultado)}
        tarefa.situacao = TarefaNaFila.Situacao.COLHIDA
        tarefa.colhida_em = timezone.now()
        tarefa.save(update_fields=["contexto", "situacao", "colhida_em"])
        prontas.append((tarefa, tarefa.contexto["metricas"]))
    return prontas


def avancar_na_fila(rodada: Rodada) -> str:
    """Um passo da rodada na fila padrao: "esperando" ou "pronta"."""
    from datetime import datetime

    from apps.radar.models import ConfiguracaoDoRadar, ContasExternas, TarefaNaFila
    from apps.radar.provedores import corpo_de_volume

    config, contas = ConfiguracaoDoRadar.carregar(), ContasExternas.carregar()
    etapa = dict(rodada.etapa or {})
    ctx = _contexto(rodada)
    estados, cidades = _locais(rodada, ctx)
    centro_local = estados.get(ctx["centro"].uf)

    def guardar():
        rodada.etapa = etapa
        rodada.save(update_fields=["etapa"])

    if not etapa:
        etapa = {"inicio": timezone.now().isoformat(), "fase": "volume_postar", "ideias": {}}
        pedidos = _pedidos_de_ideias(rodada) if centro_local else []
        if pedidos:
            tarefas = []
            for pedido in pedidos:
                corpo = corpo_de_volume([], config=config, local=centro_local)
                corpo.pop("keywords")
                corpo["sort_by"] = "search_volume"
                if pedido.get("sementes"):
                    corpo["keywords"] = pedido["sementes"][:20]
                    tarefas.append((TarefaNaFila.Tipo.IDEIAS, corpo, {}))
                else:
                    corpo["target"] = pedido["site"]
                    tarefas.append((TarefaNaFila.Tipo.IDEIAS_DO_SITE, corpo, {}))
            etapa.update(fase="ideias", tarefas=_postar(rodada, tarefas, contas))
            guardar()
            return "esperando"
    if etapa["fase"] == "ideias":
        prontas = _colher(etapa["tarefas"], contas)
        if prontas is None:
            return "esperando"
        ideias: dict = {}
        for _tarefa, metricas in prontas:
            for palavra, m in metricas.items():
                ideias.setdefault(palavra, {**m, "origem": "ideia"})
        etapa.update(fase="volume_postar", ideias=ideias)
    if etapa["fase"] == "volume_postar":
        palavras = list(dict.fromkeys([*(rodada.palavras or []), *etapa["ideias"]]))
        palavras = palavras[:MAXIMO_DE_PALAVRAS]
        tarefas = []
        for uf, local in estados.items():
            ja = etapa["ideias"] if uf == ctx["centro"].uf else {}
            faltam = _faltam(palavras, ja)
            if faltam:
                corpo = corpo_de_volume(faltam, config=config, local=local)
                tarefas.append((TarefaNaFila.Tipo.VOLUME, corpo, {"uf": uf}))
        for codigo, local in cidades.items():
            if palavras:
                corpo = corpo_de_volume(palavras, config=config, local=local)
                tarefas.append((TarefaNaFila.Tipo.VOLUME, corpo, {"cidade": codigo}))
        etapa.update(fase="volume", tarefas=_postar(rodada, tarefas, contas) if tarefas else [])
        guardar()
        if tarefas:
            return "esperando"
    prontas = _colher(etapa.get("tarefas") or [], contas)
    if prontas is None:
        return "esperando"
    por_uf: dict = {uf: {} for uf in estados}
    if ctx["centro"].uf in por_uf:
        por_uf[ctx["centro"].uf].update(etapa["ideias"])
    reais: dict = {}
    for tarefa, metricas in prontas:
        if "uf" in tarefa.contexto:
            for palavra, m in metricas.items():
                por_uf.setdefault(tarefa.contexto["uf"], {})[palavra] = {**m, "origem": "lista"}
        elif "cidade" in tarefa.contexto:
            reais[int(tarefa.contexto["cidade"])] = metricas
    _montar(rodada, ctx, por_uf, reais, datetime.fromisoformat(etapa["inicio"]))
    return "pronta"


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
