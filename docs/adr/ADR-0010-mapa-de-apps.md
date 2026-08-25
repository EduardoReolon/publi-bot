# ADR-0010 — Mapa canonico de apps e models

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

As analises anteriores produziram arvores de apps mutuamente incompativeis:
`Document` contra `SourceDocument`, `Node` contra `EndNode`, e tres nomes
diferentes para o mesmo mecanismo de retomada. Sem consolidacao, a primeira
migration nasceria com duas tabelas para a mesma entidade.

## Decisao

Cinco apps sob `apps/`, com nomes fixados.

### `apps/accounts` — schema `public`

| Model | Papel |
|---|---|
| `Tenant` | Um cliente, um schema, um site |
| `Domain` | Host que resolve para o tenant |
| `User` | Diretorio unico de usuarios |
| `TenantMembership` | Vinculo pessoa-tenant com papel |

### `apps/knowledge` — schema do tenant

`DocumentCategory`, `Document`, `SuperChunk`, `RetrievalQuery`, `RetrievalHit`.

O nome `SourceDocument` fica aposentado.

### `apps/content` — schema do tenant

`PromptTemplate`, `PromptVersion`, `PromptRun`, `TopicBatch`, `Topic`,
`Article`, `ArticleRevision`, `ArticleCitation`, `Question`, `Answer`,
`AnswerCitation`.

### `apps/integrations` — schema do tenant

`Site` (nome canonico; `Node`, `EndNode` e `Site` como sinonimos ficam
aposentados em favor deste), `SiteSeoSnapshot`, `SitePost`, `SiteApiCall`,
`PublicationSchedule`, `PublicationSlot`, `PublishAttempt`.

### `apps/ops` — schema do tenant, exceto onde indicado

`InferenceConnection`, `InferenceLease`, `GenerationJob` (**um unico mecanismo
de retomada**), `InferenceLog`, `Notification`, alem das views `healthz` e
`readyz` e do `RequestIDMiddleware`.

## Consequencias

- `apps/accounts` entra em `SHARED_APPS`; os demais em `TENANT_APPS`.
- Cada app so entra no `INSTALLED_APPS` quando for construido, para que
  `manage.py check` reflita o que existe de verdade.
