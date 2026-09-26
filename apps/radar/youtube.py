"""YouTube no radar: comentarios viram demanda, videos viram candidatos a fonte.

Duas coisas saem de cada busca de videos:

* **Sinais de demanda** — os comentarios que sao PERGUNTAS. Nenhum LLM le os
  comentarios brutos: um filtro barato separa as perguntas, e o agrupamento
  por embedding (o mesmo dos outros sinais) transforma centenas delas em
  poucos temas. O tamanho do grupo ja e o sinal de demanda.
* **Candidatos a fonte** — os proprios videos, em Documentos > Fontes
  sugeridas. A curadoria aprova o CANAL como confiavel, e nao so o video.

API oficial (YouTube Data API v3) para busca e comentarios: gratuita, com cota
diaria (uma busca custa 100 unidades de 10 mil; comentarios, 1). A chamada vai
para o livro-caixa com custo zero.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata

import httpx

from apps.radar import custos
from apps.radar.models import ChamadaExterna, ContasExternas, SinalDeDemanda
from apps.radar.provedores import ProvedorIndisponivel

logger = logging.getLogger("publibot.radar")

API = "https://www.googleapis.com/youtube/v3"

_INTERROGATIVAS = (
    "como ",
    "qual ",
    "quais ",
    "quanto ",
    "quantos ",
    "quantas ",
    "quando ",
    "onde ",
    "por que ",
    "porque ",
    "pode ",
    "posso ",
    "devo ",
    "da pra ",
    "da para ",
    "e possivel",
    "alguem sabe",
    "o que ",
    "tem como",
    "how ",
    "what ",
    "why ",
)


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.lower().split())


def e_pergunta(texto: str) -> bool:
    """Um comentario que pergunta algo, de tamanho que diz alguma coisa."""
    limpo = _normalizar(texto)
    if not 15 <= len(limpo) <= 300:
        return False
    if "http" in limpo:
        return False
    return "?" in limpo or limpo.startswith(_INTERROGATIVAS)


def _chave(contas: ContasExternas) -> str:
    from apps.inference.security import decifrar

    chave = decifrar(contas.youtube_chave_ciphertext) if contas.tem_youtube else None
    if not chave:
        raise ProvedorIndisponivel("a chave da API do YouTube nao esta configurada.")
    return chave


def _get(recurso: str, params: dict, *, contas: ContasExternas, consulta: str) -> dict:
    try:
        resposta = httpx.get(
            f"{API}/{recurso}", params={**params, "key": _chave(contas)}, timeout=30.0
        )
    except httpx.HTTPError as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.YOUTUBE,
            endpoint=recurso,
            finalidade=ChamadaExterna.Finalidade.RADAR,
            consulta=consulta,
            sucesso=False,
            erro=str(exc),
        )
        raise ProvedorIndisponivel(f"YouTube nao respondeu: {exc}") from exc

    if not resposta.is_success:
        motivo = ""
        try:
            motivo = resposta.json()["error"]["errors"][0]["reason"]
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        custos.registrar(
            provedor=ChamadaExterna.Provedor.YOUTUBE,
            endpoint=recurso,
            finalidade=ChamadaExterna.Finalidade.RADAR,
            consulta=consulta,
            sucesso=False,
            erro=f"HTTP {resposta.status_code} {motivo}",
        )
        if motivo == "commentsDisabled":
            return {"items": []}
        if motivo in {"quotaExceeded", "dailyLimitExceeded"}:
            raise ProvedorIndisponivel("a cota diaria da API do YouTube acabou.")
        raise ProvedorIndisponivel(f"YouTube respondeu HTTP {resposta.status_code} {motivo}")

    dados = resposta.json()
    custos.registrar(
        provedor=ChamadaExterna.Provedor.YOUTUBE,
        endpoint=recurso,
        finalidade=ChamadaExterna.Finalidade.RADAR,
        consulta=consulta,
        itens=len(dados.get("items") or []),
    )
    return dados


def buscar_videos(consulta: str, *, quantos: int, contas: ContasExternas, idioma: str = "pt"):
    dados = _get(
        "search",
        {
            "part": "snippet",
            "q": consulta,
            "type": "video",
            "maxResults": max(1, min(quantos, 25)),
            "relevanceLanguage": idioma,
            "order": "relevance",
        },
        contas=contas,
        consulta=consulta,
    )
    videos = []
    for item in dados.get("items") or []:
        video_id = (item.get("id") or {}).get("videoId")
        trecho = item.get("snippet") or {}
        if video_id:
            videos.append(
                {
                    "id": video_id,
                    "titulo": trecho.get("title", ""),
                    "descricao": trecho.get("description", ""),
                    "canal_id": trecho.get("channelId", ""),
                    "canal_nome": trecho.get("channelTitle", ""),
                    "publicado": trecho.get("publishedAt", ""),
                }
            )
    return videos


def comentarios(video_id: str, *, contas: ContasExternas, quantos: int = 100) -> list[dict]:
    dados = _get(
        "commentThreads",
        {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max(1, min(quantos, 100)),
            "order": "relevance",
            "textFormat": "plainText",
        },
        contas=contas,
        consulta=video_id,
    )
    saida = []
    for item in dados.get("items") or []:
        topo = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        texto = topo.get("textOriginal") or topo.get("textDisplay") or ""
        if texto:
            saida.append({"texto": texto, "curtidas": int(topo.get("likeCount") or 0)})
    return saida


def _registrar_candidato(video: dict, *, consulta: str):
    """O video como candidato a fonte. Canal confiavel (APROVAR) ja aprova."""
    from apps.knowledge.fontes_web import _ja_conhecida, aprovar, caminho_do_canal
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.videos import data_do_youtube, url_do_video

    url = url_do_video(video["id"])
    if _ja_conhecida(url):
        return None
    caminho = caminho_do_canal(video["canal_id"])
    candidato = CandidatoDeFonte.objects.create(
        url=url,
        tipo=CandidatoDeFonte.Tipo.VIDEO,
        titulo=video["titulo"][:500],
        trecho=video["descricao"][:1000],
        dominio=f"youtube.com/channel/{video['canal_id']}"[:200],
        canal_id=video["canal_id"][:100],
        canal_nome=video["canal_nome"][:200],
        publicado_em=data_do_youtube(video["publicado"]),
        consulta=consulta[:500],
        preferido=caminho is not None,
    )
    if caminho is not None and caminho.nivel == "aprovar":
        aprovar(candidato, categoria=caminho.categoria, automatico=True)
    return candidato


def colher_sinais(sementes: list[str], *, rodada, videos: int) -> list[SinalDeDemanda]:
    """Busca videos das sementes, colhe as perguntas dos comentarios.

    `videos` e o total da rodada, dividido entre as sementes.
    """
    from apps.radar.coleta import _novo_sinal
    from apps.radar.models import ConfiguracaoDoRadar

    contas = ContasExternas.carregar()
    config = ConfiguracaoDoRadar.carregar()
    sementes = sementes[:3]
    if not sementes or videos <= 0:
        return []
    por_semente = max(1, math.ceil(videos / len(sementes)))

    novos: list[SinalDeDemanda] = []
    for semente in sementes:
        for video in buscar_videos(
            semente, quantos=por_semente, contas=contas, idioma=config.codigo_de_idioma
        ):
            _registrar_candidato(video, consulta=semente)
            for comentario in comentarios(video["id"], contas=contas):
                if not e_pergunta(comentario["texto"]):
                    continue
                texto = re.sub(r"\s+", " ", comentario["texto"]).strip()
                sinal = _novo_sinal(
                    texto[:500],
                    SinalDeDemanda.Fonte.YOUTUBE,
                    rodada=rodada,
                    video=video["id"],
                    curtidas=comentario["curtidas"],
                )
                if sinal is not None:
                    novos.append(sinal)
    return novos
