"""Testes dos comandos que existem para nao deixar o worker esquecido.

A separacao em dois processos esta certa e mesmo assim tropeca: `runserver`
sobe, o servidor responde, e nada indica que falta metade do sistema. Sem
worker o cadastro de tenant nao termina — e nao falha tambem, porque a
mensagem e publicada com sucesso e fica na fila.

`dev` sobe os dois juntos; `broker_status` responde, do terminal, se a
mensagem chegou e se alguem a consome.
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
    """`dev` prende os dois no mesmo terminal: se um cai, o outro vai junto.

    Em producao isso significaria derrubar o site porque o worker morreu — por
    isso la sao units separadas do systemd.
    """
    with pytest.raises(CommandError, match="so para desenvolvimento"):
        call_command("dev")


@override_settings(DEBUG=True)
def test_dev_sobe_web_e_worker(monkeypatch):
    lancados: list[list[str]] = []

    def falso_popen(argv, *a, **k):
        processo = ProcessoFalso(argv)
        lancados.append(argv)
        # O segundo processo "morre" na hora, para o laco de espera terminar.
        if len(lancados) == 2:
            processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    call_command("dev", "127.0.0.1:8123")

    assert len(lancados) == 2
    worker, web = lancados
    assert "worker" in worker and "core" in worker
    assert "runserver" in web and "127.0.0.1:8123" in web


@override_settings(DEBUG=True)
def test_dev_sem_worker_sobe_so_o_servidor(monkeypatch):
    lancados: list[list[str]] = []

    def falso_popen(argv, *a, **k):
        processo = ProcessoFalso(argv)
        lancados.append(argv)
        processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    call_command("dev", sem_worker=True)

    assert len(lancados) == 1
    assert "runserver" in lancados[0]


@override_settings(DEBUG=True)
def test_dev_usa_pool_solo_no_windows(monkeypatch):
    """O pool `prefork` nao tem suporte oficial no Windows desde o Celery 4.

    Sem `-P solo` o worker falha de forma erratica — e o erro nao aponta o
    pool como causa.
    """
    lancados: list[list[str]] = []

    def falso_popen(argv, *a, **k):
        processo = ProcessoFalso(argv)
        lancados.append(argv)
        processo.terminate()
        return processo

    monkeypatch.setattr("subprocess.Popen", falso_popen)
    monkeypatch.setattr(sys, "platform", "win32")
    call_command("dev")

    assert "-P" in lancados[0]
    assert lancados[0][lancados[0].index("-P") + 1] == "solo"


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
