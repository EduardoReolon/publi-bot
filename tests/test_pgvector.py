"""Testes do pgvector sob schema por tenant.

Existem por causa de um modo de falha muito especifico e caro: a extensao
`vector` NAO se replica por schema. Instalada apenas no `public`, o PRIMEIRO
tenant funciona e o SEGUNDO falha ao migrar, com "type vector does not exist".

E uma falha que so aparece quando ja existe cliente no ar.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import schema_context
from psycopg import sql


@pytest.mark.django_db
def test_extensao_vector_esta_no_schema_extensions():
    """Se alguem instalar a extensao no `public`, este teste falha — e e essa
    a intencao. O lugar correto e um schema dedicado, incluido no search_path
    de toda conexao via PG_EXTRA_SEARCH_PATHS."""
    connection.set_schema_to_public()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.nspname
              FROM pg_extension e
              JOIN pg_namespace n ON n.oid = e.extnamespace
             WHERE e.extname = 'vector'
            """
        )
        row = cursor.fetchone()

    assert row is not None, (
        "extensao 'vector' ausente. Rode ./scripts/setup-db.sh "
        "(ou instale postgresql-<versao>-pgvector)"
    )
    assert row[0] == "extensions", (
        f"extensao 'vector' esta no schema '{row[0]}', deveria estar em "
        f"'extensions'. No schema public ela nao fica alcancavel para o "
        f"segundo tenant."
    )


@pytest.mark.django_db
def test_tipo_vector_utilizavel_de_dentro_de_dois_tenants(_exige_pgvector, tenant_factory):
    """O caso que a auditoria previu como falha.

    Nao basta o primeiro tenant funcionar: e no segundo que o problema
    aparece, porque ele e criado depois e nao herda nada do primeiro.
    """
    primeiro = tenant_factory("vec_um")
    segundo = tenant_factory("vec_dois")

    for tenant in (primeiro, segundo):
        # O PostgreSQL nao aceita parametro no lugar de um IDENTIFICADOR, entao
        # o nome do schema precisa ser composto. `sql.Identifier` faz o
        # escapamento correto — interpolar com f-string aqui seria injecao de
        # SQL, e este mesmo padrao vai reaparecer no codigo de producao.
        tabela = sql.Identifier(tenant.schema_name, "v")

        with schema_context(tenant.schema_name), connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE TABLE {} (id serial PRIMARY KEY, emb vector(3))").format(tabela)
            )
            cursor.execute(
                sql.SQL("INSERT INTO {} (emb) VALUES (%s), (%s)").format(tabela),
                ["[1,0,0]", "[0,1,0]"],
            )
            cursor.execute(
                sql.SQL("SELECT emb <=> %s FROM {} ORDER BY 1").format(tabela), ["[1,0,0]"]
            )
            distancias = [round(float(r[0]), 4) for r in cursor.fetchall()]

        assert distancias[0] == pytest.approx(0.0), (
            f"distancia de cosseno para si mesmo deveria ser 0 em "
            f"{tenant.schema_name}, veio {distancias[0]}"
        )
        assert distancias[1] == pytest.approx(1.0), (
            f"vetores ortogonais deveriam ter distancia 1 em "
            f"{tenant.schema_name}, veio {distancias[1]}"
        )


@pytest.mark.django_db
def test_indice_hnsw_com_cosseno_pode_ser_criado(_exige_pgvector, tenant_factory):
    """HNSW e a escolha do ADR-0004. IVFFlat exigiria treino sobre dados ja
    existentes e degradaria com insercao incremental — que e exatamente o
    padrao da curadoria manual deste produto."""
    tenant = tenant_factory("vec_hnsw")

    tabela = sql.Identifier(tenant.schema_name, "v")

    with schema_context(tenant.schema_name), connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE TABLE {} (id serial, emb vector(1024))").format(tabela))
        cursor.execute(
            sql.SQL(
                "CREATE INDEX v_hnsw ON {} USING hnsw (emb vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            ).format(tabela)
        )
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = 'v_hnsw'",
            [tenant.schema_name],
        )
        indexdef = cursor.fetchone()[0]

    assert "hnsw" in indexdef.lower()
    assert "vector_cosine_ops" in indexdef
