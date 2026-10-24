"""Estudos de mercado: buscas no Google por regiao, em rodadas com outra IA.

Uma bancada do dono (so superusuario, so no tenant marcado em
`Tenant.estudos`): descreve uma empresa, escolhe a cidade e o raio, e vai
conversando com a outra IA. Cada rodada busca na DataForSEO, por estimativa
(estado x populacao, barata) ou pelo valor real de cada cidade; o historico
fica inteiro. Ver docs/ESTUDOS.md.
"""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EstudosConfig(AppConfig):
    name = "apps.estudos"
    verbose_name = _("Estudos de mercado")
    default_auto_field = "django.db.models.BigAutoField"

    # --- Extensoes do nucleo (apps/ops/extensoes.py) -------------------------
    urls = ("estudos/", "apps.estudos.urls", "estudos")
    menu = [
        {
            "rotulo": _("Estudos"),
            "url": "estudos:inicio",
            "aba": "estudos",
            "visivel": "apps.estudos.acesso.pode_ver",
        },
    ]
