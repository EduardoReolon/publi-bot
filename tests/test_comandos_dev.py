"""Testes dos comandos que existem para nao deixar o worker esquecido.

A separacao em processos esta certa e mesmo assim tropeca: `runserver`
sobe, o servidor responde, e nada indica que falta metade do sistema. Sem
worker o cadastro de tenant nao termina — e nao falha tambem, porque a
mensagem e publicada com sucesso e fica na fila.

`dev` sobe todos juntos — web, worker e beat; `broker_status` responde, do
terminal, se a mensagem chegou e se alguem a consome.
"""

from __future__ import annotations

import sys

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

# O comando importa o nome no topo do modulo, entao e ele que precisa ser
# substituido — trocar `apps.ops.broker.mensagens_pendentes` nao rebindeia a
# referencia que o comando ja capturou.
ALVO = "apps.ops.management.commands.broker_status.mensagens_pendentes"


class ProcessoFalso:
    """Fica "rodando" ate alguem chamar terminate()."""

    def __init__(self, argv):
        self.argv = argv
        self._codigo = None

    def poll(self):
        return self._codigo

    def terminate(self):
        self._codigo = 0

    def wait(self, timeout=None):
        self._codigo = 0
        return 0

    def kill(self):
        self._codigo = -9


# ---------------------------------------------------------------------------
# dev
# ---------------------------------------------------------------------------
@override_settings(DEBUG=False)
def test_dev_recusa_rodar_fora_do_debug():
    """`dev` prende todos no mesmo terminal: se um cai, os outros vao junto.

    Em producao isso significaria derrubar o site porque o worker morreu — por
    isso la sao units separadas do systemd.
    """
    with pytest.raises(CommandError, match="so para desenvolvimento"):
        call_command("dev")


def _capturar(monkeypatch) -> list[list[str]]:
    """Substitui o Popen e devolve a lista dos comandos lancados.

    O ultimo processo "morre" na hora para o laco de espera do comando
    terminar; qual deles e o ultimo nao importa, porque o comando derruba
    todos assim que qualquer um sai.
    """
    lancados: list[list[str]] = []

    def falso_popen(argv, *a, **k):
        processo = ProcessoFalso(argv)
        lancados.append(argv)
        processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    return lancados


@pytest.fixture(autouse=True)
def sem_worker_de_gpu(settings):
    """O worker de GPU so sobe quando o checkout esta NESTA maquina.

    Neutralizado por padrao: se estivesse instalado na maquina de quem roda a
    suite, metade dos testes de contagem passaria a ver um processo a mais — e
    o resultado dependeria do que cada um tem no disco.
    """
    settings.WORKER_GPU_DIR = ""
    settings.INFERENCIA_BASE_URL = ""


@override_settings(DEBUG=True)
def test_dev_sobe_os_tres_processos(monkeypatch):
    """Um comando, o sistema inteiro.

    Os tres precisam subir juntos porque a falta de qualquer um e silenciosa:
    sem worker o cadastro de tenant nunca termina, e sem beat nada acontece
    por horario — nenhum erro em lugar nenhum, nos dois casos.
    """
    # `sem_conferir`: estes testes olham o lancamento dos processos. A conferencia
    # do banco tem os testes dela em test_check_db.py e exigiria banco aqui.
    lancados = _capturar(monkeypatch)

    call_command("dev", "127.0.0.1:8123", sem_conferir=True)

    assert len(lancados) == 3
    worker, beat, web = lancados
    assert "worker" in worker and "core" in worker
    assert "beat" in beat and "core" in beat
    assert "runserver" in web and "127.0.0.1:8123" in web


@override_settings(DEBUG=True)
def test_dev_nao_deixa_arquivo_de_pid_do_beat(monkeypatch):
    """O padrao (`celerybeat.pid`) sobrevive a um encerramento abrupto, e a
    subida seguinte falha com "Pidfile already exists" — um erro sobre um
    arquivo que quem desenvolve nem sabia que existia."""
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    _, beat, _ = lancados
    assert "--pidfile=" in beat


@override_settings(DEBUG=True)
def test_dev_sem_worker_nao_sobe_o_worker(monkeypatch):
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_worker=True, sem_conferir=True)

    assert not any("worker" in comando for comando in lancados)
    assert any("runserver" in comando for comando in lancados)


@override_settings(DEBUG=True)
def test_dev_sem_beat_nao_sobe_o_agendador(monkeypatch):
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_beat=True, sem_conferir=True)

    assert not any("beat" in comando for comando in lancados)
    assert any("runserver" in comando for comando in lancados)


@override_settings(DEBUG=True)
def test_dev_usa_pool_solo_no_windows(monkeypatch):
    """O pool `prefork` nao tem suporte oficial no Windows desde o Celery 4.

    Sem `-P solo` o worker falha de forma erratica — e o erro nao aponta o
    pool como causa.
    """
    lancados = _capturar(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")

    call_command("dev", sem_conferir=True)

    assert "-P" in lancados[0]
    assert lancados[0][lancados[0].index("-P") + 1] == "solo"


# ---------------------------------------------------------------------------
# O worker de GPU
# ---------------------------------------------------------------------------
# Ele vive em OUTRO repositorio. O `dev` so o sobe por conveniencia, quando o
# checkout esta nesta maquina e a porta esta livre — e o que se testa aqui e
# exatamente quando ele NAO deve subir, porque cada um desses casos, errado,
# derruba o `dev` inteiro ou cria um segundo arbitro.
@override_settings(DEBUG=True)
def test_sobe_o_worker_quando_ele_mora_aqui(monkeypatch, tmp_path, settings):
    _fingir_checkout_do_worker(tmp_path, settings)
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:8090"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    worker = [c for c in lancados if "app:app" in c]
    assert len(worker) == 1
    assert "8090" in worker[0]


@override_settings(DEBUG=True)
def test_nao_sobe_o_worker_que_esta_em_outra_maquina(monkeypatch, tmp_path, settings, capsys):
    """Subir uma copia local daria DOIS arbitros — e dois arbitros sobre uma
    placa nao arbitram nada: cada um acharia a GPU livre."""
    _fingir_checkout_do_worker(tmp_path, settings)
    settings.INFERENCIA_BASE_URL = "http://100.64.0.7:8090"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    assert not [c for c in lancados if "app:app" in c]
    assert "outra maquina" in capsys.readouterr().out


@override_settings(DEBUG=True)
def test_sem_o_caminho_do_checkout_nao_tenta_subir(monkeypatch, settings, capsys):
    """Sem palpite de caminho: o worker e outro repositorio, e adivinhar
    `../worker-gpu` acertaria so na maquina de quem escreveu."""
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:8090"
    settings.WORKER_GPU_DIR = ""
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    assert not [c for c in lancados if "app:app" in c]
    assert "WORKER_GPU_DIR" in capsys.readouterr().out


@override_settings(DEBUG=True)
def test_sem_gpu_nao_sobe_mesmo_instalado(monkeypatch, tmp_path, settings):
    _fingir_checkout_do_worker(tmp_path, settings)
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:8090"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_gpu=True, sem_conferir=True)

    assert not [c for c in lancados if "app:app" in c]


@override_settings(DEBUG=True)
def test_nao_sobe_um_segundo_worker_na_porta_ja_ocupada(monkeypatch, tmp_path, settings, capsys):
    """O estado normal de quem instalou a unit. Subir o segundo daria
    "address already in use" e derrubaria o `dev` inteiro, porque qualquer
    processo que morre encerra todos."""
    import socket

    _fingir_checkout_do_worker(tmp_path, settings)
    lancados = _capturar(monkeypatch)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)
        settings.INFERENCIA_BASE_URL = f"http://127.0.0.1:{servidor.getsockname()[1]}"

        call_command("dev", sem_conferir=True)

    assert not [c for c in lancados if "app:app" in c]
    assert "ja de pe" in capsys.readouterr().out


@override_settings(DEBUG=True)
def test_sem_inferencia_configurada_o_banner_avisa(monkeypatch, capsys):
    """Sem worker nao ha texto, capa nem conversao — e nada disso falha de
    forma visivel sozinho."""
    _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    assert "Sem texto, capa ou conversao" in capsys.readouterr().out


@override_settings(DEBUG=True)
def test_o_worker_recebe_o_segredo_com_o_nome_que_ele_espera(monkeypatch, tmp_path, settings):
    """Aqui a variavel se chama `INFERENCIA_API_KEY`; la,
    `WORKER_SHARED_SECRET`. Sem a ponte, o worker sobe e responde 500 a toda
    chamada — com uma mensagem que fala de configuracao sem dizer qual
    arquivo preencher."""
    _fingir_checkout_do_worker(tmp_path, settings)
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:8090"
    settings.INFERENCIA_API_KEY = "o-segredo"

    ambientes = []

    def falso_popen(argv, *a, **k):
        ambientes.append((argv, k.get("env")))
        processo = ProcessoFalso(argv)
        processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    call_command("dev", sem_conferir=True)

    do_worker = next(env for argv, env in ambientes if "app:app" in argv)
    assert do_worker["WORKER_SHARED_SECRET"] == "o-segredo"


@override_settings(DEBUG=True)
def test_o_env_do_worker_vence_o_do_terminal(tmp_path, settings, monkeypatch):
    """E o mesmo arquivo que o systemd le por `EnvironmentFile`. Ignora-lo
    faria o worker do `dev` se comportar diferente do configurado, e a
    diferenca apareceria so como "aqui esta mais lento"."""
    raiz = _fingir_checkout_do_worker(tmp_path, settings)
    (raiz / ".env").write_text("IMAGEM_DEVICE=cuda\nWORKER_SHARED_SECRET=do-arquivo\n")
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:8090"
    settings.INFERENCIA_API_KEY = "do-django"

    ambientes = []

    def falso_popen(argv, *a, **k):
        ambientes.append((argv, k.get("env")))
        processo = ProcessoFalso(argv)
        processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    call_command("dev", sem_conferir=True)

    do_worker = next(env for argv, env in ambientes if "app:app" in argv)
    assert do_worker["IMAGEM_DEVICE"] == "cuda"
    assert do_worker["WORKER_SHARED_SECRET"] == "do-arquivo"


def test_a_sonda_de_porta_ve_uma_porta_livre_como_livre():
    import socket

    from apps.ops.management.commands.dev import _porta_ocupada

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonda:
        sonda.bind(("127.0.0.1", 0))
        porta = sonda.getsockname()[1]

    assert _porta_ocupada("127.0.0.1", porta) is False


def test_a_sonda_de_porta_ve_uma_porta_ocupada_como_ocupada():
    import socket

    from apps.ops.management.commands.dev import _porta_ocupada

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)

        assert _porta_ocupada("127.0.0.1", servidor.getsockname()[1]) is True


def _fingir_checkout_do_worker(tmp_path, settings):
    """Uma arvore que parece o checkout do worker, sem os 3 GB de torch."""
    import sys as _sys

    raiz = tmp_path / "worker-gpu"
    subpasta = "Scripts" if _sys.platform == "win32" else "bin"
    nome = "python.exe" if _sys.platform == "win32" else "python"
    binario = raiz / "venv" / subpasta / nome
    binario.parent.mkdir(parents=True)
    binario.touch()
    settings.WORKER_GPU_DIR = str(raiz)
    return raiz


# ---------------------------------------------------------------------------
# broker_status
# ---------------------------------------------------------------------------
def test_broker_status_nao_imprime_a_senha_do_broker(monkeypatch, capsys):
    """A linha vai para o terminal a cada execucao."""
    monkeypatch.setattr(ALVO, lambda *a, **k: 0)
    with override_settings(
        CELERY_BROKER_URL="sqla+postgresql+psycopg://publibot:senha-secreta@127.0.0.1:5432/publibot"
    ):
        call_command("broker_status")

    saida = capsys.readouterr().out
    assert "senha-secreta" not in saida
    assert "127.0.0.1:5432/publibot" in saida


def test_broker_status_fila_com_mensagem_lista_as_duas_causas(monkeypatch, capsys):
    monkeypatch.setattr(ALVO, lambda *a, **k: 2)
    call_command("broker_status")

    saida = capsys.readouterr().out
    assert "manage.py dev" in saida
    # A segunda causa e a que ninguem procura: worker ligado a outro broker.
    assert "OUTRO broker" in saida


def test_broker_status_fila_ilegivel_nao_diz_que_esta_vazia(monkeypatch, capsys):
    """`None` e "nao sei ler a fila", nunca "a fila esta vazia"."""
    monkeypatch.setattr(ALVO, lambda *a, **k: None)
    call_command("broker_status")

    saida = capsys.readouterr().out
    assert "nao respondeu" in saida
    assert "Fila vazia" not in saida
