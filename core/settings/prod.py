"""Producao.

Tudo aqui pressupoe HTTPS terminado no Nginx, com X-Forwarded-Proto repassado.
"""

from __future__ import annotations

from core import env
from core.settings.base import *
from core.settings.base import ROOT_DOMAIN

DEBUG = False

# Um processo so abre o modelo de embedding (vetores-publibot.service); os
# outros pedem a ele. Ver `ServicoDeVetoresClient`.
EMBEDDING_CLIENT = env.get("EMBEDDING_CLIENT", "apps.knowledge.embeddings.ServicoDeVetoresClient")

# Sem default: se a variavel faltar, o processo nao sobe. Um ALLOWED_HOSTS
# vazio em producao e uma falha de configuracao, nao um caso a tolerar.
ALLOWED_HOSTS = env.csv_list("DJANGO_ALLOWED_HOSTS") or [ROOT_DOMAIN, f".{ROOT_DOMAIN}"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_DOMAIN = f".{ROOT_DOMAIN}"
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False
CSRF_TRUSTED_ORIGINS = [f"https://{ROOT_DOMAIN}", f"https://*.{ROOT_DOMAIN}"]

X_FRAME_OPTIONS = "DENY"

# Opcional: o sistema nao manda e-mail em nenhum fluxo hoje. Sem EMAIL_HOST, o
# que o Django tentar enviar (erro para ADMINS, por exemplo) sai no log.
EMAIL_HOST = env.get("EMAIL_HOST", "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_PORT = env.integer("EMAIL_PORT", 587)
EMAIL_HOST_USER = env.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env.boolean("EMAIL_USE_TLS", True)

# Em producao so o Docling converte PDF. Aceitar o caminho local aqui
# significaria indexar texto de coluna dupla embaralhado e publicar citando uma
# fonte cujo conteudo foi lido errado — sem nada no site indicando isso.
PERMITIR_EXTRACAO_LOCAL = env.boolean("PERMITIR_EXTRACAO_LOCAL", False)
