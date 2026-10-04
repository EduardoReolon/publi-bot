"""As redes que o PubliBot conhece. Rede nova: um modulo com REDE (formato,
estilo, publicador, oauth) e uma linha aqui."""

from __future__ import annotations

from apps.social.redes import gmn, instagram, linkedin
from apps.social.redes.base import Rede

REDES: dict[str, Rede] = {r.codigo: r for r in (linkedin.REDE, instagram.REDE, gmn.REDE)}


def rede(codigo: str) -> Rede:
    return REDES[codigo]


def escolhas() -> list[tuple[str, str]]:
    return [(r.codigo, r.nome) for r in REDES.values()]
