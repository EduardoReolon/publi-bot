# ADR-0001 — Proposito e escopo do produto

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O repositorio carregava duas identidades incompativeis. O `README.md` terminava
com *"Conceptualized and Built as an exploration of scalable AI workflows"* —
linguagem de portfolio — enquanto o corpo do mesmo documento descrevia um SaaS
multi-tenant com clientes pagantes. As duas coisas exigem arquiteturas
diferentes, e escolher errado custa meses.

A especificacao descrevia cerca de 9 dominios, 26 models, 15 tasks, 4 filas,
multi-tenancy, worker GPU remoto, motor de cadencia e duas implementacoes de
referencia do contrato de integracao. Isso e trabalho de varios meses-pessoa
para um desenvolvedor solo que ainda nao publicou um unico artigo.

## Decisao

Construir como **portfolio com caminho para produto**, com estas fronteiras:

**Dentro do escopo desde o inicio:**

- Multi-tenancy real, com um tenant por subdominio.
- Cadastro autonomo na home: subdominio desejado, e-mail e senha.
- Sites de terceiros reais desde o primeiro dia (hoje sites do proprio autor).
- Cada tenant com seus proprios usuarios vinculados.
- Autenticacao por e-mail e senha, com o modelo de dados ja preparado para
  migrar a um provedor OIDC externo (Zitadel) sem migration destrutiva.

**Fora do escopo, deliberadamente:**

- Cobranca, planos e faturamento.
- Cotas e limites de uso.
- Sistema de permissoes granulares alem de papeis por vinculo.
- Conformidade formal com a LGPD (DPA, ROPA, atendimento a titular).

**Regra de produto:** um tenant corresponde a um site. Um cliente com tres
sites tera tres tenants. Esta simplificacao e intencional: mantem a
configuracao de cada tenant minima e sem ambiguidade.

## Consequencias

- A configuracao do site (URL, chave de API, idioma de publicacao) mora num
  model dentro do schema do tenant, nao no model `Tenant`.
- Cada tenant tem seu proprio corpus de documentos. Um cliente com tres sites
  curara o mesmo artigo tres vezes. A auditoria mediu esse custo entre 5 e 15
  minutos de trabalho humano por documento — e o custo dominante do produto.
  Instrumentar `curation_seconds` e `review_seconds` desde o inicio nao e
  metrica de vaidade: e o que permite saber se o negocio fecha.
- O provisionamento de tenant nao pode ser sincrono. `CREATE SCHEMA` mais as
  migrations levam de segundos a mais de um minuto, e isso nao cabe dentro da
  request de cadastro.
- Sem cobranca, qualquer pessoa que alcance a home pode criar um schema.
  Enquanto o produto nao for publico, o cadastro exige convite.
