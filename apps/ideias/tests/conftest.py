"""Os testes da caixa de ideias usam a mesma infraestrutura da suite: tenant
de teste, cliente autenticado."""

from tests.conftest import (  # noqa: F401
    _exige_pgvector,
    _schema_modelo,
    _sem_bases_academicas,
    embedding_falso,
    public_tenant,
    tenant_factory,
    user,
)
from tests.test_interface import ambiente  # noqa: F401
