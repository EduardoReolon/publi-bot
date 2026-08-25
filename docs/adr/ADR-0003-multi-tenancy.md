# ADR-0003 — Multi-tenancy por schema

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O `README.md` listava "Django-Tenants" na stack e mandava rodar
`manage.py migrate_schemas`, enquanto o `ARCHITECTURE.md` afirmava que a stack
usava *"a biblioteca padrao de multi-tenancy do Django (esquemas baseados em
banco)"*. **O Django nao possui multi-tenancy nativo**, nem por schema nem por
linha; a afirmacao era factualmente falsa. Na pratica nada existia: nem
`django-tenants` instalado, nem roteador, nem middleware.

Duas opcoes reais:

- **Schema por tenant** (`django-tenants`): isolamento forte no banco.
- **Row-level** (`tenant_id` como chave estrangeira em toda entidade).

## Decisao

**Schema por tenant, com `django-tenants`.** Cada tenant e um schema do
PostgreSQL, alcancado por um subdominio do dominio raiz.

Esta decisao contraria a recomendacao da auditoria tecnica, que preferia
row-level pelos custos operacionais listados abaixo. Foi tomada com esses
custos na mesa, porque o isolamento no banco e o alinhamento natural entre
subdominio, schema e Organization do Zitadel valem mais para este produto.

## Consequencias

Os tres custos conhecidos, e como cada um e tratado:

1. **A extensao `vector` nao se replica por schema.** Instalada apenas no
   `public`, a migration do segundo tenant falha com *"type vector does not
   exist"*. Tratamento: a extensao vive num schema dedicado `extensions`,
   incluido no search_path de toda conexao via
   `PG_EXTRA_SEARCH_PATHS = ["extensions"]`.

2. **Tasks do Celery nao tem request**, entao o `TenantMainMiddleware` nunca
   roda dentro de um worker. Sem tratamento, toda task executaria contra
   `public` — e o resultado nao seria um erro barulhento, seria gravar dado de
   um cliente no schema de outro, em silencio. Tratamento: `tenant-schemas-celery`
   carrega o `_schema_name` no cabecalho da mensagem no despacho e restaura o
   search_path antes e depois de cada execucao. **Verificado com broker e
   worker reais**, e travado nos testes de `tests/test_tenant_isolation.py`.

3. **Cada tenant ganha seu proprio indice HNSW**, que e residente em memoria.
   N tenants multiplicam a RAM do PostgreSQL. Tratamento: o indice so e criado
   acima de um volume minimo de chunks; abaixo de aproximadamente dez mil
   vetores a varredura sequencial ja e rapida o bastante.

Alem disso:

- Corpus, prompts e configuracao **nao atravessam tenants**. Dois sites do
  mesmo cliente nao compartilham base de conhecimento.
- `manage.py migrate` puro nao percorre os schemas dos tenants. O comando
  correto e `migrate_schemas`.
- O `search_path` dentro de um tenant e `<schema>, public, extensions`, entao
  tabelas que existem so no `public` continuam visiveis de dentro do tenant.
  E isso que viabiliza o ADR-0006.

## Correcoes de documentacao exigidas

- `ARCHITECTURE.md:47` — remover a afirmacao sobre multi-tenancy nativa.
- `README.md:61` e `README.md:86` — ja corrigidos na reescrita.
