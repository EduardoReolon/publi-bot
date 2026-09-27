"""Rotulo legivel das parcelas da nota, para qualquer tela que as mostre."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def rotulo_da_parcela(chave: str) -> str:
    from apps.radar.models import GrupoDeDemanda

    return str(GrupoDeDemanda.ROTULOS_DAS_PARCELAS.get(chave, chave))
