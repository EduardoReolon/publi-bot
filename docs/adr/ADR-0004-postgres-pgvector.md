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
- **Instalacao nativa, sem container, e o caminho padrao** — em
  desenvolvimento e em producao. No Ubuntu o pgvector esta no repositorio
  oficial da distribuicao (`postgresql-<versao>-pgvector`), entao o argumento
  que normalmente justifica containerizar o banco nao se aplica aqui.
  `scripts/setup-db.sh` faz a preparacao de forma idempotente.
- Isso mantem coerencia com o resto do deploy, que ja e nativo: virtualenv,
  Gunicorn, Nginx e systemd. Um banco em container no meio disso seria a peca
  fora do padrao.
- O `compose.yaml` permanece no repositorio como **alternativa**, util em um
  unico caso concreto: desenvolver no Windows, onde compilar o pgvector exige
  MSVC e os headers do PostgreSQL. Mesmo nesse caso ele sobe apenas
  PostgreSQL e Redis — o Django nunca e containerizado, para preservar
  depurador, recarga automatica e stack trace direto.
- Verificado nativamente: extensao `vector` 0.6.0 criada no schema
  `extensions`, coluna `vector(1024)`, indice HNSW com `vector_cosine_ops` e
  consulta por distancia de cosseno funcionando **de dentro de dois schemas de
  tenant diferentes** — que era exatamente o caso previsto como falha.
