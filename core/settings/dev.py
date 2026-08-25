"""Desenvolvimento local.

Sobe contra o Postgres e o Redis do compose.yaml. Nada aqui deve jamais ser
importado em producao.
"""

from __future__ import annotations

from core import env
from core.settings.base import *
from core.settings.base import ROOT_DOMAIN

DEBUG = True

# Em dev qualquer subdominio de .localhost resolve para 127.0.0.1 sem editar
# /etc/hosts. O ponto inicial faz o Django aceitar os subdominios tambem.
ALLOWED_HOSTS = [
    ROOT_DOMAIN,
    f".{ROOT_DOMAIN}",
    "127.0.0.1",
    "localhost",
    ".localhost",
]

# O cookie precisa valer para o apex E para os subdominios, senao a sessao
# criada no cadastro (na home) nao acompanha o usuario ate o tenant dele.
SESSION_COOKIE_DOMAIN = f".{ROOT_DOMAIN}"
CSRF_TRUSTED_ORIGINS = [
    f"http://{ROOT_DOMAIN}:8000",
    f"http://*.{ROOT_DOMAIN}:8000",
]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Executa a task no processo, sincronamente. Use SOMENTE para depurar: mascara
# todo problema de serializacao e de propagacao de schema entre processos.
CELERY_TASK_ALWAYS_EAGER = env.boolean("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
