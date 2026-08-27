"""Executor de migrations que ignora tenants sem schema fisico.

O `migrate_schemas` do django-tenants trata os dois caminhos de forma
diferente. Com `--schema=<nome>` ele confere antes:

    if not schema_exists(self.schema_name, ...):
        raise RuntimeError('Schema "{}" does not exist')

Sem `--schema`, migrando todos, ele nao confere nada: pega TODA linha de
`accounts_tenant` fora do public e manda migrar. Para um projeto com
`auto_create_schema = True` isso e coerente — a linha so existe depois do
schema. Aqui nao: o ADR-0001 desliga essa flag justamente para o schema nascer
numa task, depois da request de cadastro. Entre o cadastro e o worker terminar,
a linha existe e o schema nao.

O efeito e desproporcional. Um unico tenant nessa janela — ou um cujo
provisionamento falhou — derruba o `migrate_schemas` inteiro, e o erro nao
menciona nem o tenant nem a causa: o search_path aponta para um schema
inexistente, o `CREATE TABLE django_migrations` cai em qualquer coisa que
sobrar no caminho e o Postgres reclama do que encontrar por ultimo. Duas
mensagens ja vistas para a MESMA causa:

    Unable to create the django_migrations table (nenhum esquema foi
    selecionado para cria-lo(a)

    Unable to create the django_migrations table (permission denied for
    schema extensions

Nao da para sobrescrever o comando: o Django resolve nomes de comando pela
ordem de INSTALLED_APPS e o `django_tenants` precisa vir antes de `apps`. O
`GET_EXECUTOR_FUNCTION` e o ponto de extensao que a propria biblioteca oferece.
"""

from __future__ import annotations

import sys

from django_tenants.migration_executors import get_executor as _get_executor_original
from django_tenants.utils import schema_exists


class IgnoraSchemaAusente:
    """Filtra a lista de tenants antes de migrar, e diz o que ficou de fora.

    Pular em silencio seria pior que estourar: alguem leria "migrations
    aplicadas" e concluiria que os tenants estao todos em dia.
    """

    def _existentes(self, tenants: list) -> list:
        presentes, ausentes = [], []
        for item in tenants or []:
            # No modo multi-tipo cada item e (schema_name, tipo).
            nome = item[0] if isinstance(item, (tuple, list)) else item
            (presentes if schema_exists(nome) else ausentes).append(item)

        if ausentes:
            nomes = ", ".join(str(i[0] if isinstance(i, (tuple, list)) else i) for i in ausentes)
            sys.stderr.write(
                f"Ignorando {len(ausentes)} tenant(s) sem schema no banco: {nomes}.\n"
                f"  Sao registros criados no cadastro cujo provisionamento ainda nao\n"
                f"  terminou (ADR-0001). Para criar o schema agora:\n"
                f"      python manage.py provision_tenant <schema_name>\n"
            )
        return presentes

    def run_migrations(self, tenants=None):
        return super().run_migrations(tenants=self._existentes(tenants))

    def run_multi_type_migrations(self, tenants):
        return super().run_multi_type_migrations(tenants=self._existentes(list(tenants)))


def get_executor(codename=None):
    """Devolve o executor pedido, com o filtro por cima.

    A subclasse e montada aqui em vez de escrita a mao porque o codename e
    escolhido em tempo de execucao (`standard`, `multiprocessing`,
    `subprocess`) e o filtro vale para todos igualmente.
    """
    base = _get_executor_original(codename)
    return type(f"{base.__name__}SemSchemaAusente", (IgnoraSchemaAusente, base), {})
