"""Caixa de ideias: um modulo a parte, ligado ao nucleo por extensoes.

A pessoa manda uma ideia como ela vem (texto ou audio): "dizem X, eu acho Y".
O modelo separa a afirmacao, a tese e as buscas; o PubliBot busca o discurso,
a evidencia a favor e — com mais esforco — a evidencia contra; tudo passa
pela curadoria, e a ideia vira pauta (com o bloco do debate) e, se quiser,
post de redes. Ver docs/CAIXA_DE_IDEIAS.md.
"""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IdeiasConfig(AppConfig):
    name = "apps.ideias"
    verbose_name = _("Caixa de ideias")
    default_auto_field = "django.db.models.BigAutoField"

    # --- Extensoes do nucleo (apps/ops/extensoes.py) -------------------------
    urls = ("ideias/", "apps.ideias.urls", "ideias")
    menu = [
        {
            "rotulo": _("Ideias"),
            "url": "ideias:inicio",
            "aba": "ideias",
            "pendencia": "ideias",
        },
    ]
    prompts_iniciais = "apps.ideias.prompts.PROMPTS"
    pendencias = "apps.ideias.views.pendencias"
