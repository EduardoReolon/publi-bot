"""Fila padrao (Standard) da DataForSEO: postar agora, colher em minutos.

A mesma pagina de resultados custa cerca de um terco quando nao se exige a
resposta na hora. A rodada do radar nao tem pressa, entao posta as tarefas e
volta depois; so a busca manual, em que alguem esta olhando a tela, usa o
modo ao vivo.

Como funciona:

* `postar` manda ate 100 tarefas por requisicao (`.../task_post`). O custo e
  cobrado AQUI, e e aqui que vai para o livro-caixa e que o teto e conferido;
* cada tarefa aceita vira uma `TarefaNaFila`;
* `colher` pede o resultado (`.../task_get/...`), que e gratuito. Enquanto a
  tarefa esta na fila, a DataForSEO responde com os codigos 40601/40602 e a
  tarefa continua esperando. O batimento `colher_fila` passa a cada 5 minutos;
* tarefa que nao voltou em `PRAZO` expira, e a rodada segue com o que chegou.

Os caminhos seguem a documentacao v3 da DataForSEO. Os de SERP e volume sao os
mesmos das rotas ao vivo com `task_post`/`task_get` no lugar de `live`; o de
avaliacoes so existe na fila.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import httpx

from apps.radar import custos
from apps.radar.models import ChamadaExterna, ContasExternas, TarefaNaFila
from apps.radar.provedores import (
    DATAFORSEO_BASE,
    TIMEOUT,
    ProvedorIndisponivel,
    _credenciais_dataforseo,
    conferir_http_dataforseo,
)

logger = logging.getLogger("publibot.radar")

ROTAS = {
    TarefaNaFila.Tipo.SERP: (
        "/serp/google/organic/task_post",
        "/serp/google/organic/task_get/advanced/{id}",
    ),
    TarefaNaFila.Tipo.VOLUME: (
        "/keywords_data/google_ads/search_volume/task_post",
        "/keywords_data/google_ads/search_volume/task_get/{id}",
    ),
    TarefaNaFila.Tipo.AVALIACOES: (
        "/business_data/google/reviews/task_post",
        "/business_data/google/reviews/task_get/{id}",
    ),
}

CRIADA = 20100
PRONTA = 20000
# "Task Handed" e "Task In Queue": ainda nao terminou.
AINDA_NA_FILA = {40601, 40602}
POR_REQUISICAO = 100
PRAZO = timedelta(hours=24)


def _conferir_resposta(resposta: httpx.Response) -> dict:
    conferir_http_dataforseo(resposta)
    dados = resposta.json()
    if dados.get("status_code") != PRONTA:
        raise ProvedorIndisponivel(
            f"DataForSEO: {dados.get('status_code')} {dados.get('status_message', '')}"
        )
    return dados


def postar(
    tipo: str,
    tarefas: list[tuple[dict, dict]],
    *,
    contas: ContasExternas,
    finalidade: str,
    rodada=None,
) -> list[TarefaNaFila]:
    """Posta as tarefas. `tarefas` e uma lista de (corpo, contexto).

    O contexto fica guardado na `TarefaNaFila` e volta para quem processa o
    resultado (a semente da busca, o concorrente da avaliacao...).
    """
    caminho = ROTAS[tipo][0]
    criadas: list[TarefaNaFila] = []
    for inicio in range(0, len(tarefas), POR_REQUISICAO):
        lote = tarefas[inicio : inicio + POR_REQUISICAO]
        estimativa = custos.ESTIMATIVAS[("dataforseo", f"{tipo}_fila")] * len(lote)
        custos.conferir_teto(estimativa)
        # A etiqueta volta na resposta: e ela que casa a tarefa com o contexto,
        # sem depender da ordem.
        corpo = [{**c, "tag": str(i)} for i, (c, _) in enumerate(lote)]
        login, senha = _credenciais_dataforseo(contas)
        try:
            dados = _conferir_resposta(
                httpx.post(
                    f"{DATAFORSEO_BASE}{caminho}",
                    json=corpo,
                    auth=(login, senha),
                    timeout=TIMEOUT,
                )
            )
        except (httpx.HTTPError, ValueError, ProvedorIndisponivel) as exc:
            custos.registrar(
                provedor=ChamadaExterna.Provedor.DATAFORSEO,
                endpoint=caminho.lstrip("/"),
                finalidade=finalidade,
                consulta=f"{len(lote)} tarefas",
                sucesso=False,
                erro=str(exc),
            )
            if isinstance(exc, ProvedorIndisponivel):
                raise
            raise ProvedorIndisponivel(f"DataForSEO nao respondeu: {exc}") from exc

        recusadas = []
        for indice, tarefa in enumerate(dados.get("tasks") or []):
            etiqueta = (tarefa.get("data") or {}).get("tag")
            posicao = int(etiqueta) if str(etiqueta or "").isdigit() else indice
            if tarefa.get("status_code") != CRIADA or not tarefa.get("id"):
                recusadas.append(f"{tarefa.get('status_code')} {tarefa.get('status_message', '')}")
                continue
            criadas.append(
                TarefaNaFila.objects.create(
                    task_id=tarefa["id"],
                    tipo=tipo,
                    finalidade=finalidade,
                    rodada=rodada,
                    contexto=lote[posicao][1] if posicao < len(lote) else {},
                )
            )
        custos.registrar(
            provedor=ChamadaExterna.Provedor.DATAFORSEO,
            endpoint=caminho.lstrip("/"),
            finalidade=finalidade,
            consulta=f"{len(lote)} tarefas",
            custo=Decimal(str(dados.get("cost") or 0)),
            itens=len(lote) - len(recusadas),
            sucesso=not recusadas,
            erro="; ".join(recusadas),
        )
    return criadas


def colher(tarefa: TarefaNaFila, contas: ContasExternas) -> dict | None:
    """O resultado da tarefa, ou None se ainda esta na fila.

    Levanta `ProvedorIndisponivel` se a DataForSEO disser que a tarefa falhou.
    """
    caminho = ROTAS[tarefa.tipo][1].format(id=tarefa.task_id)
    login, senha = _credenciais_dataforseo(contas)
    try:
        resposta = httpx.get(f"{DATAFORSEO_BASE}{caminho}", auth=(login, senha), timeout=TIMEOUT)
        if resposta.status_code >= 500:
            raise httpx.HTTPError(f"HTTP {resposta.status_code}")
        dados = _conferir_resposta(resposta)
    except (httpx.HTTPError, ValueError) as exc:
        # Falha de rede nao condena a tarefa: o proximo batimento tenta de novo
        # (e o PRAZO e o limite dessa paciencia).
        logger.warning("Nao foi possivel colher %s: %s", tarefa, exc)
        return None
    resultado = (dados.get("tasks") or [{}])[0]
    codigo = resultado.get("status_code")
    if codigo in AINDA_NA_FILA:
        return None
    if codigo != PRONTA:
        raise ProvedorIndisponivel(f"{codigo} {resultado.get('status_message', '')}")
    return resultado


def vencida(tarefa: TarefaNaFila, agora) -> bool:
    return agora - tarefa.criada_em > PRAZO
