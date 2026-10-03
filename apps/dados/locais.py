"""Recorte geografico: Brasil ou um estado.

O nome do estado e a forma canonica guardada em `Valor.local` ("Parana" sai
"Paraná"); cada adaptador traduz para o codigo da instituicao dele. O estado
do site vem das regioes do Radar: todas no mesmo estado -> aquele estado.
"""

from __future__ import annotations

import unicodedata

BRASIL = "Brasil"

UFS = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia",
    "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}  # fmt: skip


def _chave(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


_POR_NOME = {_chave(nome): nome for nome in UFS.values()}


def normalizar(local: str) -> str:
    """ "pr", "Parana", "Paraná" -> "Paraná"; vazio ou "brasil" -> "Brasil"."""
    texto = (local or "").strip()
    if not texto or _chave(texto) in {"brasil", "brazil"}:
        return BRASIL
    if texto.upper() in UFS:
        return UFS[texto.upper()]
    return _POR_NOME.get(_chave(texto), texto)


def sigla(local: str) -> str:
    nome = normalizar(local)
    return next((s for s, n in UFS.items() if n == nome), "")


def estado_do_site() -> str:
    """O estado do site, pelas regioes do Radar; "" se nao ha um so."""
    from apps.radar.models import ConfiguracaoDoRadar

    estados = set()
    for regiao in ConfiguracaoDoRadar.carregar().regioes or []:
        partes = [p.strip() for p in str(regiao.get("nome") or "").split(",")]
        achados = {_POR_NOME[_chave(p)] for p in partes if _chave(p) in _POR_NOME}
        if not achados:
            return ""  # uma regiao fora de um estado conhecido: nao arrisca
        estados |= achados
    return estados.pop() if len(estados) == 1 else ""


def recortes_do_site() -> list[str]:
    """Onde buscar o valor: o estado do site (se ha um so) e o Brasil."""
    estado = estado_do_site()
    return [estado, BRASIL] if estado else [BRASIL]
