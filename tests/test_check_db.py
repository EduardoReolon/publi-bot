"""Testes do `check_db` e da pista sobre o pgvector.

Todos nascem do mesmo relato: apontar o projeto para um PostgreSQL
recem-instalado e receber, la na frente, dentro de uma task do worker:

    django.db.utils.ProgrammingError: tipo "vector" nao existe
    LINE 1: ... "embedding" vector(1024)...

Nada ali diz que falta uma extensao. E tres causas diferentes produzem
exatamente esse texto: extensao ausente, extensao fora do search_path, e
extensao presente sem GRANT USAGE no schema.
"""

from __future__ import annotations

import sys

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.tasks import _com_pista
from apps.ops.management.commands import check_db


# ---------------------------------------------------------------------------
# Pista no erro do provisionamento
# ---------------------------------------------------------------------------
def test_erro_do_vector_ganha_a_causa_e_o_comando():
    """A mensagem crua nao menciona extensao nenhuma."""
    original = 'type "vector" does not exist\nLINE 1: ..."embedding" vector(1024)...'

    texto = _com_pista(original)

    assert original in texto
    assert "pgvector" in texto
    assert "check_db" in texto


def test_erro_do_vector_em_portugues_tambem():
    """O Postgres traduz a mensagem conforme o locale do servidor.

    Foi em pt-BR que o relato chegou: `tipo "vector" nao existe`.
    """
    texto = _com_pista('tipo "vector" não existe')

    assert "pgvector" in texto


def test_outros_erros_passam_intactos():
    """So esta causa ganha texto extra; o resto seria ruido."""
    original = "permission denied for table accounts_tenant"

    assert _com_pista(original) == original


# ---------------------------------------------------------------------------
# check_db
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_check_db_passa_num_banco_preparado(public_tenant, capsys):
    """O banco de teste herda a extensao do template1 — se este teste falha,
    e o proprio ambiente que esta sem pgvector."""
    call_command("check_db")

    saida = capsys.readouterr().out
    assert "Banco pronto" in saida
    assert "vector(1024)" in saida


@pytest.mark.django_db
def test_check_db_falha_e_sai_com_codigo_1_quando_falta_o_tenant_public(db, capsys):
    """Sem o tenant public a home devolve 404, entao isto e um problema."""
    from apps.accounts.models import Domain, Tenant

    Domain.objects.all().delete()
    Tenant.objects.all().delete()

    with pytest.raises(SystemExit) as excecao:
        call_command("check_db")

    assert excecao.value.code == 1
    assert "bootstrap_public" in capsys.readouterr().out


@pytest.mark.django_db
def test_causas_repetidas_aparecem_uma_vez_so(public_tenant, monkeypatch, capsys):
    """Extensao ausente derruba tres verificacoes pela mesma causa.

    Imprimir o mesmo paragrafo tres vezes empurra os outros problemas para
    fora da tela.
    """

    def sempre_falha(self):
        for _ in range(3):
            self._falha("simulado", "* mesmo remedio")

    monkeypatch.setattr(check_db.Command, "_extensoes", sempre_falha)

    with pytest.raises(SystemExit):
        call_command("check_db")

    saida = capsys.readouterr().out
    assert saida.count("* mesmo remedio") == 1
    assert "1 problema(s)" in saida


def test_sql_do_remedio_nao_usa_psql_menos_c():
    """Um `psql -c "..."` com aspas duplas aninhadas nao sobrevive ao shell.

    O SQL precisa de `"$user"`, entao a instrucao e colar dentro do psql.
    """
    remedio = check_db._remedio_extensao()

    assert "psql -U postgres -d" in remedio
    assert " -c " not in remedio
    assert 'SET search_path TO "$user", public, extensions;' in remedio


def test_remedio_no_windows_manda_compilar_o_pgvector(monkeypatch):
    """Nao existe binario oficial de pgvector para Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    remedio = check_db._remedio_extensao()

    assert "nmake /F Makefile.win" in remedio
    assert "x64 Native Tools" in remedio
    assert "apt install" not in remedio


def test_remedio_no_linux_manda_instalar_o_pacote(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    remedio = check_db._remedio_extensao()

    assert "apt install" in remedio
    assert "nmake" not in remedio


def test_remedio_do_template1_nao_mexe_no_search_path():
    """Quem manda no search_path do banco de teste e o settings, nao o template."""
    remedio = check_db._remedio_template1()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in remedio
    assert "ALTER DATABASE" not in remedio


# ---------------------------------------------------------------------------
# dev confere antes de subir
# ---------------------------------------------------------------------------
@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_dev_recusa_subir_com_o_banco_incompleto(db, monkeypatch):
    """Conferir depois de subir nao adianta: o servidor sobe, o cadastro e
    aceito, e a falha so aparece dentro da task do worker."""
    from apps.accounts.models import Domain, Tenant

    Domain.objects.all().delete()
    Tenant.objects.all().delete()

    def nao_deveria_subir(*a, **k):
        raise AssertionError("nenhum processo pode subir com o banco incompleto")

    monkeypatch.setattr("subprocess.Popen", nao_deveria_subir)

    with pytest.raises(CommandError, match="banco nao esta pronto"):
        call_command("dev")
