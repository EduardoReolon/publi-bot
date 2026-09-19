"""Sobe TODOS os processos do projeto num terminal so, em desenvolvimento.

Sao tres sempre — web, worker e beat — mais o worker de GPU quando ele mora
nesta maquina. Este comando e o unico lugar que precisa saber disso.
Acrescentar outro amanha e acrescentar uma linha em `_servicos()`: quem usa
continua rodando `manage.py dev`.

O worker de GPU vive em OUTRO repositorio: a placa e um recurso da maquina,
compartilhado com outros sistemas, e nao um detalhe deste projeto. O `dev` so
o sobe por conveniencia, quando `WORKER_GPU_DIR` aponta para um checkout dele.

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
            "--sem-gpu",
            action="store_true",
            help=(
                "Nao sobe o worker de GPU, mesmo que ele esteja instalado "
                "aqui. Sem ele nao ha texto, capa nem conversao de PDF."
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

        aviso = _nota_sobre_a_gpu(options, servicos)
        if aviso:
            self.stdout.write(aviso)

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

    if not options["sem_gpu"]:
        gpu = _servico_de_gpu()
        if gpu is not None:
            servicos.append(gpu)

    # O web vem por ultimo de proposito: assim a primeira linha que rola na tela
    # depois do banner e a dele, que e onde se olha.
    servicos.append(("web", [sys.executable, "manage.py", "runserver", options["addrport"]], None))
    return servicos


def _servico_de_gpu() -> tuple[str, list[str], dict[str, str]] | None:
    """O worker de GPU, se ele morar NESTA maquina e nao estiver de pe.

    Ele vive em OUTRO repositorio, porque mais de um sistema o usa: este
    projeto, o CRM, e o que vier. A placa e um recurso da maquina, nao deste
    projeto — e enquanto o codigo dela morava aqui dentro, era so uma questao
    de tempo ate um segundo consumidor precisar do mesmo e nao ter como.

    O que o `dev` faz e conveniencia: subir o worker junto quando ele esta na
    mesma maquina e a porta esta livre. Tres condicoes:

    - a URL de inferencia aponta para c'a. Apontando para outra maquina,
      subir uma copia local daria dois arbitros — e dois arbitros sobre uma
      placa nao arbitram nada;
    - `WORKER_GPU_DIR` aponta para um checkout com venv. Sem palpite de
      caminho: o repositorio e outro, e adivinhar acertaria so na maquina de
      quem escreveu;
    - a porta esta livre. Ocupada significa que ele ja esta de pe — e subir o
      segundo daria "address already in use", derrubando o `dev` inteiro,
      porque qualquer processo que morre encerra todos.
    """
    from urllib.parse import urlparse

    url = getattr(settings, "INFERENCIA_BASE_URL", "")
    if not url:
        return None

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None

    raiz = _raiz_do_worker()
    if raiz is None:
        return None

    python = _python_do_worker(raiz)
    if not python.exists():
        return None

    porta = endereco.port or 8090
    if _porta_ocupada(endereco.hostname, porta):
        return None

    return (
        "worker-gpu",
        [
            str(python),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            endereco.hostname,
            "--port",
            str(porta),
            # UM processo: dois seriam dois arbitros.
            "--workers",
            "1",
            "--app-dir",
            str(raiz),
        ],
        _ambiente_do_worker(raiz),
    )


def _raiz_do_worker() -> Path | None:
    """O checkout do worker, se `WORKER_GPU_DIR` disser onde ele esta."""
    caminho = getattr(settings, "WORKER_GPU_DIR", "")
    if not caminho:
        return None
    raiz = Path(caminho).expanduser()
    return raiz if raiz.is_dir() else None


def _python_do_worker(raiz: Path) -> Path:
    if sys.platform == "win32":
        return raiz / "venv" / "Scripts" / "python.exe"
    return raiz / "venv" / "bin" / "python"


def _nota_sobre_a_gpu(options, servicos) -> str:
    """Explica por que o worker de GPU nao esta na lista.

    O silencio seria pior que o ruido: quem instalou a unit ve "Subindo:
    worker, beat, web" e conclui que nada de GPU vai acontecer — quando vai,
    pelo servico que ja estava de pe. E quem nao tem nada precisa saber que
    texto, capa e conversao de PDF nao vao funcionar.
    """
    from urllib.parse import urlparse

    if options["sem_gpu"] or any(nome == "worker-gpu" for nome, _, _ in servicos):
        return ""

    url = getattr(settings, "INFERENCIA_BASE_URL", "")
    if not url:
        return "GPU:      nenhuma (INFERENCIA_BASE_URL vazia). Sem texto, capa ou conversao."

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return f"GPU:      em outra maquina ({endereco.hostname}); nada a subir aqui."

    if _porta_ocupada(endereco.hostname, endereco.port or 8090):
        return f"GPU:      ja de pe em {url} (servico proprio); nao subi outro."

    if _raiz_do_worker() is None:
        return (
            f"GPU:      {url} aponta para c'a, mas WORKER_GPU_DIR nao aponta para "
            f"um checkout do worker-gpu."
        )

    return f"GPU:      {url} aponta para c'a, mas o venv do worker nao existe."


def _porta_ocupada(host: str, porta: int) -> bool:
    """Se alguem ja aceita conexao ali.

    Uma conexao TCP e nao uma requisicao HTTP: o que se quer saber e se a porta
    esta tomada, e isso independe de quem a tomou. Perguntar `/health/` diria
    se e o servico certo, mas responderia "livre" para uma porta ocupada por
    outra coisa — e o `uvicorn` falharia do mesmo jeito.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonda:
        sonda.settimeout(0.3)
        return sonda.connect_ex((host, porta)) == 0


def _ambiente_do_worker(raiz: Path) -> dict[str, str]:
    """O ambiente do worker: o `.env` DELE por cima do do terminal.

    Ler o `.env` do outro repositorio nao e conveniencia: e o mesmo arquivo
    que o systemd le por `EnvironmentFile` quando o servico roda de verdade.
    Sem ele, o worker subido pelo `dev` ignoraria o dispositivo, o modelo e os
    limites configurados — e se comportaria diferente do que voce ajustou, com
    a diferenca aparecendo so como "aqui esta mais lento".

    O segredo tem uma ponte: neste projeto ele se chama `INFERENCIA_API_KEY`,
    no worker `WORKER_SHARED_SECRET`. Sao dois nomes para o mesmo valor, cada
    um com o nome que faz sentido do seu lado.
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
        ambiente["WORKER_SHARED_SECRET"] = getattr(settings, "INFERENCIA_API_KEY", "")

    return ambiente


def _sem_segredo(url: str) -> str:
    """Esconde a senha do broker: esta linha vai para o terminal a cada `dev`."""
    if "@" not in url:
        return url
    esquema, _, resto = url.partition("://")
    _, _, host = resto.rpartition("@")
    return f"{esquema}://***@{host}"
