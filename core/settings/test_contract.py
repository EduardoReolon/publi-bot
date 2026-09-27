"""Configuracao para exercitar o contrato contra o lado do site.

O lado do site e o pacote `publi-bot-core-django` (app `publibot_core`,
repositorio EduardoReolon/publi-bot-core-django), instalado pelo
requirements-dev. Para testar os dois lados conversando de verdade, ele e
hospedado aqui durante os testes.

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

from pathlib import Path

from core import env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env.load_env_file(BASE_DIR)

SECRET_KEY = "apenas-para-o-teste-de-contrato"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "publibot_core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "core.urls_contract_test"

# Para as tags de template do no (bloco da chamada).
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]

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

# A rota de fotos grava arquivo. Diretorio temporario proprio: sem isto os
# arquivos do teste iriam parar na pasta de midia do projeto.
import tempfile  # noqa: E402

MEDIA_ROOT = tempfile.mkdtemp(prefix="publibot-contrato-")
MEDIA_URL = "/media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# Credenciais do teste.
PUBLIBOT_API_KEY = "chave-de-api-do-teste"
PUBLIBOT_SIGNING_SECRET = "segredo-de-assinatura-do-teste"
PUBLIBOT_SITE_TITLE = "Site de teste"
PUBLIBOT_PUBLIC_URL = "https://exemplo.com.br"
PUBLIBOT_HOME_TEXT = "Texto da home do site de teste."
# O cliente de teste fala HTTPS (ClienteSeguro): a exigencia fica ligada.
PUBLIBOT_REQUIRE_HTTPS = True

# O nonce so pode ser aceito uma vez. Com cache em memoria do processo, o
# comportamento e o mesmo do Redis para o que este teste verifica.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "contrato",
    }
}
