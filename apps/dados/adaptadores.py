"""Um adaptador por instituicao: traduz o formato dela para um so.

A geracao do artigo nunca ve a estrutura de cada lugar. Ela pede o valor de
uma serie, num local, e recebe `ValorObservado`. Adaptador que ainda nao foi
escrito (`pronto = False`) nao quebra nada: o catalogo usa os valores ja
gravados (digitados a mao, por exemplo).

Para escrever um adaptador: implemente `procurar` (o catalogo da instituicao,
para sugerir series) e `valores` (os numeros de uma serie), marque `pronto`, e
registre em ADAPTADORES. Teste com respostas gravadas, sem rede.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal


class AdaptadorPendente(Exception):
    """O adaptador desta instituicao ainda nao foi escrito."""


@dataclass
class ValorObservado:
    periodo: str
    valor: Decimal
    local: str = "Brasil"
    nota: str = ""


@dataclass
class SerieEncontrada:
    codigo: str
    titulo: str
    descricao: str = ""
    unidade: str = ""
    recortes: list = field(default_factory=list)
    periodicidade: str = ""
    url: str = ""


class Adaptador:
    nome = ""
    pronto = False

    def procurar(self, termo: str, limite: int = 20) -> list[SerieEncontrada]:
        raise AdaptadorPendente(f"o adaptador {self.nome} ainda nao foi escrito.")

    def valores(self, serie, *, local: str = "Brasil", ultimos: int = 1) -> list[ValorObservado]:
        """`local`: "Brasil" ou o nome do estado (apps/dados/locais.py); traduza
        para o codigo da instituicao. Sem esse recorte, devolva []."""
        raise AdaptadorPendente(f"o adaptador {self.nome} ainda nao foi escrito.")

    def codigo_do_link(self, url: str) -> str:
        """O codigo da serie num link citado pelas fontes ("" se nao reconhece).
        Ex.: sidra.ibge.gov.br/tabela/4752 -> "4752". Alimenta o catalogo pelo acervo."""
        return ""


class IBGE(Adaptador):
    """SIDRA: servicodados.ibge.gov.br/api/v3/agregados (catalogo e metadados)
    e apisidra.ibge.gov.br/values (valores). A FAZER."""

    nome = "IBGE"


class BancoCentral(Adaptador):
    """SGS: api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados. A FAZER."""

    nome = "Banco Central"


@contextmanager
def arquivo_temporario(url: str, *, limite_mb: int = 2048):
    """Baixa um arquivo grande (DATASUS, planilhas) para um temporario e APAGA
    ao sair, deu certo ou nao. O adaptador le, resume (por ano e estado) e
    grava so o resumo em `Valor`: o servidor nao guarda a base inteira.
    Passou de `limite_mb`: para e apaga."""
    import os
    import tempfile

    import httpx

    caminho = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, prefix="publibot-dados-") as destino:
            caminho = destino.name
            baixado = 0
            with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resposta:
                resposta.raise_for_status()
                for pedaco in resposta.iter_bytes(1 << 20):
                    baixado += len(pedaco)
                    if baixado > limite_mb * (1 << 20):
                        raise ValueError(f"{url} passou de {limite_mb} MB.")
                    destino.write(pedaco)
        yield caminho
    finally:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)


ADAPTADORES: dict[str, Adaptador] = {"ibge": IBGE(), "bcb": BancoCentral()}


def adaptador_de(instituicao) -> Adaptador | None:
    return ADAPTADORES.get(instituicao.adaptador or "")


def situacao_do_adaptador(instituicao) -> str:
    """ "pronto", "a_fazer" (previsto, sem codigo ainda) ou "nenhum"."""
    adaptador = adaptador_de(instituicao)
    if adaptador is None:
        return "nenhum"
    return "pronto" if adaptador.pronto else "a_fazer"
