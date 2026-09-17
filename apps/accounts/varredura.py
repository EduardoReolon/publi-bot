"""Roda uma rotina dentro do schema de cada tenant.

Existe por causa do beat. Uma task despachada de dentro de um tenant carrega o
`_schema_name` no cabecalho da mensagem, e o worker restaura o search_path
antes de executar (ver `core/celery.py`). O beat nao tem tenant nenhum: ele
despacha do proprio processo, onde o schema e sempre o `public`.

O efeito disso e assimetrico e por isso enganoso. Uma task agendada que so toca
tabela compartilhada — `release_expired_leases`, por exemplo — funciona
perfeitamente no `public`. Uma que toca tabela de tenant quebra na hora, com
`relation "content_article" does not exist`, porque essas tabelas existem em
CADA schema de cliente e em nenhum lugar no `public`.

Nao ha como "rodar no tenant certo": nao existe tenant certo. Toda tarefa
periodica de dominio precisa passar por TODOS eles. E o que esta funcao faz.

Um tenant que falha nao interrompe os demais. Sem isso, uma unica linha
estragada no primeiro cliente em ordem alfabetica congelaria a publicacao de
todos os outros — e o unico sinal seria o silencio.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger("publibot.accounts")


def schemas_ativos() -> list[str]:
    """Os schemas que devem receber trabalho periodico.

    So os ATIVOS. Um tenant em `provisioning` ainda nao tem tabela nenhuma, e
    um `failed` tem o schema pela metade: varrer qualquer um dos dois produz
    exatamente o erro que esta funcao existe para evitar. Um `suspended` tem as
    tabelas, mas suspender um cliente e justamente parar de trabalhar para ele.
    """
    from apps.accounts.models import Tenant

    # A consulta vai explicitamente para o `public`. `Tenant` e compartilhado e
    # seria alcancavel de dentro de um tenant pelo search_path, mas depender
    # disso significaria que a ordem de chamada muda o resultado.
    with schema_context(get_public_schema_name()):
        return list(
            Tenant.objects.filter(status=Tenant.Status.ACTIVE)
            .exclude(schema_name=get_public_schema_name())
            .order_by("schema_name")
            .values_list("schema_name", flat=True)
        )


def para_cada_tenant(rotina: Callable[[], int], nome: str) -> int:
    """Roda `rotina()` em cada tenant ativo e soma o que cada um devolveu.

    `nome` aparece no log quando um tenant falha; sem ele a mensagem diria que
    algo quebrou sem dizer o que estava rodando.
    """
    total = 0

    for schema in schemas_ativos():
        with schema_context(schema):
            try:
                total += rotina() or 0
            except Exception:
                # Amplo de proposito: o proximo tenant precisa rodar, qualquer
                # que tenha sido o problema deste. E `exception`, nao `error`:
                # sem o traceback, um erro que so acontece num cliente vira um
                # bilhete sem endereco.
                logger.exception("%s falhou no tenant %s; seguindo para o proximo.", nome, schema)

    return total
