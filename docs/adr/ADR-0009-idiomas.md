# ADR-0009 — Ingles no codigo, portugues na interface

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

Havia tres idiomas sem regra: README em ingles, arquitetura em portugues,
`LANGUAGE_CODE = 'en-us'`, e a especificacao nomeava estados de negocio em
portugues — `"Aprovado e Agendado"`, `"Requer Ingestao de Novos Documentos"`,
`"Pendente"`.

**O valor de um `choices` e gravado em cada linha da tabela** e aparece em toda
consulta, todo filtro do painel, toda condicao de task, e no payload trocado
com os Nos Finais. Troca-lo depois exige migration de dados em todas as tabelas
de conteudo e quebra toda integracao ja instalada.

Ha ainda um site em italiano no horizonte.

## Decisao

- **Identificadores, nomes de app, model e campo, valores de `choices` e chaves
  do contrato de API: ingles em snake_case.**
- **Rotulos, mensagens e interface: portugues do Brasil, via gettext.**
- `LANGUAGE_CODE = "pt-br"`, com `locale/` configurado desde o primeiro commit.

O `locale/` existe desde o inicio de proposito: adaptar um painel ja escrito em
portugues para usar gettext depois exige revarrer template por template.

## Consequencias

- Idiomas previstos: `pt-br`, `en`, `it`. Adicionar o italiano sera acrescentar
  um arquivo `.po`, sem tocar em codigo.
- O contrato publico dos Nos Finais fica em ingles, que e o que se espera de
  quem for implementa-lo em qualquer linguagem.
- Existem tres eixos de idioma distintos, e confundi-los produz modelagem
  errada:

  | Eixo | Onde vive | Varia por |
  |---|---|---|
  | Idioma do documento-fonte | `Document.language` | Documento |
  | Idioma do conteudo publicado | `Site.content_language` | Site |
  | Idioma da interface | preferencia do usuario | Pessoa |

- **O modelo de embedding nao entra nessa tabela**: ele e multilingue e unico
  para toda a instalacao. Ver ADR-0005.
- Uma consequencia menos obvia: a verificacao de sobreposicao literal entre o
  artigo gerado e a fonte so funciona quando ambos compartilham idioma. Com
  fonte em ingles e artigo em portugues, a sobreposicao de n-gramas e
  estruturalmente zero, e essa protecao para de atuar em fontes estrangeiras.
