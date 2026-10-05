"""Redes sociais: um modulo a parte, ligado ao nucleo por extensoes.

O nucleo nao sabe que este modulo existe (ver apps/ops/extensoes.py): tira-lo
de INSTALLED_APPS tira o menu, as rotas, os prompts e o bloco no artigo. Por
dentro, tudo o que ele le do nucleo passa por `apps/social/fontes.py` — a
unica porta. Os testes de `apps/social/tests/test_fronteira.py` conferem as
duas regras.
"""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SocialConfig(AppConfig):
    name = "apps.social"
    verbose_name = _("Redes sociais")
    default_auto_field = "django.db.models.BigAutoField"

    # --- Extensoes do nucleo (apps/ops/extensoes.py) -------------------------
    urls = ("redes/", "apps.social.urls", "social")
    menu = [
        {"rotulo": _("Redes"), "url": "social:inicio", "aba": "redes", "pendencia": "redes"},
    ]
    prompts_iniciais = "apps.social.prompts.PROMPTS"
    prompts_com_guia = frozenset({"social_linkedin", "social_instagram", "social_gmn"})
    blocos_do_artigo = ["social/_no_artigo.html"]
    pendencias = "apps.social.painel.pendencias"
    alertas = "apps.social.painel.alertas"
