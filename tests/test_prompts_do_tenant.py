"""Um tenant recem-criado precisa nascer com os prompts.

Encontrado em uso, no primeiro clique em "gerar artigo":

    LookupError: nenhuma versao ativa para o prompt 'consensus_filter'

`garantir_prompts_padrao` existia e era correta. Ninguem em producao a
chamava: so os testes, na fixture. `migrate_schemas` cria as TABELAS de
`apps.content`; as linhas ficavam por conta de uma funcao que so rodava na
suite.

O sintoma e tardio e desnorteante — cadastro funciona, telas abrem, acervo
indexa, pauta e criada. O sistema so revela que nunca teve com o que gerar
depois que a pessoa marcou trechos, escreveu a pauta e clicou.

Por isso NENHUM teste aqui chama `garantir_prompts_padrao`: eles provisionam
como a aplicacao provisiona e conferem o resultado. Chamar a semeadora a mao
aqui reproduziria exatamente o engano que deixou o defeito passar.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django_tenants.utils import schema_context

from apps.accounts.models import Tenant
from apps.content.models import PromptVersion
from apps.content.prompts_iniciais import PROMPTS_INICIAIS

pytestmark = pytest.mark.django_db


def test_o_comando_de_provisionamento_deixa_o_tenant_com_prompts(public_tenant):
    call_command("provision_tenant", "com_prompts", "--name=Com Prompts", verbosity_schema=0)

    with schema_context("com_prompts"):
        chaves = set(PromptVersion.objects.values_list("template__key", flat=True))

    assert chaves == set(PROMPTS_INICIAIS)


def test_a_task_de_provisionamento_tambem(public_tenant):
    """Os dois caminhos existem: a tela de cadastro usa a task, o terminal usa
    o comando. Consertar um so deixaria metade dos tenants quebrada."""
    from apps.accounts.tasks import provision_tenant

    tenant = Tenant.objects.create(
        schema_name="via_task",
        name="Via Task",
        slug="via-task",
        status=Tenant.Status.PROVISIONING,
    )

    provision_tenant(str(tenant.pk))

    with schema_context("via_task"):
        chaves = set(PromptVersion.objects.values_list("template__key", flat=True))

    assert chaves == set(PROMPTS_INICIAIS)


def test_o_prompt_que_quebrou_esta_entre_eles(public_tenant):
    """`consensus_filter` e o segundo passo da geracao — o primeiro que usa
    modelo. Ele e o que falhou em uso, e merece nome proprio aqui."""
    call_command("provision_tenant", "com_consenso", "--name=X", verbosity_schema=0)

    with schema_context("com_consenso"):
        versao = PromptVersion.objects.filter(
            template__key="consensus_filter", is_active=True
        ).first()

    assert versao is not None
    assert versao.system_prompt.strip()


def test_semear_prompts_conserta_um_tenant_que_ja_existia(tenant_factory, capsys):
    """O provisionamento so alcanca quem vier depois. Quem ja existe precisa
    deste comando — e foi o caso de todos os tenants criados ate aqui."""
    tenant = tenant_factory("sem_prompts")

    with schema_context(tenant.schema_name):
        assert not PromptVersion.objects.exists()

        call_command("semear_prompts")

        assert PromptVersion.objects.count() == len(PROMPTS_INICIAIS)

    assert "criado" in capsys.readouterr().out


def test_rodar_de_novo_nao_duplica_nem_sobrescreve(tenant_factory, capsys):
    """Os prompts vivem no banco justamente para serem ajustados pela tela
    (ADR-0012). Um deploy que sobrescrevesse o ajuste desfaria o trabalho de
    quem calibrou o texto."""
    tenant = tenant_factory("ja_semeado")

    with schema_context(tenant.schema_name):
        call_command("semear_prompts")

        versao = PromptVersion.objects.filter(template__key="consensus_filter").first()
        versao.system_prompt = "Texto ajustado a mao pela tela."
        versao.save(update_fields=["system_prompt"])

        call_command("semear_prompts")

        assert PromptVersion.objects.count() == len(PROMPTS_INICIAIS)
        versao.refresh_from_db()
        assert versao.system_prompt == "Texto ajustado a mao pela tela."

    assert "ja existem" in capsys.readouterr().out


def test_todos_alcanca_cada_tenant_ativo(tenant_factory):
    """E assim que o release.sh chama: uma implantacao conserta quem ficou sem
    prompts, sem ninguem precisar lembrar de cada schema."""
    um = tenant_factory("cliente_um")
    dois = tenant_factory("cliente_dois")

    call_command("semear_prompts", todos=True)

    for tenant in (um, dois):
        with schema_context(tenant.schema_name):
            assert PromptVersion.objects.count() == len(PROMPTS_INICIAIS)


def test_provisionar_nao_falha_se_a_semeadura_falhar(public_tenant, monkeypatch):
    """Um tenant sem prompts se conserta com um comando; um tenant preso em
    "provisionando" nao se conserta pela tela."""
    monkeypatch.setattr(
        "apps.content.services.garantir_prompts_padrao",
        lambda: (_ for _ in ()).throw(RuntimeError("banco cheio")),
    )

    call_command("provision_tenant", "apesar_do_erro", "--name=X", verbosity_schema=0)

    tenant = Tenant.objects.get(schema_name="apesar_do_erro")
    assert tenant.status == Tenant.Status.ACTIVE
