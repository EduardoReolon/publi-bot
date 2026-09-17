"""As tasks do beat rodando de onde o beat as despacha: o schema `public`.

Estes testes existem por causa de uma falha encontrada em uso, no primeiro
tenant recem-criado, com o sistema ainda vazio. O log do worker repetia a cada
minuto:

    Task apps.integrations.tasks.tick_publication_scheduler raised unexpected:
    ProgrammingError('relation "content_article" does not exist')

A causa nao estava na task. Uma task despachada de dentro de um tenant carrega
o `_schema_name` no cabecalho da mensagem e o worker restaura o search_path
antes de executar. O beat nao tem tenant: ele despacha do proprio processo,
onde o schema e o `public` — e `content_article` e tabela de TENANT, que existe
em cada schema de cliente e em nenhum lugar no `public`.

A suite anterior nao pegava isso porque chamava cada task ja de dentro de um
`schema_context`, que e como elas rodam quando uma PESSOA dispara a acao. O
caminho do beat — o unico pelo qual essas quatro tasks rodam de verdade — nao
era exercitado por ninguem.

Por isso todo teste aqui chama a task a partir do `public`, explicitamente.
"""

from __future__ import annotations

import pytest
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from apps.accounts.models import Tenant
from apps.accounts.varredura import para_cada_tenant, schemas_ativos

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A varredura
# ---------------------------------------------------------------------------
def test_varre_cada_tenant_ativo(tenant_factory):
    tenant_factory("cliente_um")
    tenant_factory("cliente_dois")
    visitados: list[str] = []

    with schema_context(get_public_schema_name()):
        para_cada_tenant(lambda: visitados.append(_schema_atual()) or 1, "teste")

    assert sorted(visitados) == ["cliente_dois", "cliente_um"]


def test_nao_varre_tenant_que_ainda_nao_tem_schema(tenant_factory):
    """Um tenant em `provisioning` nao tem tabela nenhuma, e um `failed` tem o
    schema pela metade: varrer qualquer um dos dois produz exatamente o erro
    que esta varredura existe para evitar."""
    ativo = tenant_factory("cliente_pronto")
    for situacao in (Tenant.Status.PROVISIONING, Tenant.Status.FAILED, Tenant.Status.SUSPENDED):
        parcial = tenant_factory(f"cliente_{situacao.value}")
        Tenant.objects.filter(pk=parcial.pk).update(status=situacao)

    with schema_context(get_public_schema_name()):
        schemas = schemas_ativos()

    assert schemas == [ativo.schema_name]


def test_o_public_nunca_entra_na_varredura(public_tenant):
    """O schema `public` nao tem as tabelas de tenant. Incluir a si mesmo na
    lista traria de volta, pela porta dos fundos, o erro original."""
    Tenant.objects.filter(pk=public_tenant.pk).update(status=Tenant.Status.ACTIVE)

    with schema_context(get_public_schema_name()):
        assert get_public_schema_name() not in schemas_ativos()


def test_um_tenant_que_falha_nao_impede_os_outros(tenant_factory, caplog):
    """Sem isto, uma unica linha estragada no primeiro cliente em ordem
    alfabetica congelaria a publicacao de todos os outros — e o unico sinal
    seria o silencio."""
    tenant_factory("aaa_quebrado")
    tenant_factory("zzz_saudavel")
    visitados: list[str] = []

    def rotina() -> int:
        schema = _schema_atual()
        visitados.append(schema)
        if schema == "aaa_quebrado":
            raise ValueError("algo especifico deste cliente")
        return 1

    with schema_context(get_public_schema_name()):
        total = para_cada_tenant(rotina, "teste")

    assert visitados == ["aaa_quebrado", "zzz_saudavel"]
    assert total == 1
    # O erro nao pode sumir junto: ele precisa aparecer com o nome do tenant.
    assert "aaa_quebrado" in caplog.text


# ---------------------------------------------------------------------------
# As quatro tasks do beat, chamadas de onde o beat as chama
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "caminho",
    [
        "apps.integrations.tasks.tick_publication_scheduler",
        "apps.integrations.tasks.check_publication_buffer",
        "apps.integrations.tasks.purge_expired_questions",
        "apps.ops.tasks.sweep_stalled_jobs",
    ],
)
def test_task_do_beat_roda_a_partir_do_public(caminho, tenant_factory):
    """Esta e a reproducao do defeito relatado.

    Antes da correcao, cada uma destas levantava
    `ProgrammingError: relation "..." does not exist` — porque o beat as
    despacha do `public`, e elas consultam tabelas que so existem nos schemas
    dos clientes.
    """
    from importlib import import_module

    tenant_factory("cliente_um")
    modulo, _, nome = caminho.rpartition(".")
    task = getattr(import_module(modulo), nome)

    with schema_context(get_public_schema_name()):
        resultado = task()

    # Tenant vazio: nada a fazer, e essa e a resposta certa — nao uma excecao.
    assert resultado == 0


def test_o_tique_publica_o_artigo_do_tenant_certo(tenant_factory, monkeypatch):
    """Nao basta nao levantar erro: o conteudo tem que ser encontrado.

    Uma varredura que passasse por todo tenant sem enxergar nada seria
    indistinguivel de uma correta enquanto o sistema estivesse vazio — e so
    apareceria como "o agendador nunca publica", muito depois.
    """
    from datetime import timedelta

    from apps.content.models import Article
    from apps.integrations import tasks

    tenant_factory("cliente_um")
    with schema_context("cliente_um"):
        artigo = Article.objects.create(
            title="Artigo aprovado",
            status=Article.Status.APPROVED_SCHEDULED,
            scheduled_for=timezone.now() - timedelta(minutes=5),
        )

    despachados: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tasks.publish_content,
        "delay",
        lambda tipo, identificador: despachados.append((tipo, str(identificador))),
    )

    with schema_context(get_public_schema_name()):
        total = tasks.tick_publication_scheduler()

    assert total == 1
    assert despachados == [("article", str(artigo.pk))]


def test_release_expired_leases_continua_rodando_no_public(public_tenant):
    """A excecao da regra, e por isso vale um teste proprio.

    As reservas ficam em `apps.inference`, que e app COMPARTILHADO: a tabela
    vive no `public` e e a mesma para todos os clientes. Varrer tenant por
    tenant aqui liberaria as mesmas reservas N vezes.

    Este teste esta aqui para que a diferenca seja deliberada. Sem ele, a
    proxima pessoa a ler as cinco entradas do beat veria quatro varrendo
    tenants e uma nao, e "corrigiria" a que esta certa.
    """
    from apps.ops.tasks import release_expired_leases

    with schema_context(get_public_schema_name()):
        assert release_expired_leases() == 0


def _schema_atual() -> str:
    from django.db import connection

    return connection.schema_name
