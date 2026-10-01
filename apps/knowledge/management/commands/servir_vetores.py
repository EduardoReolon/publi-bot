"""Sobe o servico de vetores (`apps.knowledge.servico_de_vetores`).

Roda como servico do systemd (`vetores-publibot.service`); nao e por tenant —
o modelo e o mesmo para todos:

    manage.py servir_vetores
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.knowledge.servico_de_vetores import endereco, servir


class Command(BaseCommand):
    help = "Sobe o servico de vetores: um processo so com o modelo de embedding."

    def handle(self, *args, **opcoes):
        host, porta = endereco()
        self.stdout.write(f"Servico de vetores em http://{host}:{porta} (Ctrl+C para parar).")
        servir()
