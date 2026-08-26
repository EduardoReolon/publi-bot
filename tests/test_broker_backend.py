"""Testes da escolha de broker (ADR-0013).

Cada teste aqui corresponde a uma armadilha encontrada implementando o modo
`BROKER_BACKEND=postgres` — todas descobertas rodando o worker, e todas
falhando em silencio ou com mensagem enganosa.
"""

from __future__ import annotations

import importlib

import pytest


def _recarrega_settings(monkeypatch, **variaveis):
    """Reimporta o settings com as variaveis de ambiente dadas.

    A escolha do broker acontece em tempo de import, entao testar exige
    reavaliar o modulo, nao apenas trocar um atributo.
    """
    for chave, valor in variaveis.items():
        if valor is None:
            monkeypatch.delenv(chave, raising=False)
        else:
            monkeypatch.setenv(chave, valor)

    import core.settings.base as base

    return importlib.reload(base)


def test_nome_da_variavel_nao_colide_com_o_namespace_do_celery():
    """`config_from_object(namespace="CELERY")` retira o prefixo CELERY_ e
    repassa a chave ao Celery. Uma variavel chamada `CELERY_BROKER` viraria
    `broker`, que o Celery interpreta como URL — e o worker sobe tentando falar
    AMQP com um host chamado "postgres".

    Este teste falha se alguem renomear a variavel de volta para dentro do
    namespace.
    """
    import core.settings.base as base

    assert hasattr(base, "BROKER_BACKEND")
    assert not hasattr(base, "CELERY_BROKER"), (
        "uma variavel chamada CELERY_BROKER e repassada ao Celery como "
        "`broker` e quebra a conexao do worker"
    )


def test_modo_postgres_monta_url_a_partir_das_credenciais_do_banco(monkeypatch):
    base = _recarrega_settings(
        monkeypatch,
        BROKER_BACKEND="postgres",
        POSTGRES_USER="usuario_teste",
        POSTGRES_PASSWORD="senha_teste",
        POSTGRES_HOST="10.0.0.9",
        POSTGRES_PORT="5433",
        POSTGRES_DB="banco_teste",
    )
    try:
        assert base.CELERY_BROKER_URL == (
            "sqla+postgresql+psycopg://usuario_teste:senha_teste@10.0.0.9:5433/banco_teste"
        )
        assert base.CELERY_RESULT_BACKEND == "django-db"
    finally:
        _recarrega_settings(monkeypatch, BROKER_BACKEND="redis")


def test_modo_postgres_ignora_celery_broker_url_do_env(monkeypatch):
    """O Celery le os.environ ANTES do settings:

        def broker_url(self):
            return (os.environ.get('CELERY_BROKER_URL') or
                    self.first('broker_url', 'broker_host'))

    Como o python-dotenv injeta o .env em os.environ, um CELERY_BROKER_URL
    esquecido la venceria silenciosamente: o settings diria Postgres e o worker
    conectaria no Redis. O modo postgres reescreve a variavel para que os dois
    concordem.
    """
    import os

    base = _recarrega_settings(
        monkeypatch,
        BROKER_BACKEND="postgres",
        CELERY_BROKER_URL="redis://127.0.0.1:6379/0",
        CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1",
    )
    try:
        assert base.CELERY_BROKER_URL.startswith("sqla+postgresql")
        # O que o Celery vai efetivamente ler:
        assert os.environ["CELERY_BROKER_URL"].startswith("sqla+postgresql")
        assert os.environ["CELERY_RESULT_BACKEND"] == "django-db"
    finally:
        _recarrega_settings(monkeypatch, BROKER_BACKEND="redis")


def test_senha_com_caractere_especial_e_escapada(monkeypatch):
    """Uma senha com `@` ou `/` quebraria a URL, e o erro apareceria como
    falha de conexao com host inexistente."""
    base = _recarrega_settings(
        monkeypatch,
        BROKER_BACKEND="postgres",
        POSTGRES_PASSWORD="se@nha/com:especiais",
        POSTGRES_USER="u",
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT="5432",
        POSTGRES_DB="d",
    )
    try:
        assert "se%40nha%2Fcom%3Aespeciais" in base.CELERY_BROKER_URL
        assert base.CELERY_BROKER_URL.count("@") == 1
    finally:
        _recarrega_settings(monkeypatch, BROKER_BACKEND="redis")


@pytest.mark.parametrize(
    "broker,espera_visibility",
    [
        ("redis", True),
        ("postgres", False),
    ],
)
def test_visibility_timeout_so_e_aplicado_ao_redis(monkeypatch, broker, espera_visibility):
    """`visibility_timeout` e exclusivo do transporte Redis. O transporte
    `sqla` repassa transport_options direto ao create_engine() do SQLAlchemy,
    que rejeita argumentos desconhecidos com TypeError na inicializacao do
    worker — nao em runtime, e nao com mensagem que aponte a causa.
    """
    base = _recarrega_settings(monkeypatch, BROKER_BACKEND=broker, CELERY_BROKER_URL=None)
    try:
        tem = "visibility_timeout" in base.CELERY_BROKER_TRANSPORT_OPTIONS
        assert tem is espera_visibility
    finally:
        _recarrega_settings(monkeypatch, BROKER_BACKEND="redis")
