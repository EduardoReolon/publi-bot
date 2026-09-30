"""Rotulo legivel das parcelas da nota, para qualquer tela que as mostre."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def rotulo_da_parcela(chave: str) -> str:
    from apps.radar.models import GrupoDeDemanda

    return str(GrupoDeDemanda.ROTULOS_DAS_PARCELAS.get(chave, chave))


@register.filter
def numero_curto(valor) -> str:
    """12345 -> "12 mil"; 2300000 -> "2,3 mi". Vazio para None."""
    if valor in (None, ""):
        return ""
    numero = int(valor)
    if numero >= 1_000_000:
        return f"{numero / 1_000_000:.1f}".replace(".", ",").removesuffix(",0") + " mi"
    if numero >= 1_000:
        return f"{numero // 1_000} mil"
    return str(numero)


@register.filter
def duracao(segundos) -> str:
    if not segundos:
        return ""
    minutos, resto = divmod(int(segundos), 60)
    horas, minutos = divmod(minutos, 60)
    return f"{horas}h{minutos:02d}" if horas else f"{minutos}min{resto:02d}"


@register.filter
def porcentagem_de(parte, todo) -> str:
    """curtidas|porcentagem_de:visualizacoes -> "4,4%"."""
    if not parte or not todo:
        return ""
    return f"{100 * int(parte) / int(todo):.1f}".replace(".", ",") + "%"
