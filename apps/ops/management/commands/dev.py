"""Sobe o servidor web e o worker do Celery juntos, em desenvolvimento.

Este comando existe por um motivo empirico: a separacao em dois processos e
correta, e mesmo assim tropeca. Um cadastro de tenant depende do worker
(ADR-0001), e sem ele nada falha — a mensagem e publicada, fica na fila, e a
tela espera para sempre. Quem esta comecando roda `runserver`, ve o servidor
de pe e conclui, com razao, que o sistema esta rodando.

Nao ha supervisor de processo aqui de proposito. Os dois filhos herdam o
terminal e NAO ganham grupo de processo proprio, entao o Ctrl+C do console
chega aos dois de uma vez — no Linux e no macOS via SIGINT ao grupo em
primeiro plano, no Windows via CTRL_C_EVENT aos processos ligados ao console.
E o comportamento que ja se espera de um terminal, sem codigo para mante-lo.

Em producao nada disto se aplica: la sao units separadas do systemd, cada uma
com seu ciclo de vida (`deploy/systemd/`).
"""

from __future__ import annotations

import subprocess
import sys
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Roda o servidor web e o worker do Celery no mesmo terminal (so em DEBUG)."

    def add_arguments(self, parser):
        parser.add_argument(
            "addrport",
            nargs="?",
            default=f"127.0.0.1:{getattr(settings, 'DEV_SERVER_PORT', '8000')}",
            help="Endereco do servidor web. Default: 127.0.0.1:<DEV_SERVER_PORT>.",
        )
        parser.add_argument(
            "--sem-worker",
            action="store_true",
            help="Sobe so o servidor web, como o runserver puro.",
        )
        parser.add_argument(
            "--concurrency", type=int, default=1, help="Processos do worker. Default: 1."
        )
        parser.add_argument(
            "--sem-conferir",
            action="store_true",
            help="Pula o check_db do banco antes de subir.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            # O `dev` prende os dois no mesmo terminal: se um cai, o outro vai
            # junto. Em producao isso significaria derrubar o site porque o
            # worker morreu.
            raise CommandError(
                "O comando `dev` e so para desenvolvimento (DEBUG=True). "
                "Em producao use as units do systemd em deploy/systemd/."
            )

        if not options["sem_conferir"]:
            # Conferir antes de subir, e nao depois: sem a extensao `vector` o
            # servidor sobe normalmente, o cadastro e aceito, e a falha so
            # aparece dentro da task do worker, como um traceback de
            # `CREATE TABLE` que nao menciona extensao nenhuma.
            try:
                call_command("check_db")
            except SystemExit as exc:
                raise CommandError(
                    "O banco nao esta pronto (detalhes acima). Resolva e rode de novo, "
                    "ou use --sem-conferir para subir assim mesmo."
                ) from exc
            self.stdout.write("")

        web = [sys.executable, "manage.py", "runserver", options["addrport"]]

        worker = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "core",
            "worker",
            "-l",
            "INFO",
            f"--concurrency={options['concurrency']}",
            "--prefetch-multiplier=1",
        ]
        if sys.platform == "win32":
            # O pool `prefork` nao tem suporte oficial no Windows desde o
            # Celery 4 e falha de forma erratica.
            worker += ["-P", "solo"]

        broker = settings.CELERY_BROKER_URL
        self.stdout.write(
            self.style.NOTICE(
                f"Broker: {_sem_segredo(broker)}  (BROKER_BACKEND={settings.BROKER_BACKEND})\n"
                f"Web:    http://{settings.ROOT_DOMAIN}"
                f"{':' + options['addrport'].rsplit(':', 1)[-1]}/\n"
                "Ctrl+C encerra os dois."
            )
        )

        processos: list[subprocess.Popen] = []
        try:
            # As duas listas sao montadas aqui, a partir de `sys.executable` e
            # de constantes; nada vem de entrada externa.
            if not options["sem_worker"]:
                processos.append(subprocess.Popen(worker))  # noqa: S603
            processos.append(subprocess.Popen(web))  # noqa: S603

            # Espera qualquer um dos dois terminar. Se o worker cai, o servidor
            # sozinho aceita cadastros que nunca serao provisionados — parar os
            # dois torna isso visivel em vez de virar uma fila crescendo.
            while all(processo.poll() is None for processo in processos):
                time.sleep(0.5)
        except KeyboardInterrupt:
            # O Ctrl+C ja chegou aos filhos pelo console; aqui so evitamos o
            # traceback e damos tempo de eles sairem sozinhos.
            pass
        finally:
            for processo in processos:
                if processo.poll() is None:
                    processo.terminate()
            for processo in processos:
                try:
                    processo.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    processo.kill()


def _sem_segredo(url: str) -> str:
    """Esconde a senha do broker: esta linha vai para o terminal a cada `dev`."""
    if "@" not in url:
        return url
    esquema, _, resto = url.partition("://")
    _, _, host = resto.rpartition("@")
    return f"{esquema}://***@{host}"
