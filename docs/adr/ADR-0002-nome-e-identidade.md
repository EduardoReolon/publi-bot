# ADR-0002 — Nome canonico do projeto

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

Havia quatro identidades simultaneas: o diretorio raiz chamava-se
`du-dudu-edu`, o titulo do README dizia "PubliBot", o comando de clone
apontava para `publi_bot`, e o pacote Django era o generico `core`. A
documentacao de deploy usava `nome_aplicacao` como literal.

O nome vira valores caros de trocar depois: o nome do app do Celery (que
aparece nas mensagens ja enfileiradas no broker), o nome do banco, o usuario de
sistema, as units do systemd, o caminho `/srv/<nome>`, o prefixo de chave do
Redis, o namespace do contrato de API e o dominio.

## Decisao

O nome canonico e **publibot**, em minusculas, sem separador.

- Slug e identificadores tecnicos: `publibot`
- Nome do app do Celery: `publibot`
- Namespace do contrato de API dos Nos Finais: `publibot/v1`
- Units do systemd: `publibot.service`, `celery-publibot.service`
- O pacote Django permanece `core` — convencao valida, e renomea-lo custaria
  mexer em todo import sem ganho nenhum.

## Consequencias

- `PROJECT_SLUG = "publibot"` fica em `core/settings/base.py` e e a fonte unica
  desse valor.
- O dominio ainda nao foi registrado. Tudo que depende dele le a variavel
  `ROOT_DOMAIN`, com `localhost` como valor de desenvolvimento.
