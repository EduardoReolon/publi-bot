"""Tags para o template do site mostrar a chamada e ligar a medicao.

    {% load publibot_node %}
    <article data-publibot-id="{{ publicacao.id }}">
      {% corpo_com_chamada publicacao %}
      {% chamada_no_fim publicacao %}
    </article>

O bloco vem de `publibot_node/chamada.html`. Sobrescreva esse template no seu
projeto com o texto, o botao e o link da sua oferta.
"""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

register = template.Library()


def _bloco(publicacao, onde: str) -> str:
    return render_to_string("publibot_node/chamada.html", {"publicacao": publicacao, "onde": onde})


@register.simple_tag
def corpo_com_chamada(publicacao):
    """O corpo, com o bloco no lugar da marca quando o artigo pede (inline)."""
    # O html_content ja foi sanitizado ao ser recebido.
    return mark_safe(publicacao.html_com_chamada(_bloco(publicacao, "meio")))  # noqa: S308


@register.simple_tag
def chamada_no_fim(publicacao):
    """O bloco no fim, para `end` e `inline`; nada para `none`."""
    if not publicacao.chamada_no_fim:
        return ""
    return mark_safe(_bloco(publicacao, "fim"))  # noqa: S308
