"""Configuracao para exercitar o contrato contra a implementacao de referencia.

O no de referencia (`docs/contrato/reference/django/publibot_node`) e um projeto
independente: quem for implementar o contrato instala aquele app no proprio
site, nao neste. Para testar os dois lados conversando de verdade, ele e
hospedado aqui temporariamente.

Isso e o que permite verificar a classe de defeito que mais importa neste
contrato: os dois lados calcularem a assinatura de forma diferente, ou
discordarem sobre o que conta como idempotencia. Testar so um lado deixaria
isso passar.

**Sem multi-tenancy de proposito.** O site que recebe conteudo e um site comum;
supor que ele tenha schema por tenant seria testar algo que o contrato nao
exige — e o TenantMainMiddleware devolveria 404 para qualquer host nao
cadastrado.

Uso:

    pytest tests/test_contrato_ponta_a_ponta.py --ds=core.settings.test_contract
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

CAMINHO_DA_REFERENCIA = BASE_DIR / "docs" / "contrato" / "reference" / "django"
if str(CAMINHO_DA_REFERENCIA) not in sys.path:
    sys.path.insert(0, str(CAMINHO_DA_REFERENCIA))

from core import env  # noqa: E402

env.load_env_file(BASE_DIR)

SECRET_KEY = "apenas-para-o-teste-de-contrato"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "publibot_node",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "core.urls_contract_test"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.get("POSTGRES_DB", "publibot"),
        "USER": env.get("POSTGRES_USER", "publibot"),
        "PASSWORD": env.require("POSTGRES_PASSWORD"),
        "HOST": env.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env.get("POSTGRES_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# Credenciais do teste.
PUBLIBOT_API_KEY = "chave-de-api-do-teste"
PUBLIBOT_SIGNING_SECRET = "segredo-de-assinatura-do-teste"
PUBLIBOT_NODE_SITE_TITLE = "Site de teste"
PUBLIBOT_NODE_PUBLIC_URL = "https://exemplo.com.br"
PUBLIBOT_NODE_HOME_TEXT = "Texto da home do site de teste."

# O nonce so pode ser aceito uma vez. Com cache em memoria do processo, o
# comportamento e o mesmo do Redis para o que este teste verifica.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "contrato",
    }
}
