# ADR-0014 — Limiar de recuperacao calibrado por medicao

**Status:** Aceito
**Data:** 2026-08-26

## Contexto

A auditoria sugeriu `RAG_MAX_COSINE_DISTANCE = 0.35`, com a ressalva correta de
que o valor precisava ser calibrado empiricamente porque "distancia de cosseno
nao e comparavel entre modelos". A ressalva estava certa e a medicao mostrou o
tamanho do problema.

## Medicao

Feita com `intfloat/multilingual-e5-large` (o modelo do ADR-0005), consulta
`"monitoramento de pressao alta na gravidez"`, vetores normalizados em L2:

| Passagem | Distancia de cosseno |
|---|---|
| PT, diretamente relevante | 0.1193 |
| EN, diretamente relevante | 0.1445 |
| PT, tema tangente (aleitamento) | 0.1678 |
| PT, outra area (cultivo de soja) | 0.1911 |
| PT, absurdo (receita de bolo) | 0.2004 |

Duas leituras:

1. **O limiar de 0.35 deixaria passar 5 de 5**, incluindo a receita de bolo. O
   filtro seria decorativo, e a regra "se a similaridade for baixa, marcar como
   'requer novas fontes'" nunca dispararia.
2. **A recuperacao cross-lingual funciona**: a passagem em ingles (0.1445) fica
   mais proxima da consulta em portugues do que qualquer passagem em portugues
   de outro assunto.

Modelos da familia e5 comprimem a faixa de similaridade — a distancia entre
"perfeito" e "absurdo" e de apenas 0.08. Isso nao e defeito, e caracteristica,
e obriga um limiar apertado.

## Decisao

`RAG_MAX_COSINE_DISTANCE = 0.16`, por ficar entre a ultima passagem relevante
(0.1445) e a primeira irrelevante (0.1678).

Este valor e **um ponto de partida medido, nao uma verdade**. Ele:

- precisa ser recalibrado sempre que `EMBEDDING_MODEL` mudar;
- foi obtido com cinco passagens sinteticas, nao com o corpus real.

Para recalibrar existe `manage.py tenant_command calibrate_retrieval`, que
lista as distancias reais entre uma consulta e todo o corpus do tenant.

## Consequencias

- Os vetores sao **normalizados em L2 na gravacao**. Verificado: o modelo
  devolve norma proxima de 29, nao 1. Sem normalizar, os valores nao seriam
  comparaveis entre execucoes.
- `tests/test_embeddings_reais.py` inclui um teste que falha se o limiar for
  afrouxado a ponto de aceitar uma passagem irrelevante. Ele roda com
  `pytest -m integration` e nao pesa na suite normal.
- A suite normal usa `FakeEmbeddingClient`, deterministico: carregar 2 GB de
  modelo a cada execucao tornaria o ciclo de desenvolvimento inviavel. Isso nao
  reduz a cobertura das regras — o que depende do modelo esta nos testes de
  integracao.
- Com a faixa tao estreita, um proximo passo natural e um criterio **relativo**
  (margem sobre o melhor resultado) em vez de apenas absoluto. Nao foi feito
  agora para nao adicionar um parametro sem dados reais que o justifiquem.
