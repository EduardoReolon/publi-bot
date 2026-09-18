"""Sobe TODOS os processos do projeto num terminal so, em desenvolvimento.

Sao tres sempre — web, worker e beat — mais os dois servicos de GPU quando eles
moram nesta maquina: a conversao de PDF e a geracao de imagem de capa. Este
comando e o unico lugar que precisa saber disso. Acrescentar outro amanha e
acrescentar uma linha em `_servicos()`: quem usa continua rodando
`manage.py dev`.

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
            "--sem-imagem",
            action="store_true",
            help=(
                "Nao sobe o worker de geracao de imagem, mesmo que ele esteja "
                "instalado aqui. Os artigos saem sem capa."
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

        for aviso in (
            _nota_sobre_a_conversao(options, servicos),
            _nota_sobre_a_imagem(options, servicos),
        ):
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

    if not options["sem_conversao"]:
        conversao = _servico_de_conversao()
        if conversao is not None:
            servicos.append(conversao)

    if not options["sem_imagem"]:
        imagem = _servico_de_imagem()
        if imagem is not None:
            servicos.append(imagem)

    # O web vem por ultimo de proposito: assim a primeira linha que rola na tela
    # depois do banner e a dele, que e onde se olha.
    servicos.append(("web", [sys.executable, "manage.py", "runserver", options["addrport"]], None))
    return servicos


def _servico_de_conversao() -> tuple[str, list[str], dict[str, str]] | None:
    """O worker do Docling, se ele morar NESTA maquina."""
    return _worker_de_gpu(
        nome="conversao",
        url=getattr(settings, "CONVERSAO_BASE_URL", ""),
        modulo="docling_api:app",
        porta_padrao=8100,
    )


def _servico_de_imagem() -> tuple[str, list[str], dict[str, str]] | None:
    """O worker de imagem de capa, se ele morar NESTA maquina.

    Mesmas condicoes da conversao, e mais uma: o `diffusers` precisa estar no
    venv do worker. Ele nao vem com o Docling, e quem instalou o worker antes
    deste servico existir tem o venv sem ele — tentar subir daria um
    `ModuleNotFoundError` que derruba o `dev` inteiro, porque qualquer
    processo que morre encerra todos.
    """
    servico = _worker_de_gpu(
        nome="imagem",
        url=getattr(settings, "IMAGEM_BASE_URL", ""),
        modulo="imagem_api:app",
        porta_padrao=8101,
    )
    if servico is None or not _tem_diffusers():
        return None
    return servico


def _worker_de_gpu(
    *, nome: str, url: str, modulo: str, porta_padrao: int
) -> tuple[str, list[str], dict[str, str]] | None:
    """Um servico de `worker-gpu/`, se ele morar NESTA maquina.

    Em producao nao mora: roda onde esta a placa e e alcancado por Tailscale
    (ADR-0007). Em desenvolvimento costuma ser a mesma maquina, e ai subir os
    outros processos a mao e esquecer este e o erro de sempre.

    Quatro condicoes, e cada uma existe por um motivo:

    - a URL esta preenchida e aponta para c'a. Apontando para outra maquina,
      subir uma copia local daria dois servicos e um receberia trabalho nenhum.
    - o venv proprio existe. Estes servicos trazem torch (~3 GB) e NAO entram
      no venv da nuvem; sem isso, o comando tentaria subir algo que nao esta
      instalado.
    - a porta da URL e a que passamos ao uvicorn, para os dois concordarem sem
      ninguem editar duas coisas.
    - a porta esta livre. Ocupada significa que o servico ja esta de pe —
      tipicamente como unit do systemd, que e como se roda numa maquina que
      serve o servidor. Subir o segundo daria "address already in use" e
      derrubaria o `dev` inteiro. Nao e conflito: e o estado normal de quem
      instalou a unit, e usa-se o que ja esta la.

    Faltando qualquer uma, devolve None em silencio — e `_nota_sobre_...`
    explica no banner por que o servico nao esta na lista.
    """
    from urllib.parse import urlparse

    if not url:
        return None

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None

    raiz = _raiz_do_worker()
    python = _python_do_worker(raiz)
    if not python.exists():
        return None

    porta = endereco.port or porta_padrao
    if _porta_ocupada(endereco.hostname, porta):
        return None

    return (
        nome,
        [
            str(python),
            "-m",
            "uvicorn",
            modulo,
            "--host",
            endereco.hostname,
            "--port",
            str(porta),
            # Um so: dois processos significam dois modelos residentes, e a
            # VRAM nao comporta. Cada servico ja recusa o segundo pedido com
            # 503 pelo mesmo motivo.
            "--workers",
            "1",
            "--app-dir",
            str(raiz),
        ],
        _ambiente_do_worker(raiz),
    )


def _raiz_do_worker() -> Path:
    return Path(settings.BASE_DIR) / "worker-gpu"


def _python_do_worker(raiz: Path) -> Path:
    if sys.platform == "win32":
        return raiz / "venv" / "Scripts" / "python.exe"
    return raiz / "venv" / "bin" / "python"


def _tem_diffusers() -> bool:
    """Se o venv do worker consegue importar o `diffusers`.

    Pergunta ao interpretador dele, e nao a este: sao venvs diferentes, e o
    `diffusers` nunca estara neste.
    """
    python = _python_do_worker(_raiz_do_worker())
    try:
        # Caminho derivado de `settings.BASE_DIR` e de constantes; nada vem de
        # entrada externa.
        return (
            subprocess.run(  # noqa: S603
                [str(python), "-c", "import diffusers"],
                capture_output=True,
                timeout=60,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _nota_sobre_a_conversao(options, servicos) -> str:
    """Explica por que o worker de conversao nao esta na lista.

    O silencio aqui seria pior que o ruido. Quem instalou a unit do systemd ve
    "Subindo: worker, beat, web" e conclui que a conversao nao vai acontecer —
    quando ela vai, pelo servico que ja estava de pe. E quem NAO instalou nada
    precisa saber que os PDFs vao sair pelo extrator local.
    """
    from urllib.parse import urlparse

    if options["sem_conversao"] or any(nome == "conversao" for nome, _, _ in servicos):
        return ""

    url = getattr(settings, "CONVERSAO_BASE_URL", "")
    if not url:
        return (
            "Conversao:  nenhuma (CONVERSAO_BASE_URL vazia). "
            "PDF sai pelo extrator local, sem analise de layout."
        )

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return f"Conversao:  em outra maquina ({endereco.hostname}); nada a subir aqui."

    if _porta_ocupada(endereco.hostname, endereco.port or 8100):
        return f"Conversao:  ja de pe em {url} (servico proprio); nao subi outro."

    return (
        f"Conversao:  {url} aponta para c'a, mas worker-gpu/venv nao existe. "
        f"PDF sai pelo extrator local."
    )


def _nota_sobre_a_imagem(options, servicos) -> str:
    """Explica por que o worker de imagem nao esta na lista.

    Mesmo motivo da nota da conversao: o silencio faria quem instalou a unit
    concluir que nao vai haver capa, e quem nao instalou nada nao saberia que
    os artigos vao sair sem imagem.
    """
    from urllib.parse import urlparse

    if options["sem_imagem"] or any(nome == "imagem" for nome, _, _ in servicos):
        return ""

    url = getattr(settings, "IMAGEM_BASE_URL", "")
    if not url:
        return (
            "Imagem:     nenhuma (IMAGEM_BASE_URL vazia). "
            "Os artigos saem sem capa; o texto nao e afetado."
        )

    endereco = urlparse(url)
    if endereco.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return f"Imagem:     em outra maquina ({endereco.hostname}); nada a subir aqui."

    if _porta_ocupada(endereco.hostname, endereco.port or 8101):
        return f"Imagem:     ja de pe em {url} (servico proprio); nao subi outro."

    if not _python_do_worker(_raiz_do_worker()).exists():
        return f"Imagem:     {url} aponta para c'a, mas worker-gpu/venv nao existe."

    return (
        f"Imagem:     {url} aponta para c'a, mas o venv do worker nao tem o "
        f"diffusers. Rode: worker-gpu/venv/bin/pip install -r worker-gpu/requirements.txt"
    )


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
