# ADR-0023 — Redes sociais como modulo isolado, no mesmo projeto

**Status:** Aceito
**Data:** 2026-10-04

## Contexto

Levar os artigos para LinkedIn, Instagram e Perfil da Empresa no Google e o
contrario da producao de artigos em estabilidade: e "como convencer pessoas",
e vai mudar muito, por tentativa. O dono pediu que a parte de redes fosse a
mais versatil e a mais separada possivel, e deixou em aberto um sistema a parte.

O que as redes precisam do PubliBot ja existe nele: os artigos (com fontes),
o perfil do negocio e as dores, o guia editorial, o modelo de linguagem com
fila e custo, os vetores, as perguntas e a fila de respostas, as conversoes do
site, o multi-tenant, o login, o Celery e a implantacao.

## Opcoes

1. **Outro sistema (outro repositorio, outro banco).** Separacao maxima, mas
   teria de refazer ou expor por API tudo o que esta acima, com autenticacao
   entre os dois, dois deploys e duas telas. Muito custo antes do primeiro post.
2. **Misturar nas apps de hoje** (`content`, `radar`). Barato agora, e a cada
   experimento as redes mexeriam no nucleo que ja esta estavel.
3. **Modulo isolado no mesmo projeto** ("monolito modular"). Uma app Django
   propria, com fronteira testada.

## Decisao

Opcao 3: `apps/social`, com duas regras conferidas por teste
(`apps/social/tests/test_fronteira.py`):

1. **O nucleo nao importa o modulo.** O modulo se declara no proprio
   AppConfig (rotas, menu, prompts, bloco na tela do artigo, pendencias) e o
   nucleo le essas declaracoes por `apps/ops/extensoes.py`. Tirar
   `apps.social` de `TENANT_APPS` tira tudo; o nucleo continua igual.
2. **O modulo so le o nucleo por uma porta**: `apps/social/fontes.py`
   (artigo, negocio, sinais, conversoes, modelo, vetores, termos proibidos,
   perguntas). Nenhum outro arquivo do modulo importa `content`, `knowledge`,
   `radar`, `editorial`, `integrations` ou `dados`.

O modulo guarda o id do artigo sem chave estrangeira: apagar um artigo nao
apaga o historico do que foi postado. Os testes moram dentro do modulo
(`apps/social/tests`).

Por dentro, o que mais muda e dado, nao codigo:

- cada **rede** e um modulo (`apps/social/redes/<rede>.py`) com formato,
  estilo do publico, publicador da API e conexao;
- o **jeito de escrever** de cada rede e um prompt no banco
  (`social_linkedin`, `social_instagram`, `social_gmn`), editavel sem deploy;
- as **abordagens** (o gancho: identificacao, surpresa, mito, erro comum...)
  sao linhas no banco, criadas e editadas pela tela, e um sorteio que aprende
  (Thompson sampling) escolhe a de cada post pelo resultado de cada conta.

## Consequencias

- Separar num servico, se um dia valer, e trocar `fontes.py` por um cliente
  de API: o resto do modulo nao muda.
- Uma mudanca no nucleo que quebre o modulo aparece nos testes do modulo, que
  rodam na mesma suite.
- O ponto de extensao (`apps/ops/extensoes.py`) serve a outros modulos
  opcionais do mesmo jeito.
