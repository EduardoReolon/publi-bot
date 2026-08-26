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
            provisioning_error=str(exc)[:2000],
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
