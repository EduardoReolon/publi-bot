"""Planilhas exportadas das redes (.csv ou .xlsx): ler, achar o cabecalho e
converter numeros e datas. Usado pelo gasto com anuncios (anuncios.py) e pelo
historico de posts enviado a mao (historico.py).

Cada rede exporta de um jeito: a Meta em .csv com o cabecalho na 1a linha; o
LinkedIn em .xlsx (ou .xls salvo como .xlsx) com linhas de explicacao antes do
cabecalho e, as vezes, duas tabelas lado a lado; o Google Ads em .csv com o
nome do relatorio e o periodo antes do cabecalho. Por isso o cabecalho e
procurado nas primeiras linhas de cada aba, pelos nomes de coluna conhecidos.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

LINHAS_ATE_O_CABECALHO = 20


class PlanilhaInvalida(ValueError):
    pass


# -- Textos, numeros e datas -------------------------------------------------------------
def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )


def chave(texto: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", sem_acento(texto).lower()).split())


def numero(texto) -> Decimal | None:
    """1.234,56 · 1,234.56 · 1234.56 · R$ 12,00 · 12 -> Decimal."""
    texto = re.sub(r"[^\d,.\-]", "", str(texto or ""))
    if not texto or texto in {"-", ".", ","}:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def inteiro(texto) -> int | None:
    n = numero(texto)
    return int(n) if n is not None else None


_DATA = re.compile(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})(?:[ T](\d{1,2}):(\d{2}))?")


def momento(texto, *, mes_primeiro: bool = False) -> datetime | None:
    """'2025-10-03', '03/10/2025 14:30', '10/3/2025' (mes primeiro, export em
    ingles) -> datetime sem fuso. None se nao for data."""
    achado = _DATA.search(str(texto or ""))
    if not achado:
        return None
    a, b, c, hora, minuto = achado.groups()
    if len(a) == 4:
        ano, mes, dia = int(a), int(b), int(c)
    else:
        ano = int(c) + (2000 if len(c) == 2 else 0)
        dia, mes = (int(b), int(a)) if mes_primeiro else (int(a), int(b))
        if mes > 12 >= dia:  # a ordem estava trocada
            dia, mes = mes, dia
    try:
        return datetime(ano, mes, dia, int(hora or 0), int(minuto or 0))
    except ValueError:
        return None


def data(texto, *, mes_primeiro: bool = False) -> date | None:
    m = momento(texto, mes_primeiro=mes_primeiro)
    return m.date() if m else None


def mes_primeiro(valores: list[str], cabecalho_em_ingles: bool) -> bool:
    """A ordem das datas da coluna: um dia > 12 decide; sem isso, a lingua."""
    for valor in valores:
        achado = _DATA.search(valor or "")
        if achado and len(achado.group(1)) <= 2:
            a, b = int(achado.group(1)), int(achado.group(2))
            if a > 12:
                return False
            if b > 12:
                return True
    return cabecalho_em_ingles


# -- Arquivo e cabecalho -------------------------------------------------------------------
def _celula(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, datetime | date):
        return valor.isoformat(sep=" ") if isinstance(valor, datetime) else valor.isoformat()
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def ler(conteudo: bytes) -> list[list[list[str]]]:
    """As abas do arquivo (o .csv e uma aba so), cada uma com suas linhas."""
    if conteudo[:4] == b"PK\x03\x04":  # .xlsx
        import openpyxl

        try:
            livro = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        except Exception as exc:  # zip quebrado, outro formato
            raise PlanilhaInvalida("nao consegui abrir o .xlsx.") from exc
        return [
            [[_celula(v) for v in linha] for linha in aba.iter_rows(values_only=True)]
            for aba in livro.worksheets
        ]
    for codificacao in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PlanilhaInvalida("nao consegui ler o arquivo (use .csv ou .xlsx).")
    try:
        # O cabecalho pode nao estar na 1a linha: o dialeto sai do meio do arquivo.
        dialeto = csv.Sniffer().sniff(texto[:8000], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    return [[[c.strip() for c in linha] for linha in csv.reader(io.StringIO(texto), dialeto)]]


def mapear(cabecalho: list[str], colunas: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """{campo: indice}. Nome igual ganha de nome que so comeca igual ("Cost"
    antes de "Cost / conv."); entre os nomes, vale a ordem da tupla."""
    chaves = [chave(c) for c in cabecalho]
    saida: dict[str, int] = {}
    usados: set[int] = set()
    for campo, nomes in colunas.items():
        achado = next(
            (i for nome in nomes for i, k in enumerate(chaves) if k == nome and i not in usados),
            None,
        )
        if achado is None:
            achado = next(
                (
                    i
                    for nome in nomes
                    for i, k in enumerate(chaves)
                    if k.startswith(nome) and i not in usados
                ),
                None,
            )
        if achado is not None:
            saida[campo] = achado
            usados.add(achado)
    return saida


def tabelas(abas: list[list[list[str]]], colunas: dict, aceita) -> list[tuple[list, list]]:
    """Em cada aba, o primeiro cabecalho (nas primeiras linhas) com o qual
    `aceita(mapa)` concorda: [(cabecalho, linhas abaixo dele)]."""
    saida = []
    for linhas in abas:
        for i, linha in enumerate(linhas[:LINHAS_ATE_O_CABECALHO]):
            if aceita(mapear(linha, colunas)):
                saida.append((linha, linhas[i + 1 :]))
                break
    return saida


def campo(linha: list[str], mapa: dict[str, int], nome: str) -> str:
    i = mapa.get(nome)
    return linha[i].strip() if i is not None and i < len(linha) and linha[i] else ""


def em_ingles(cabecalho: list[str]) -> bool:
    palavras = set(" ".join(chave(c) for c in cabecalho).split())
    return bool(palavras & {"date", "impressions", "clicks", "likes", "spent", "cost", "reach"})
