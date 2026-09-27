"""Desempenho dos artigos no site: leitura de verdade e conversao.

Os numeros sao do proprio site (recurso `insights` do contrato), agregados por
dia e sem identificar ninguem. Aqui viram, por artigo:

* **leituras de verdade** — aberturas com pelo menos 10 s de tempo ativo;
  quem abriu e fechou fica de fora;
* **tempo ativo medio** e **chegaram ao fim**;
* **chamada** — quantos viram o bloco e quantos clicaram;
* **conversoes**, em tres leituras da mesma jornada, como no Google Ads:
  *ultimo artigo* (o que estava aberto na conversao), *participou* (esteve na
  jornada, em qualquer posicao) e *atribuidas* (cada conversao dividida em
  partes iguais entre os artigos lidos — o modelo linear).

Artigo lido por menos de 10 s nao entra na jornada: nao dá para dizer que ele
ajudou a convencer alguem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

LEITURA_MINIMA = 10
# Para o radar: abaixo disto a taxa de um artigo e ruido.
MINIMO_DE_LEITURAS = 50


@dataclass
class Desempenho:
    remote_id: str
    artigo: object = None
    views: int = 0
    engaged_views: int = 0
    engaged_seconds: int = 0
    read_to_end: int = 0
    cta_views: int = 0
    cta_clicks: int = 0
    ultimo: int = 0
    participou: int = 0
    atribuidas: float = 0.0

    @property
    def titulo(self) -> str:
        return self.artigo.title if self.artigo else self.remote_id

    @property
    def url(self) -> str:
        return self.artigo.published_url if self.artigo else ""

    @staticmethod
    def _parte(numerador, denominador) -> float | None:
        return numerador / denominador if denominador else None

    @property
    def taxa_de_leitura(self):
        return self._parte(self.engaged_views, self.views)

    @property
    def tempo_medio(self):
        return self._parte(self.engaged_seconds, self.engaged_views)

    @property
    def taxa_do_fim(self):
        return self._parte(self.read_to_end, self.engaged_views)

    @property
    def taxa_de_clique(self):
        return self._parte(self.cta_clicks, self.cta_views)

    @property
    def taxa_de_conversao(self):
        return self._parte(self.atribuidas, self.engaged_views)


@dataclass
class Painel:
    dias: int
    linhas: list[Desempenho] = field(default_factory=list)
    conversoes: int = 0
    sem_artigo: int = 0
    pela_chamada: int = 0


def _jornada_lida(jornada: list[dict]) -> list[str]:
    """Os artigos lidos de verdade, sem repetir, na ordem da ultima leitura."""
    vistos: list[str] = []
    for item in jornada or []:
        if (item.get("engaged_seconds") or 0) < LEITURA_MINIMA:
            continue
        remote_id = item.get("remote_id")
        if remote_id in vistos:
            vistos.remove(remote_id)
        vistos.append(remote_id)
    return vistos


def _artigos_por_id(ids) -> dict:
    """A versao no ar de cada id remoto (versoes novas herdam o id)."""
    from apps.content.models import Article

    por_id: dict = {}
    for artigo in Article.objects.filter(remote_id__in=list(ids)).order_by("created_at"):
        atual = por_id.get(artigo.remote_id)
        if atual is None or artigo.status == Article.Status.PUBLISHED:
            por_id[artigo.remote_id] = artigo
    return por_id


def painel(dias: int = 28) -> Painel:
    from apps.integrations.models import ConversaoDoSite, LeituraDoDia

    desde = timezone.localdate() - timedelta(days=dias)
    linhas: dict[str, Desempenho] = {}

    somas = (
        LeituraDoDia.objects.filter(dia__gte=desde)
        .values("remote_id")
        .annotate(
            views_=Sum("views"),
            engaged_views_=Sum("engaged_views"),
            engaged_seconds_=Sum("engaged_seconds"),
            read_to_end_=Sum("read_to_end"),
            cta_views_=Sum("cta_views"),
            cta_clicks_=Sum("cta_clicks"),
        )
    )
    for soma in somas:
        linha = linhas.setdefault(soma["remote_id"], Desempenho(soma["remote_id"]))
        for campo in ("views", "engaged_views", "engaged_seconds", "read_to_end"):
            setattr(linha, campo, soma[f"{campo}_"] or 0)
        linha.cta_views = soma["cta_views_"] or 0
        linha.cta_clicks = soma["cta_clicks_"] or 0

    resultado = Painel(dias=dias)
    for conversao in ConversaoDoSite.objects.filter(dia__gte=desde):
        resultado.conversoes += 1
        resultado.pela_chamada += int(conversao.via_cta)
        lidos = _jornada_lida(conversao.jornada)
        if not lidos:
            resultado.sem_artigo += 1
            continue
        linhas.setdefault(lidos[-1], Desempenho(lidos[-1])).ultimo += 1
        for remote_id in lidos:
            linha = linhas.setdefault(remote_id, Desempenho(remote_id))
            linha.participou += 1
            linha.atribuidas += 1 / len(lidos)

    artigos = _artigos_por_id(linhas)
    for remote_id, linha in linhas.items():
        linha.artigo = artigos.get(remote_id)
    resultado.linhas = sorted(
        linhas.values(), key=lambda d: (-d.atribuidas, -d.engaged_views, d.titulo)
    )
    return resultado


def que_convertem(dias: int = 90):
    """Para o radar: (vetor, taxa de conversao) dos artigos com dado bastante,
    e a taxa media do site. None quando nao ha conversao nenhuma a comparar.
    """
    import numpy as np

    from apps.radar.atualizacoes import vigia_de

    linhas = [
        linha
        for linha in painel(dias).linhas
        if linha.artigo is not None and linha.engaged_views >= MINIMO_DE_LEITURAS
    ]
    leituras = sum(linha.engaged_views for linha in linhas)
    atribuidas = sum(linha.atribuidas for linha in linhas)
    if not leituras or not atribuidas:
        return None
    vizinhos = []
    for linha in linhas:
        vigia = vigia_de(linha.artigo)
        if vigia.vetor is not None:
            vizinhos.append((np.asarray(vigia.vetor, dtype=np.float32), linha.taxa_de_conversao))
    return (vizinhos, atribuidas / leituras) if vizinhos else None
