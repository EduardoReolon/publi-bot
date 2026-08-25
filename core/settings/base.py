"""Configuracao comum a todos os ambientes.

Nao importe este modulo diretamente: use `core.settings.dev` ou
`core.settings.prod`, que definem DEBUG e as politicas de seguranca.

Decisoes registradas em docs/adr/ — em especial ADR-0003 (schema por tenant),
ADR-0006 (usuarios compartilhados no public) e ADR-0009 (ingles no codigo,
pt-BR na interface).
"""

from __future__ import annotations

from pathlib import Path

from core import env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env.load_env_file(BASE_DIR)

# ---------------------------------------------------------------------------
# Identidade
# ---------------------------------------------------------------------------
# Nome canonico do projeto (ADR-0002). Vira prefixo de chave no Redis, nome do
# app do Celery, nome de unit do systemd e namespace do contrato de API.
PROJECT_SLUG = "publibot"

# Dominio raiz do SaaS. A home fica no apex; cada tenant num subdominio.
# Em desenvolvimento use "localhost": navegadores resolvem qualquer
# *.localhost para 127.0.0.1 sem precisar editar /etc/hosts.
ROOT_DOMAIN = env.get("ROOT_DOMAIN", "localhost")

# ---------------------------------------------------------------------------
# Seguranca
# ---------------------------------------------------------------------------
SECRET_KEY = env.require("DJANGO_SECRET_KEY")

DEBUG = False

ALLOWED_HOSTS: list[str] = env.csv_list("DJANGO_ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Aplicacoes — divididas por schema (django-tenants)
# ---------------------------------------------------------------------------
# SHARED_APPS  -> tabelas criadas SOMENTE no schema `public`
# TENANT_APPS  -> tabelas replicadas em CADA schema de tenant
#
# Dentro de um tenant o search_path e "<schema>, public, extensions", entao as
# tabelas compartilhadas continuam visiveis de dentro do tenant. E por isso que
# os usuarios podem morar so no public e ainda assim serem alcancaveis por uma
# FK vinda de uma tabela do tenant.

SHARED_APPS = [
    # Precisa ser o primeiro: registra o backend e os comandos de schema.
    "django_tenants",
    # Tenant, Domain, User e TenantMembership (ADR-0006).
    "apps.accounts",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    # O agendamento vive no public: as entradas fixas de infraestrutura sao do
    # sistema, nao de um cliente. A cadencia por tenant fica em outra tabela.
    "django_celery_beat",
]

TENANT_APPS = [
    # Necessario dentro do schema para o admin e as permissoes por tenant
    # resolverem corretamente.
    "django.contrib.contenttypes",
    # Os apps de dominio entram aqui conforme forem construidos:
    #   "apps.knowledge",
    #   "apps.content",
    #   "apps.integrations",
    #   "apps.ops",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

TENANT_MODEL = "accounts.Tenant"
TENANT_DOMAIN_MODEL = "accounts.Domain"

# A extensao `vector` do pgvector e criada UMA VEZ no schema `extensions` e
# alcancada por todos os tenants via search_path. Sem isto, a migration do
# segundo tenant falha com: type "vector" does not exist.
PG_EXTRA_SEARCH_PATHS = ["extensions"]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    # Precisa vir antes de tudo: resolve o subdominio -> tenant e fixa o
    # search_path da conexao para o resto da request.
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# O roteamento difere entre a home (apex) e um tenant (subdominio): a home tem
# cadastro e landing; o tenant tem o painel.
ROOT_URLCONF = "core.urls_tenants"
PUBLIC_SCHEMA_URLCONF = "core.urls_public"

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
# O ENGINE e o backend do django-tenants, nao o do Django: e ele que emite o
# `SET search_path` a cada uso de conexao.
DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": env.get("POSTGRES_DB", PROJECT_SLUG),
        "USER": env.get("POSTGRES_USER", PROJECT_SLUG),
        "PASSWORD": env.require("POSTGRES_PASSWORD"),
        "HOST": env.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": env.integer("POSTGRES_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# Direciona cada migration para o schema certo (public vs. tenant).
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Autenticacao
# ---------------------------------------------------------------------------
# Trocar isto depois do primeiro migrate exige cirurgia manual em
# django_content_type e auth_permission. Ver ADR-0006.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Internacionalizacao (ADR-0009)
# ---------------------------------------------------------------------------
# Codigo, nomes de campo e valores de choices em ingles; interface traduzida.
# O `locale/` existe desde o primeiro commit porque retrofitar gettext num
# painel ja escrito custa revarrer template por template.
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("pt-br", "Portugues (Brasil)"),
    ("en", "English"),
    ("it", "Italiano"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Arquivos estaticos e de midia
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cada tenant grava em MEDIA_ROOT/<schema_name>/... automaticamente. Com "%s"
# o schema_name e interpolado; trocar para S3 depois e so trocar a storage,
# mantendo o mesmo prefixo por tenant.
MULTITENANT_RELATIVE_MEDIA_ROOT = "%s"

STORAGES = {
    "default": {
        "BACKEND": "django_tenants.files.storage.TenantFileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Teto de upload. PDFs cientificos passam facil de 10 MB.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.integer("DATA_UPLOAD_MAX_MEMORY_SIZE", 52_428_800)
FILE_UPLOAD_MAX_MEMORY_SIZE = 5_242_880

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# O default do Celery e 3 tentativas saturando em 10 minutos — bem menos do que
# a arquitetura promete. Estes valores dao ~300s, 600s, 1200s... saturando em
# 6h, o que cobre cerca de 3 dias de indisponibilidade de um No Final.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_DEFAULT_RETRY_DELAY = 300
CELERY_TASK_MAX_RETRIES = 20

# O default e 4: mesmo com --concurrency=1 o worker reserva 4 mensagens, e as
# 3 extras ficam presas contando para o visibility_timeout.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# O default do transporte Redis e 3600s: uma inferencia mais longa que isso faz
# o broker REENTREGAR a mesma task. Isto e paliativo — a garantia real vem do
# GenerationJob no banco, que e a fonte da verdade.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": env.integer("CELERY_VISIBILITY_TIMEOUT", 86_400),
}

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ---------------------------------------------------------------------------
# Interruptor geral de publicacao
# ---------------------------------------------------------------------------
# Checado no inicio de toda task que faz POST para um No Final. E a unica forma
# de parar uma publicacao equivocada sem derrubar servico.
PUBLISHING_ENABLED = env.boolean("PUBLISHING_ENABLED", True)

# Monta o payload completo e grava a tentativa, mas nao faz o POST. E o unico
# jeito seguro de validar o contrato contra um site real em desenvolvimento.
PUBLISH_DRY_RUN = env.boolean("PUBLISH_DRY_RUN", False)

# ---------------------------------------------------------------------------
# Recuperacao (RAG)
# ---------------------------------------------------------------------------
# multilingual-e5-large: 1024 dimensoes, MIT, ~100 idiomas, roda em CPU na
# nuvem via ONNX. Mesma dimensao do bge-m3, entao trocar de modelo depois nao
# exige ALTER TYPE nem recriar o indice HNSW — so re-embutir o corpus.
EMBEDDING_MODEL = env.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EMBEDDING_DIM = env.integer("EMBEDDING_DIM", 1024)

# O modelo trunca em 512 tokens SEM AVISO. Este teto e validado com o
# tokenizer real na curadoria, nunca por contagem de caracteres.
EMBEDDING_MAX_TOKENS = env.integer("EMBEDDING_MAX_TOKENS", 480)

# Calibrar empiricamente antes de confiar: distancia de cosseno nao e
# comparavel entre modelos diferentes.
RAG_TOP_K = env.integer("RAG_TOP_K", 3)
RAG_MAX_COSINE_DISTANCE = env.decimal("RAG_MAX_COSINE_DISTANCE", 0.35)

# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": env.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        PROJECT_SLUG: {"level": env.get("LOG_LEVEL", "INFO"), "propagate": True},
    },
}

DEFAULT_FROM_EMAIL = env.get("DEFAULT_FROM_EMAIL", f"nao-responda@{ROOT_DOMAIN}")
