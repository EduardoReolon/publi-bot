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
def sem_worker_de_conversao(settings):
    """O worker do Docling so sobe quando esta instalado NESTA maquina.

    Neutralizado por padrao: se estivesse instalado na maquina de quem roda a
    suite, metade dos testes de contagem passaria a ver um processo a mais — e
    o resultado dependeria do que cada um tem no disco.
    """
    settings.CONVERSAO_BASE_URL = ""


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
# O worker de conversao (Docling)
# ---------------------------------------------------------------------------
@override_settings(DEBUG=True)
def test_sobe_o_worker_de_conversao_quando_ele_mora_aqui(monkeypatch, tmp_path, settings):
    """Em desenvolvimento o Docling costuma rodar na mesma maquina, e subir
    tres processos a mao e esquecer o quarto e o mesmo erro de sempre."""
    _fingir_venv_do_worker(monkeypatch, tmp_path)
    settings.CONVERSAO_BASE_URL = "http://127.0.0.1:8100"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    conversao = [c for c in lancados if "docling_api:app" in c]
    assert len(conversao) == 1
    assert "8100" in conversao[0]


@override_settings(DEBUG=True)
def test_nao_sobe_o_worker_que_esta_em_outra_maquina(monkeypatch, tmp_path, settings):
    """Em producao ele roda onde esta a placa (ADR-0007). Subir uma copia local
    daria dois servicos, e um deles nao receberia trabalho nenhum."""
    _fingir_venv_do_worker(monkeypatch, tmp_path)
    settings.CONVERSAO_BASE_URL = "http://100.64.0.9:8100"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    assert not any("docling_api:app" in c for c in lancados)


@override_settings(DEBUG=True)
def test_sem_o_venv_do_worker_nao_tenta_subir(monkeypatch, settings):
    """O Docling traz torch (~3 GB) e nao entra no venv da nuvem. Sem o venv
    proprio, tentar subir seria pedir um modulo que nao existe."""
    settings.CONVERSAO_BASE_URL = "http://127.0.0.1:8100"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conferir=True)

    assert not any("docling_api:app" in c for c in lancados)


@override_settings(DEBUG=True)
def test_sem_conversao_nao_sobe_mesmo_instalado(monkeypatch, tmp_path, settings):
    _fingir_venv_do_worker(monkeypatch, tmp_path)
    settings.CONVERSAO_BASE_URL = "http://127.0.0.1:8100"
    lancados = _capturar(monkeypatch)

    call_command("dev", sem_conversao=True, sem_conferir=True)

    assert not any("docling_api:app" in c for c in lancados)


def test_o_worker_recebe_o_segredo_com_o_nome_que_ele_espera(monkeypatch, tmp_path, settings):
    """Sao dois nomes para o mesmo valor: `CONVERSAO_SEGREDO` no PubliBot,
    `WORKER_SHARED_SECRET` no worker. Sem a ponte, o worker sobe sem segredo e
    responde 500 a toda conversao — com uma mensagem que fala de configuracao,
    nao de qual arquivo preencher."""
    from apps.ops.management.commands.dev import _ambiente_do_worker

    settings.CONVERSAO_SEGREDO = "o-segredo-do-env-do-publibot"

    ambiente = _ambiente_do_worker(tmp_path)

    assert ambiente["WORKER_SHARED_SECRET"] == "o-segredo-do-env-do-publibot"


def test_o_env_do_worker_vence_o_do_terminal(tmp_path, settings):
    """Esse arquivo e o MESMO que o systemd le por `EnvironmentFile`. Ignora-lo
    faria o worker do `dev` rodar com outro dispositivo do que o configurado —
    e a diferenca apareceria so como "aqui esta mais lento"."""
    from apps.ops.management.commands.dev import _ambiente_do_worker

    (tmp_path / ".env").write_text(
        "# um comentario\n"
        "DOCLING_DEVICE=cuda\n"
        'WORKER_SHARED_SECRET="o-do-worker"\n'
        "linha solta sem igual\n",
        encoding="utf-8",
    )
    settings.CONVERSAO_SEGREDO = "o-do-publibot"

    ambiente = _ambiente_do_worker(tmp_path)

    assert ambiente["DOCLING_DEVICE"] == "cuda"
    assert ambiente["WORKER_SHARED_SECRET"] == "o-do-worker"


def _fingir_venv_do_worker(monkeypatch, tmp_path) -> None:
    """Cria a arvore que `_servico_de_conversao` procura, sem instalar nada."""
    import sys as _sys

    from django.conf import settings as _settings

    subpasta = "Scripts" if _sys.platform == "win32" else "bin"
    nome = "python.exe" if _sys.platform == "win32" else "python"
    binario = tmp_path / "worker-gpu" / "venv" / subpasta / nome
    binario.parent.mkdir(parents=True)
    binario.touch()
    monkeypatch.setattr(_settings, "BASE_DIR", tmp_path)


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
