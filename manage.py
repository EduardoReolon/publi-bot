#!/usr/bin/env python
"""Utilitario de linha de comando do Django.

Atencao aos comandos de schema (django-tenants):

    migrate_schemas --shared    aplica migrations SOMENTE no schema public
    migrate_schemas             aplica em public e em todos os tenants
    create_tenant               cria um tenant e roda as migrations nele
    tenant_command <cmd> --schema=<nome>   roda um comando dentro de um tenant

`manage.py migrate` puro NAO percorre os schemas dos tenants.
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Nao foi possivel importar o Django. Ele esta instalado e o virtualenv esta ativo?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
