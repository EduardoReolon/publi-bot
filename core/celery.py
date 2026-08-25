"""Aplicacao Celery do PubliBot.

Ponto critico deste arquivo: a classe usada NAO e a `Celery` do celery, e sim
a `CeleryApp` do tenant-schemas-celery.

Por que isso importa: uma task nao tem request HTTP, entao o
`TenantMainMiddleware` nunca roda dentro de um worker. Sem essa troca de
classe, toda task executa contra o schema `public` — e o resultado nao e um
erro barulhento, e gravar dado de um cliente no lugar errado. A `CeleryApp`
carrega o `_schema_name` no cabecalho da mensagem no momento do despacho e
restaura o search_path antes e depois de cada execucao.
"""

from __future__ import annotations

import os

from tenant_schemas_celery.app import CeleryApp

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")

app = CeleryApp("publibot")

# Todas as chaves CELERY_* do settings viram configuracao do Celery.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks.py em cada app instalado.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self) -> str:
    """Task de fumaca. Retorna o schema em que esta rodando.

    Serve para verificar, com uma chamada, se a propagacao de tenant esta de
    pe: despachada de dentro de um tenant, tem que responder o schema daquele
    tenant, nunca "public".
    """
    from django.db import connection

    return connection.schema_name
