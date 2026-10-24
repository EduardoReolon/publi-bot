"""Os municipios do Brasil, a area de um estudo (raio) e a populacao.

As coordenadas vem de `dados/municipios.csv` (projeto aberto
kelvins/municipios-brasileiros, licenca MIT, com os codigos do IBGE). A
populacao vem do Censo do IBGE (SIDRA, tabela 4709), lida uma vez por estado e
guardada em `Populacao`.

O Google (e a DataForSEO) nao medem volume num raio: o raio e feito aqui, com
a distancia em linha reta ate cada municipio.
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import httpx

from apps.dados.locais import UFS
from apps.estudos.models import Populacao

logger = logging.getLogger("publibot.estudos")

ARQUIVO = Path(__file__).parent / "dados" / "municipios.csv"
SIDRA = "https://apisidra.ibge.gov.br/values/t/4709/n6/in%20n3%20{uf}/v/93/p/last"
# Codigo do IBGE de cada estado -> sigla.
SIGLAS = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL", 28: "SE", 29: "BA",
    31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR", 42: "SC", 43: "RS",
    50: "MS", 51: "MT", 52: "GO", 53: "DF",
}  # fmt: skip


@dataclass(frozen=True)
class Municipio:
    codigo: int
    nome: str
    latitude: float
    longitude: float
    uf: int

    @property
    def sigla(self) -> str:
        return SIGLAS.get(self.uf, "")

    @property
    def estado(self) -> str:
        return UFS.get(self.sigla, "")

    @property
    def rotulo(self) -> str:
        return f"{self.nome} ({self.sigla})"


@cache
def todos() -> dict[int, Municipio]:
    with ARQUIVO.open(encoding="utf-8") as arquivo:
        return {
            int(linha["codigo_ibge"]): Municipio(
                codigo=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                latitude=float(linha["latitude"]),
                longitude=float(linha["longitude"]),
                uf=int(linha["codigo_uf"]),
            )
            for linha in csv.DictReader(arquivo)
        }


def procurar(texto: str, limite: int = 15) -> list[Municipio]:
    """Municipios pelo nome (sem acento), os que comecam com o texto primeiro."""
    from apps.radar.locais import normalizar

    termo = normalizar(texto)
    if len(termo) < 2:
        return []
    achados = [m for m in todos().values() if termo in normalizar(m.nome)]
    achados.sort(key=lambda m: (not normalizar(m.nome).startswith(termo), m.nome, m.sigla))
    return achados[:limite]


def distancia_km(a: Municipio, b: Municipio) -> float:
    """Em linha reta (haversine)."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def no_raio(centro: int, raio_km: float) -> list[tuple[Municipio, float]]:
    """(municipio, distancia) dentro do raio, do mais perto ao mais longe."""
    base = todos().get(centro)
    if base is None:
        return []
    saida = [(m, distancia_km(base, m)) for m in todos().values()]
    return sorted(((m, d) for m, d in saida if d <= raio_km), key=lambda x: x[1])


# -- Populacao -------------------------------------------------------------------------
def _ler_sidra(dados) -> dict[int, int]:
    """{codigo do municipio: populacao} da resposta do SIDRA (a 1a linha e o
    cabecalho, que diz qual coluna e o municipio)."""
    if not isinstance(dados, list) or len(dados) < 2:
        return {}
    cabecalho = dados[0]
    coluna = next((k for k, v in cabecalho.items() if k.endswith("C") and "Munic" in str(v)), "D1C")
    saida = {}
    for linha in dados[1:]:
        try:
            saida[int(linha[coluna])] = int(float(linha["V"]))
        except (KeyError, TypeError, ValueError):
            continue  # "..." ou "-": sem dado
    return saida


def carregar_populacao(ufs: set[int]) -> list[str]:
    """Le do IBGE a populacao dos estados que ainda nao estao guardados.
    Devolve os avisos (estado que nao respondeu)."""
    avisos = []
    guardados = set(Populacao.objects.values_list("codigo", flat=True))
    for uf in sorted(ufs):
        codigos = {c for c, m in todos().items() if m.uf == uf}
        if codigos <= guardados:
            continue
        try:
            resposta = httpx.get(SIDRA.format(uf=uf), timeout=60.0)
            resposta.raise_for_status()
            lidos = _ler_sidra(resposta.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Populacao do estado %s nao lida: %s", uf, exc)
            avisos.append(f"populacao de {SIGLAS.get(uf, uf)} nao lida no IBGE: {exc}")
            continue
        Populacao.objects.bulk_create(
            [Populacao(codigo=c, populacao=p, ano=2022) for c, p in lidos.items()],
            update_conflicts=True,
            unique_fields=["codigo"],
            update_fields=["populacao", "ano"],
        )
    return avisos


def populacoes(codigos) -> dict[int, int]:
    return dict(
        Populacao.objects.filter(codigo__in=list(codigos)).values_list("codigo", "populacao")
    )


def populacao_do_estado(uf: int) -> int:
    codigos = [c for c, m in todos().items() if m.uf == uf]
    return sum(populacoes(codigos).values())


# -- Os locais do Google (DataForSEO) ------------------------------------------------
def _garantir_locais() -> None:
    """A lista de locais da DataForSEO (rota gratuita), se ainda nao baixada."""
    from apps.radar import locais
    from apps.radar.models import ContasExternas, LocalDisponivel

    if not LocalDisponivel.objects.filter(pais="BR", tipo="City").exists():
        locais.atualizar_lista(ContasExternas.carregar())


def local_do_estado(uf: int) -> int | None:
    from apps.radar.locais import normalizar
    from apps.radar.models import LocalDisponivel

    _garantir_locais()
    nome = normalizar(UFS.get(SIGLAS.get(uf, ""), ""))
    local = LocalDisponivel.objects.filter(pais="BR", tipo="State", busca=nome).first()
    return local.codigo if local else None


def local_da_cidade(municipio: Municipio) -> int | None:
    """O local do Google da cidade, pelo nome e pelo estado ('Cascavel, Parana')."""
    from apps.radar.locais import normalizar
    from apps.radar.models import LocalDisponivel

    _garantir_locais()
    busca = normalizar(f"{municipio.nome}, {municipio.estado}")
    local = (
        LocalDisponivel.objects.filter(pais="BR", busca=busca)
        .filter(tipo__in=["City", "Municipality"])
        .first()
    )
    return local.codigo if local else None
