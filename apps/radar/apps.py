from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RadarConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.radar"
    label = "radar"
    verbose_name = _("Radar de pautas")
