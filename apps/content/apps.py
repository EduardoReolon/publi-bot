from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content"
    label = "content"
    verbose_name = _("Conteudo")

    def ready(self) -> None:
        # Importar registra os fluxos no orquestrador. Sem isto, `criar_job`
        # levanta KeyError e nada e gerado — foi exatamente o estado em que o
        # projeto ficou por sete entregas: passos escritos e testados, nenhum
        # deles alcancavel.
        from apps.content import flows  # noqa: F401
