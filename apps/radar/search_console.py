"""Search Console: como o site esta indo, e onde ele quase chega.

Duas respostas que nenhum dado de mercado da:

* **Para quais buscas o site ja aparece, e em que posicao.** As consultas em
  que ele esta entre a 8a e a 20a posicao, com impressoes, sao as de retorno
  mais rapido: a pagina ja existe e o Google ja a considera — falta pouco.
  Elas viram sinais de demanda no radar.
* **Como cada artigo publicado esta indo.** A coleta e guardada por retrato,
  e o painel compara o artigo com ele mesmo ao longo do tempo.

**Acesso sem OAuth.** O cliente adiciona o e-mail de UMA conta de servico
nossa (arquivo em `GSC_CONTA_DE_SERVICO_ARQUIVO`) como usuario da
propriedade, com permissao de leitura. Um app OAuth com este escopo exigiria
verificacao pelo Google; a conta de servico nao exige.

O token e obtido assinando um JWT com a chave privada da conta de servico
(RS256, com a `cryptography` que o projeto ja usa) — sem biblioteca extra do
Google.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import threading
import time
from functools import lru_cache
from urllib.parse import quote

import httpx
from django.conf import settings
from django.utils import timezone

from apps.radar import custos
from apps.radar.models import (
    ChamadaExterna,
    ColetaDoConsole,
    ConfiguracaoDoRadar,
    LinhaDoConsole,
    SinalDeDemanda,
)
from apps.radar.provedores import ProvedorIndisponivel

logger = logging.getLogger("publibot.radar")

ESCOPO = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://searchconsole.googleapis.com/webmasters/v3"

# "Quase la": posicao media entre estas duas, e impressoes suficientes para
# nao ser ruido.
POSICAO_QUASE_LA = (8.0, 20.0)
IMPRESSOES_MINIMAS = 10

# Retratos guardados. Mais que isso e historia que ninguem abre.
COLETAS_GUARDADAS = 12


@lru_cache(maxsize=1)
def conta_de_servico() -> dict | None:
    caminho = getattr(settings, "GSC_CONTA_DE_SERVICO_ARQUIVO", "")
    if not caminho:
        return None
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError):
        logger.exception("Nao foi possivel ler a conta de servico do Search Console.")
        return None
    if not dados.get("client_email") or not dados.get("private_key"):
        return None
    return dados


def email_da_conta() -> str:
    conta = conta_de_servico()
    return conta["client_email"] if conta else ""


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _jwt(conta: dict, agora: int) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    cabecalho = {"alg": "RS256", "typ": "JWT"}
    corpo = {
        "iss": conta["client_email"],
        "scope": ESCOPO,
        "aud": conta.get("token_uri") or "https://oauth2.googleapis.com/token",
        "iat": agora,
        "exp": agora + 3600,
    }
    base = f"{_b64(json.dumps(cabecalho).encode())}.{_b64(json.dumps(corpo).encode())}"
    chave = serialization.load_pem_private_key(conta["private_key"].encode(), password=None)
    assinatura = chave.sign(base.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{base}.{_b64(assinatura)}"


_trava = threading.Lock()
_token: dict = {"valor": "", "expira": 0.0}


def _token_de_acesso() -> str:
    conta = conta_de_servico()
    if conta is None:
        raise ProvedorIndisponivel(
            "a conta de servico do Search Console nao esta configurada "
            "(GSC_CONTA_DE_SERVICO_ARQUIVO)."
        )
    with _trava:
        if _token["valor"] and _token["expira"] > time.time() + 60:
            return _token["valor"]
        agora = int(time.time())
        try:
            resposta = httpx.post(
                conta.get("token_uri") or "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": _jwt(conta, agora),
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise ProvedorIndisponivel(f"Google nao emitiu o token: {exc}") from exc
        if not resposta.is_success:
            raise ProvedorIndisponivel(
                f"Google recusou a conta de servico (HTTP {resposta.status_code})."
            )
        dados = resposta.json()
        _token["valor"] = dados["access_token"]
        _token["expira"] = agora + int(dados.get("expires_in", 3600))
        return _token["valor"]


def consultar(propriedade: str, inicio: datetime.date, fim: datetime.date) -> list[dict]:
    """Linhas (consulta, pagina) do periodo, do Search Analytics."""
    corpo = {
        "startDate": inicio.isoformat(),
        "endDate": fim.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": 5000,
    }
    endpoint = f"sites/{quote(propriedade, safe='')}/searchAnalytics/query"
    try:
        resposta = httpx.post(
            f"{API}/{endpoint}",
            json=corpo,
            headers={"Authorization": f"Bearer {_token_de_acesso()}"},
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.SEARCH_CONSOLE,
            endpoint="searchAnalytics/query",
            finalidade=ChamadaExterna.Finalidade.RADAR,
            consulta=propriedade,
            sucesso=False,
            erro=str(exc),
        )
        raise ProvedorIndisponivel(f"Search Console nao respondeu: {exc}") from exc

    if resposta.status_code == 403:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.SEARCH_CONSOLE,
            endpoint="searchAnalytics/query",
            finalidade=ChamadaExterna.Finalidade.RADAR,
            consulta=propriedade,
            sucesso=False,
            erro="403",
        )
        raise ProvedorIndisponivel(
            f"sem acesso a {propriedade}. Adicione {email_da_conta()} como usuario "
            f"da propriedade no Search Console (Configuracoes > Usuarios e permissoes)."
        )
    if not resposta.is_success:
        raise ProvedorIndisponivel(f"Search Console respondeu HTTP {resposta.status_code}.")

    linhas = resposta.json().get("rows") or []
    custos.registrar(
        provedor=ChamadaExterna.Provedor.SEARCH_CONSOLE,
        endpoint="searchAnalytics/query",
        finalidade=ChamadaExterna.Finalidade.RADAR,
        consulta=propriedade,
        itens=len(linhas),
    )
    return linhas


def coletar(dias: int = 28) -> ColetaDoConsole:
    """Um retrato dos ultimos `dias`, terminando 3 dias atras.

    Os ultimos dois ou tres dias do Search Console sao provisorios e mudam.
    Parar antes deles faz um retrato ser comparavel com o seguinte.
    """
    config = ConfiguracaoDoRadar.carregar()
    if not config.propriedade_search_console:
        raise ProvedorIndisponivel("informe a propriedade do Search Console na tela do Radar.")

    fim = timezone.localdate() - datetime.timedelta(days=3)
    inicio = fim - datetime.timedelta(days=dias - 1)
    linhas = consultar(config.propriedade_search_console, inicio, fim)

    coleta = ColetaDoConsole.objects.create(
        propriedade=config.propriedade_search_console, inicio=inicio, fim=fim, linhas=len(linhas)
    )
    LinhaDoConsole.objects.bulk_create(
        [
            LinhaDoConsole(
                coleta=coleta,
                consulta=(linha.get("keys") or ["", ""])[0][:500],
                pagina=(linha.get("keys") or ["", ""])[1][:500],
                cliques=int(linha.get("clicks") or 0),
                impressoes=int(linha.get("impressions") or 0),
                ctr=float(linha.get("ctr") or 0),
                posicao=float(linha.get("position") or 0),
            )
            for linha in linhas
            if len(linha.get("keys") or []) == 2
        ]
    )

    antigas = ColetaDoConsole.objects.order_by("-coletada_em")[COLETAS_GUARDADAS:]
    ColetaDoConsole.objects.filter(pk__in=[c.pk for c in antigas]).delete()
    return coleta


def quase_la(coleta: ColetaDoConsole):
    """Consultas em que o site esta perto da primeira pagina."""
    baixo, alto = POSICAO_QUASE_LA
    return coleta.linhas_set.filter(
        posicao__gte=baixo, posicao__lte=alto, impressoes__gte=IMPRESSOES_MINIMAS
    ).order_by("-impressoes")


def colher_sinais(*, rodada) -> list[SinalDeDemanda]:
    """Coleta um retrato e transforma o "quase la" em sinais de demanda.

    O volume do sinal e o numero de impressoes no periodo: e demanda REAL que
    ja chega ao site, e por isso pesa na nota como volume de busca.
    """
    from apps.radar.coleta import _novo_sinal

    coleta = coletar()
    novos = []
    for linha in quase_la(coleta)[:50]:
        sinal = _novo_sinal(
            linha.consulta,
            SinalDeDemanda.Fonte.SEARCH_CONSOLE,
            rodada=rodada,
            posicao=round(linha.posicao, 1),
            impressoes=linha.impressoes,
            cliques=linha.cliques,
            pagina=linha.pagina,
        )
        if sinal is not None:
            sinal.volume = linha.impressoes
            sinal.save(update_fields=["volume"])
            novos.append(sinal)
    return novos


def desempenho_dos_artigos(limite: int = 30) -> list[dict]:
    """Cliques, impressoes e posicao de cada artigo publicado, no ultimo retrato.

    Compara com o retrato anterior quando ha, para a tela mostrar a direcao.
    """
    from django.db.models import Avg, Sum

    from apps.content.models import Article

    coletas = list(ColetaDoConsole.objects.order_by("-coletada_em")[:2])
    if not coletas:
        return []

    def totais(coleta, url):
        agregado = coleta.linhas_set.filter(pagina=url).aggregate(
            cliques=Sum("cliques"), impressoes=Sum("impressoes"), posicao=Avg("posicao")
        )
        return agregado

    saida = []
    artigos = Article.objects.exclude(published_url="").order_by("-published_at")[:limite]
    for artigo in artigos:
        atual = totais(coletas[0], artigo.published_url)
        anterior = totais(coletas[1], artigo.published_url) if len(coletas) > 1 else {}
        principais = list(
            coletas[0]
            .linhas_set.filter(pagina=artigo.published_url)
            .order_by("-impressoes")
            .values("consulta", "impressoes", "posicao")[:3]
        )
        saida.append(
            {
                "artigo": artigo,
                "cliques": atual["cliques"] or 0,
                "impressoes": atual["impressoes"] or 0,
                "posicao": atual["posicao"],
                "posicao_anterior": (anterior or {}).get("posicao"),
                "consultas": principais,
            }
        )
    return saida
