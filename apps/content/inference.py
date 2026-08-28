"""Executa um prompt versionado contra uma conexao de inferencia.

Este e o unico lugar do sistema que junta as quatro coisas que uma chamada ao
modelo precisa: a versao do prompt (do banco, nao do codigo), a escolha da
conexao, a reserva de capacidade e o registro do que aconteceu.

Concentrar isso aqui e o que sustenta duas promessas do projeto. A primeira e
poder trocar de modelo sem tocar em nada do fluxo (ADR-0008, o motivo de nao
haver LangChain). A segunda e a contabilidade: toda chamada vira um
`InferenceLog` e um `PromptRun`, entao da para responder quanto custou um
artigo e qual versao de prompt o produziu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import connection as conexao_do_banco

from apps.content.models import PromptRun, PromptVersion
from apps.content.services import escolher_versao_de_prompt
from apps.inference.leases import (
    SemCapacidade,
    escolher_conexao,
    gerar_owner_key,
    registrar_falha,
    registrar_sucesso,
    reserva,
)
from apps.inference.models import InferenceConnection
from apps.inference.providers.base import (
    ProviderPermanentError,
    ProviderTransientError,
    get_provider,
)
from apps.ops.models import InferenceLog
from apps.ops.orchestrator import PassoAdiado

logger = logging.getLogger("publibot.content")


def _tenant_atual():
    """Resolve o Tenant real a partir do schema em uso.

    Nao serve usar `connection.tenant` direto: dentro de um `schema_context` o
    django-tenants coloca ali um `FakeTenant`, que so carrega o `schema_name`.
    Passar esse objeto a um filtro por chave estrangeira levanta
    `ValidationError: nao e um UUID valido` — erro que aponta para o tipo do
    valor e nao para a causa.

    O `Tenant` vive no schema public, que esta no search_path de toda conexao,
    entao a consulta funciona de dentro do tenant sem trocar de schema.
    """
    from django_tenants.utils import get_public_schema_name

    from apps.accounts.models import Tenant

    schema = conexao_do_banco.schema_name
    if schema == get_public_schema_name():
        return None
    return Tenant.objects.filter(schema_name=schema).first()


@dataclass(frozen=True)
class ResultadoDoPrompt:
    texto: str
    prompt_run: PromptRun
    versao: PromptVersion
    conexao: InferenceConnection


class SemModeloConfigurado(RuntimeError):
    """Nenhuma conexao sabe executar esta carga, ou nenhuma foi cadastrada.

    Diferente de `PassoAdiado`: ali existe capacidade e ela esta ocupada; aqui
    nao ha o que esperar, porque nada foi configurado.
    """


def executar_prompt(
    *,
    key: str,
    variaveis: dict[str, str],
    site=None,
    job=None,
    json_schema: dict | None = None,
    workload: str = InferenceConnection.Workload.TEXT,
) -> ResultadoDoPrompt:
    """Roda um prompt e devolve o texto cru do modelo.

    Nao interpreta a resposta: quem chamou sabe o que esperar. O
    `consensus_filter` espera JSON e tem `interpretar_tese` para isso; a
    redacao espera Markdown com marcadores e tem `aplicar_rascunho`.

    Levanta `PassoAdiado` quando todas as conexoes estao ocupadas — o
    orquestrador entende isso como "tente de novo mais tarde" e **nao** gasta
    uma tentativa, porque nada deu errado.
    """
    versao = escolher_versao_de_prompt(
        key, site_overrides=getattr(site, "model_overrides", None) or None
    )

    tenant = _tenant_atual()

    conexao = escolher_conexao(workload=workload, tenant=tenant, model_name=versao.model_name or "")
    if conexao is None:
        if not InferenceConnection.objects.filter(is_active=True).exists():
            raise SemModeloConfigurado(
                "nenhuma conexao de inferencia ativa cadastrada. "
                "Cadastre uma em Configuracao > Inferencia."
            )
        # Existem conexoes, mas nenhuma com vaga ou fora do circuito aberto.
        raise PassoAdiado("todas as conexoes de inferencia estao ocupadas", tentar_em_segundos=120)

    modelo = versao.model_name or conexao.default_model
    if not modelo:
        raise SemModeloConfigurado(
            f"a versao do prompt {key!r} nao define modelo e a conexao "
            f"{conexao.name!r} nao tem modelo padrao."
        )

    cliente = get_provider(conexao)
    corpo = versao.user_prompt_template.format(**variaveis)

    try:
        with reserva(conexao, owner_key=gerar_owner_key(), model_name=modelo):
            resposta = cliente.chat(
                model=modelo,
                system=versao.system_prompt,
                user=corpo,
                temperature=versao.temperature,
                max_tokens=versao.max_tokens,
                json_schema=json_schema,
            )
    except SemCapacidade as exc:
        # Corrida: a vaga existia na escolha e sumiu antes da reserva.
        raise PassoAdiado(str(exc), tentar_em_segundos=120) from exc
    except (ProviderTransientError, ProviderPermanentError) as exc:
        registrar_falha(conexao)
        InferenceLog.objects.create(
            connection=conexao,
            job=job,
            model_name=modelo,
            workload=workload,
            succeeded=False,
            error=str(exc)[:2000],
        )
        if isinstance(exc, ProviderTransientError):
            # Fora do ar agora nao significa fora do ar sempre.
            raise PassoAdiado(f"provedor indisponivel: {exc}", tentar_em_segundos=300) from exc
        raise

    registrar_sucesso(conexao)

    InferenceLog.objects.create(
        connection=conexao,
        job=job,
        model_name=resposta.model or modelo,
        workload=workload,
        input_tokens=resposta.input_tokens,
        output_tokens=resposta.output_tokens,
        latency_ms=resposta.latency_ms,
        succeeded=True,
    )

    prompt_run = PromptRun.objects.create(
        prompt_version=versao,
        input_tokens=resposta.input_tokens,
        output_tokens=resposta.output_tokens,
        latency_ms=resposta.latency_ms,
    )

    logger.info(
        "Prompt %s v%s executado em %s (%sms, %s tokens de saida)",
        key,
        versao.version,
        conexao.name,
        resposta.latency_ms,
        resposta.output_tokens,
    )

    return ResultadoDoPrompt(
        texto=resposta.text, prompt_run=prompt_run, versao=versao, conexao=conexao
    )
