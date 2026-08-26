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
#
# ATENCAO ao valor de ROOT_DOMAIN em desenvolvimento: um dominio de rotulo
# unico NAO funciona aqui. Verificado com o Chromium: ao receber
# `Set-Cookie: ...; Domain=.localhost`, o navegador DESCARTA o atributo Domain
# e grava o cookie como host-only, porque `localhost` e tratado como sufixo
# publico (um TLD) e cookies abrangentes em sufixo publico sao recusados.
#
# O efeito e silencioso e confuso: o login funciona no apex, o cookie aparece
# no navegador, e mesmo assim o subdominio do tenant devolve a tela de login.
#
# Por isso o padrao de desenvolvimento e `publibot.localhost`, com dois
# rotulos: o cookie `.publibot.localhost` e aceito, e navegadores resolvem
# qualquer `*.localhost` para 127.0.0.1 sem precisar editar /etc/hosts.
if "." in ROOT_DOMAIN:
    SESSION_COOKIE_DOMAIN = f".{ROOT_DOMAIN}"
else:
    # Host-only. O login nao atravessa subdominios, mas ao menos funciona
    # dentro de cada um, em vez de falhar sem explicacao.
    SESSION_COOKIE_DOMAIN = None

# A porta entra no CSRF_TRUSTED_ORIGINS porque o Django compara a origem
# completa (esquema, host E porta). Deixa-la fixa em 8000 fazia todo POST
# retornar 400 para quem subisse o runserver em outra porta — sem nenhuma
# mensagem que apontasse a porta como causa.
DEV_SERVER_PORT = env.get("DEV_SERVER_PORT", "8000")

CSRF_TRUSTED_ORIGINS = [
    f"http://{ROOT_DOMAIN}:{DEV_SERVER_PORT}",
    f"http://*.{ROOT_DOMAIN}:{DEV_SERVER_PORT}",
    # Sem porta, para quando o servico roda atras de um proxy na 80.
    f"http://{ROOT_DOMAIN}",
    f"http://*.{ROOT_DOMAIN}",
]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Executa a task no processo, sincronamente. Use SOMENTE para depurar: mascara
# todo problema de serializacao e de propagacao de schema entre processos.
CELERY_TASK_ALWAYS_EAGER = env.boolean("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
