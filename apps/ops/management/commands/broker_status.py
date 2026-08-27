"""Diz com qual broker este processo fala e o que ha na fila.

Serve para responder, sem adivinhacao, a pergunta que aparece quando um tenant
nao sai de "provisionando": a mensagem chegou na fila? Alguem consumiu?

A distincao que importa e entre "a fila esta vazia" e "nao consegui ler a
fila". A primeira significa que existe um worker; a segunda, que o broker nao
esta respondendo. As duas se parecem quando a unica evidencia e a tela parada.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ops.broker import FILA_PADRAO, mensagens_pendentes


class Command(BaseCommand):
    help = "Mostra o broker configurado e quantas mensagens estao esperando um worker."

    def add_arguments(self, parser):
        parser.add_argument("--fila", default=FILA_PADRAO, help=f"Default: {FILA_PADRAO}.")

    def handle(self, *args, **options):
        fila = options["fila"]

        self.stdout.write(f"BROKER_BACKEND : {settings.BROKER_BACKEND}")
        self.stdout.write(f"broker         : {_sem_segredo(settings.CELERY_BROKER_URL)}")
        self.stdout.write(f"fila           : {fila}")

        pendentes = mensagens_pendentes(fila)

        if pendentes is None:
            self.stdout.write(
                self.style.ERROR(
                    "\nNao foi possivel LER a fila — o broker nao respondeu.\n"
                    "  Suba o Redis, ou use BROKER_BACKEND=postgres para usar o\n"
                    "  mesmo PostgreSQL da aplicacao (ADR-0013)."
                )
            )
            return

        self.stdout.write(f"pendentes      : {pendentes}")

        if pendentes == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nFila vazia. Se ainda assim algo nao anda, o problema esta no\n"
                    "worker (lento, ou caindo no meio) — o log dele e o proximo lugar."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"\n{pendentes} mensagem(ns) esperando. Se este numero nao cai, nenhum\n"
                "worker esta consumindo ESTA fila. Duas causas, nesta ordem:\n"
                "\n"
                "  1. Nao ha worker rodando. Suba os dois processos juntos com:\n"
                "         python manage.py dev\n"
                "     ou, em outro terminal:\n"
                "         celery -A core worker -l INFO --concurrency=1 --prefetch-multiplier=1\n"
                "\n"
                "  2. O worker esta em OUTRO broker. Compare a linha `transport:` do\n"
                "     banner dele com o `broker` acima — tem de ser o mesmo. O\n"
                "     BROKER_BACKEND e lido do .env por cada processo na hora em que\n"
                "     ele sobe, entao um terminal aberto antes de voce editar o .env\n"
                "     continua no broker antigo."
            )
        )


def _sem_segredo(url: str) -> str:
    if "@" not in url:
        return url
    esquema, _, resto = url.partition("://")
    _, _, host = resto.rpartition("@")
    return f"{esquema}://***@{host}"
