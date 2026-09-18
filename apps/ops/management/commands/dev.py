"""Sobe TODOS os processos do projeto num terminal so, em desenvolvimento.

Hoje sao tres — web, worker e beat — e este comando e o unico lugar que precisa
saber disso. Acrescentar um quarto amanha e acrescentar uma linha em
`_servicos()`: quem usa continua rodando `manage.py dev`.

Este comando existe por um motivo empirico: a separacao em processos e correta,
e mesmo assim tropeca. Um cadastro de tenant depende do worker (ADR-0001), e sem
ele nada falha — a mensagem e publicada, fica na fila, e a tela espera para
sempre. Quem esta comecando roda `runserver`, ve o servidor de pe e conclui, com
razao, que o sistema esta rodando.

O beat tem a mesma armadilha, um nivel acima: sem ele a aplicacao funciona,
tudo responde, e simplesmente nada acontece sozinho — conteudo aprovado nunca
e publicado, trabalho parado nunca e retomado, reserva vencida nunca e solta.
Nenhum erro em lugar nenhum.

Nao ha supervisor de processo aqui de proposito. Os filhos herdam o terminal e
NAO ganham grupo de processo proprio, entao o Ctrl+C do console chega a todos de
uma vez — no Linux e no macOS via SIGINT ao grupo em primeiro plano, no Windows
via CTRL_C_EVENT aos processos ligados ao console. E o comportamento que ja se
espera de um terminal, sem codigo para mante-lo.

Em producao nada disto se aplica: la sao units separadas do systemd, cada uma
com seu ciclo de vida (`deploy/systemd/`). A correspondencia e um para um, e e
proposital — o que roda na sua maquina e o que roda no servidor.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

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
            help="Nao sobe o worker. As tarefas ficam na fila, sem executar.",
        )
        parser.add_argument(
            "--sem-beat",
            action="store_true",
            help=(
                "Nao sobe o agendador. Nada roda por horario: publicacao, "
                "varredura de trabalhos parados e liberacao de reservas param."
            ),
        )
        parser.add_argument(
            "--sem-conversao",
            action="store_true",
            help=(
                "Nao sobe o worker de conversao de PDF, mesmo que ele esteja "
                "instalado aqui. Os PDFs caem no extrator local."
            ),
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

        servicos = _servicos(options)

        broker = settings.CELERY_BROKER_URL
        porta = options["addrport"].rsplit(":", 1)[-1]
        self.stdout.write(
            self.style.NOTICE(
                f"Broker:   {_sem_segredo(broker)}  (BROKER_BACKEND={settings.BROKER_BACKEND})\n"
                f"Web:      http://{settings.ROOT_DOMAIN}:{porta}/\n"
                f"Subindo:  {', '.join(nome for nome, _, _ in servicos)}\n"
                "Ctrl+C encerra todos."
            )
        )

        processos: list[subprocess.Popen] = []
        try:
            for _, comando, ambiente in servicos:
                # Os comandos sao montados em `_servicos`, a partir de
                # `sys.executable` e de constantes; nada vem de entrada externa.
                processos.append(subprocess.Popen(comando, env=ambiente))  # noqa: S603

            # Espera QUALQUER um terminar, e entao derruba o resto. Deixar os
            # outros de pe seria pior que parar: o servidor sozinho aceita
            # cadastros que nunca serao provisionados, e a fila cresce em
            # silencio. Parando tudo, o problema aparece.
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


def _servicos(options) -> list[tuple[str, list[str], dict[str, str] | None]]:
    """Os processos que compoem o sistema: (nome, comando, ambiente).

    O ambiente e `None` para quem herda o do terminal, que e o caso de quase
    todos. So o worker de conversao precisa do proprio: ele mora em outro venv,
    com outro `.env`.

    ESTE e o ponto de extensao. Um processo novo — outro worker, um servico
    auxiliar — entra aqui, e `manage.py dev` continua sendo o unico comando que
    quem desenvolve precisa saber. Ao acrescentar um, crie tambem o unit
    correspondente em `deploy/systemd/`: a correspondencia um para um entre os
    dois e o que mantem "roda na minha maquina" e "roda no servidor" com o mesmo
    significado.
    """
    servicos: list[tuple[str, list[str], dict[str, str] | None]] = []

    if not options["sem_worker"]:
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
        servicos.append(("worker", worker, None))

    if not options["sem_beat"]:
        servicos.append(
            (
                "beat",
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "core",
                    "beat",
                    "-l",
                    "INFO",
                    # Sem arquivo de pid. O padrao (`celerybeat.pid`) sobrevive
                    # a um encerramento abrupto, e a proxima subida falha com
                    # "Pidfile already exists" — um erro sobre um arquivo que a
                    # pessoa nem sabia que existia. O scheduler deste projeto e
                    # o do banco, entao nao ha estado local a preservar.
                    "--pidfile=",
                ],
                None,
            )
        )

    if not options["sem_conversao"]:
        conversao = _servico_de_conversao()
        if conversao is not None:
            servicos.append(conversao)

    # O web vem por ultimo de proposito: assim a primeira linha que rola na tela
    # depois do banner e a dele, que e onde se olha.
    servicos.append(("web", [sys.executable, "manage.py", "runserver", options["addrport"]], None))
    return servicos


def _servico_de_conversao() -> tuple[str, list[str], dict[str, str]] | None:
    """O worker do Docling, se ele morar NESTA maquina.

    Em producao ele nao mora: roda onde esta a placa e e alcancado por
    Tailscale (ADR-0007). Em desenvolvimento costuma ser a mesma maquina, e ai
    subir tres processos a mao e esquecer o quarto e o mesmo erro de sempre.

    Tres condicoes, e cada uma existe por um motivo:

    - `CONVERSAO_BASE_URL` aponta para c'a. Apontando para outra maquina, subir
      uma copia local daria dois servicos e um deles receberia trabalho nenhum.
    - o venv proprio existe. O Docling traz torch (~3 GB) e NAO entra no venv
      da nuvem; sem isso, o comando tentaria subir algo que nao esta instalado.
    - a porta da URL e a que passamos ao uvicorn, para os dois concordarem sem
      ninguem editar duas coisas.

    Faltando qualquer uma, devolve None em silencio: nao ter o worker aqui e
    um estado legitimo, e a tela de curadoria ja avisa quando a conversao saiu
    pelo extrator local.
    """
    from urllib.parse import urlparse

    url = getattr(settings, "CONVERSAO_BASE_URL", "")
    if not url:
        return None

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None

    raiz = Path(settings.BASE_DIR) / "worker-gpu"
    python = raiz / "venv" / "bin" / "python"
    if sys.platform == "win32":
        python = raiz / "venv" / "Scripts" / "python.exe"
    if not python.exists():
        return None

    return (
        "conversao",
        [
            str(python),
            "-m",
            "uvicorn",
            "docling_api:app",
            "--host",
            endereco.hostname,
            "--port",
            str(endereco.port or 8100),
            # Um so: duas conversoes simultaneas estouram a VRAM, e o servico
            # ja recusa a segunda com 503 justamente por isso.
            "--workers",
            "1",
            "--app-dir",
            str(raiz),
        ],
        _ambiente_do_worker(raiz),
    )


def _ambiente_do_worker(raiz: Path) -> dict[str, str]:
    """O ambiente do worker: o `.env` dele por cima do do terminal.

    Ler o `worker-gpu/.env` aqui nao e conveniencia. Esse arquivo e o mesmo que
    o systemd le por `EnvironmentFile` quando o servico roda de verdade; sem
    le-lo, o worker subido pelo `dev` ignoraria `DOCLING_DEVICE` e
    `DOCLING_THREADS` e se comportaria diferente do que voce configurou — a
    diferenca apareceria so como "aqui esta mais lento", sem causa visivel.

    O segredo tem uma ponte: no PubliBot ele se chama `CONVERSAO_SEGREDO`, no
    worker `WORKER_SHARED_SECRET`. Sao dois nomes para o mesmo valor, cada um
    com o nome que faz sentido do seu lado. Se o `.env` do worker nao o
    definir, o do PubliBot vale — assim um arquivo so basta em
    desenvolvimento.
    """
    import os

    ambiente = dict(os.environ)

    arquivo = raiz / ".env"
    if arquivo.is_file():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            limpa = linha.strip()
            if not limpa or limpa.startswith("#") or "=" not in limpa:
                continue
            chave, _, valor = limpa.partition("=")
            ambiente[chave.strip()] = valor.strip().strip('"').strip("'")

    if not ambiente.get("WORKER_SHARED_SECRET"):
        ambiente["WORKER_SHARED_SECRET"] = getattr(settings, "CONVERSAO_SEGREDO", "")

    return ambiente


def _sem_segredo(url: str) -> str:
    """Esconde a senha do broker: esta linha vai para o terminal a cada `dev`."""
    if "@" not in url:
        return url
    esquema, _, resto = url.partition("://")
    _, _, host = resto.rpartition("@")
    return f"{esquema}://***@{host}"
