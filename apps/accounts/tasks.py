"""Provisionamento assincrono de tenants.

Por que assincrono: criar um schema e rodar as migrations dentro dele leva de
segundos a mais de um minuto. Fazer isso dentro da request de cadastro daria
timeout no Nginx e deixaria a pessoa olhando para uma tela travada — por isso
`Tenant.auto_create_schema = False` (ADR-0001).

Estas tasks rodam SEMPRE no schema `public`: e la que vivem Tenant, Domain e
User. O tenant-schemas-celery propaga o schema de quem despachou, entao o
despacho precisa acontecer a partir do public — o que e naturalmente o caso,
ja que o cadastro acontece no dominio raiz.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("publibot.accounts")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    # A fonte da verdade do provisionamento e a coluna `status` do proprio
    # Tenant, que a tela de espera consulta — o AsyncResult nunca e lido.
    #
    # Desligar o resultado nao e economia de espaco: e o que impede o despacho
    # de travar a request de cadastro. Em `Celery.send_task` existe
    #
    #     if not ignore_result:
    #         self.backend.on_task_call(P, task_id)
    #
    # e, com o Redis como result backend, esse `on_task_call` abre a conexao do
    # consumidor de resultados. Com o Redis fora do ar ele nao falha: entra num
    # laco de 20 tentativas de 1s. Medido — o `.delay()` bloqueou 19,5s dentro
    # do `on_commit` do cadastro e terminou em RuntimeError, deixando o tenant
    # em "provisionando" para sempre. Com `ignore_result`, a mesma situacao
    # falha em 0,66s com um OperationalError que da para tratar.
    ignore_result=True,
)
def provision_tenant(self, tenant_id: str) -> str:
    """Cria o schema do tenant e aplica as migrations dentro dele.

    Recebe apenas o id — nunca o objeto. Uma task que carrega objetos
    serializados trabalha sobre um retrato do passado; carregando do banco, ela
    sempre ve o estado atual.

    Idempotente: se o schema ja existe, `create_schema(check_if_exists=True)`
    nao refaz o trabalho. Isso importa porque `acks_late=True` significa que uma
    task pode ser reentregue apos uma queda do worker.
    """
    from apps.accounts.models import Tenant

    tenant = Tenant.objects.get(pk=tenant_id)

    if tenant.status == Tenant.Status.ACTIVE:
        logger.info("Tenant %s ja esta ativo; nada a fazer.", tenant.schema_name)
        return tenant.schema_name

    try:
        tenant.create_schema(check_if_exists=True, verbosity=0)
    except Exception as exc:
        # Guardar o erro no proprio tenant e o que permite a tela de status
        # dizer o que houve, em vez de girar para sempre.
        logger.exception("Falha ao provisionar o tenant %s", tenant.schema_name)
        Tenant.objects.filter(pk=tenant.pk).update(
            status=Tenant.Status.FAILED,
            provisioning_error=_com_pista(str(exc))[:2000],
        )
        # Uma falha transitoria (banco reiniciando, conexao caida) merece nova
        # tentativa; depois dos retries o estado FAILED permanece visivel.
        raise self.retry(exc=exc) from exc

    with transaction.atomic():
        Tenant.objects.filter(pk=tenant.pk).update(
            status=Tenant.Status.ACTIVE,
            provisioned_at=timezone.now(),
            provisioning_error="",
        )

    logger.info("Tenant %s provisionado.", tenant.schema_name)
    return tenant.schema_name


def despachar_provisionamento(tenant_id: str, schema_name: str) -> None:
    """Publica a task de provisionamento sem deixar o cadastro cair junto.

    Roda dentro de um `transaction.on_commit`, ou seja, depois do COMMIT e
    ainda na thread da request. Uma excecao aqui ja nao desfaz nada — o tenant,
    o usuario e o vinculo estao gravados — mas sobe como erro 500 para quem
    acabou de se cadastrar, sem dizer o que houve, e deixa o registro parado em
    "provisionando" sem nenhum rastro da causa.

    Broker fora do ar e o caso concreto: `kombu.exceptions.OperationalError`.
    Gravar o motivo no proprio tenant e o que permite a tela de espera dizer
    "nao foi possivel falar com a fila" em vez de girar por tres minutos.
    """
    from apps.accounts.models import Tenant

    try:
        provision_tenant.delay(tenant_id)
    except Exception as exc:
        logger.exception("Falha ao despachar o provisionamento de %s", schema_name)
        Tenant.objects.filter(pk=tenant_id).update(
            status=Tenant.Status.FAILED,
            provisioning_error=f"Nao foi possivel publicar na fila: {exc}"[:2000],
        )


def _com_pista(erro: str) -> str:
    """Acrescenta a causa quando a mensagem do Postgres nao a revela.

    `type "vector" does not exist` chega assim, dentro de um `CREATE TABLE` de
    trinta colunas, sem dizer que falta uma EXTENSAO — nem qual, nem como
    instalar. E o primeiro erro que qualquer pessoa encontra ao apontar o
    projeto para um PostgreSQL recem-instalado, e a mensagem crua nao ajuda
    ninguem a sair dele.
    """
    if 'type "vector" does not exist' in erro or 'tipo "vector"' in erro:
        return (
            f"{erro}\n\n"
            "A extensao pgvector nao esta instalada ou nao esta alcancavel. "
            "Rode  python manage.py check_db  para ver o diagnostico e os "
            "comandos exatos."
        )
    return erro
