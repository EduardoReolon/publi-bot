from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class InferenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inference"
    label = "inference"
    verbose_name = _("Inferencia")
