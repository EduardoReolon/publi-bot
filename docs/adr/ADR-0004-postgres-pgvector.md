# ADR-0004 — PostgreSQL, pgvector e o indice HNSW

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O `settings.py` de scaffold apontava para SQLite, que nao tem tipo `vector`,
nao suporta indices HNSW ou IVFFlat, e trava sob escrita concorrente de
workers. `psycopg2-binary` e `pgvector` ja estavam instalados e nao eram usados
por nada.

## Decisao

PostgreSQL 17 com `pgvector`, acessado pelo backend do `django-tenants` (que e
o que emite o `SET search_path`). Driver `psycopg` versao 3.

A extensao `vector` e criada **uma unica vez**, no schema `extensions`, pelo
script de inicializacao em `deploy/postgres/init/01-extensions.sql`.

Indice **HNSW com distancia de cosseno**:

```python
HnswIndex(
    name="superchunk_embedding_hnsw",
    fields=["embedding"],
    m=16,
    ef_construction=64,
    opclasses=["vector_cosine_ops"],
)
```

**Nunca IVFFlat**, que exige treino sobre dados ja existentes e degrada com
insercao incremental — exatamente o padrao da curadoria manual deste produto.

## Consequencias

- Vetores sao normalizados em L2 na gravacao.
- O indice e criado depois da carga inicial. Abaixo de aproximadamente dez mil
  Super Chunks a varredura sequencial ja atende.
- `CONN_MAX_AGE = 60` no processo web; **`CONN_MAX_AGE = 0` nos processos do
  Celery**, para nao segurar conexao entre tarefas.
- Desenvolvimento local usa a imagem `pgvector/pgvector:pg17` via
  `compose.yaml`. Apenas a infraestrutura e containerizada; o Django roda no
  virtualenv nativo, preservando depurador e recarga automatica.
