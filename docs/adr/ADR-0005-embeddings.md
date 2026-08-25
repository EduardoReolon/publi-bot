# ADR-0005 — Modelo de embedding e onde ele roda

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O corpus e multilingue: literatura cientifica em ingles, portugues e
eventualmente outros idiomas, com publicacao em portugues e, no futuro,
italiano. Isso elimina modelos monolingues.

Existe aqui uma **assimetria que decide a arquitetura**: indexar um documento
pode esperar a GPU acordar; **consultar nao pode**. Todo retrieval precisa
embutir a consulta com exatamente o mesmo modelo do indice. Se o embedding
vivesse apenas na GPU local, buscar no pgvector com o computador desligado
seria impossivel, e o fluxo reativo de perguntas e respostas pararia inteiro.

A auditoria recomendava `bge-m3` via `fastembed`. **Verificacao no proprio
pacote mostrou que `fastembed` 0.8.0 nao inclui `BAAI/bge-m3`** no registro de
modelos de texto. Os multilingues que ele entrega sao:

| Modelo | Dim | Truncamento | Prefixos | Licenca |
|---|---|---|---|---|
| `intfloat/multilingual-e5-large` | 1024 | 512 tokens | obrigatorios | MIT |
| `jinaai/jina-embeddings-v3` | 1024 | 1024 tokens | nao precisa | **cc-by-nc-4.0** |
| `paraphrase-multilingual-mpnet-base-v2` | 768 | — | nao | Apache |

`jina-embeddings-v3` e licenciado para uso nao comercial, o que colide com o
caminho comercial do ADR-0001.

## Decisao

**`intfloat/multilingual-e5-large`, 1024 dimensoes, rodando na nuvem em CPU**
via `fastembed` (ONNX, sem torch).

O argumento decisivo nao e qualidade comparada, e reversibilidade:
`multilingual-e5-large` e `bge-m3` tem **ambos 1024 dimensoes**. Trocar de
modelo depois nao exige `ALTER TYPE` nem recriar o indice HNSW — exige apenas
re-embutir o corpus. A decisao cara (a dimensao) fica travada em 1024 e serve
aos dois.

## Consequencias

- **Retrieval sempre disponivel**, independentemente da GPU estar ligada.
- A VRAM da GPU fica integralmente para geracao de texto — o que importa muito
  numa placa de 8 GB.
- **O modelo trunca em 512 tokens sem emitir erro.** O excedente e descartado
  em silencio. Por isso `EMBEDDING_MAX_TOKENS = 480` e validado com o
  tokenizador real do modelo, nunca por contagem de caracteres, e o `SuperChunk`
  e um-para-muitos por documento (abstract e conclusao viram chunks separados).
- **Os prefixos `query: ` e `passage: ` sao obrigatorios.** Esquece-los nao
  gera erro: derruba a revocacao em silencio. Por isso o cliente de embedding
  expoe apenas `embed_query()` e `embed_passage()`, e **nao existe** um metodo
  `embed()` cru onde alguem possa errar.
- `embedding_model` e `embedding_dim` sao gravados **por linha**, permitindo
  que duas geracoes convivam durante uma migracao de modelo.
- O modelo de embedding **nao varia por tenant**. Ele ja e multilingue: uma
  consulta em portugues encontra um abstract em ingles no mesmo espaco
  vetorial. Um modelo por tenant produziria espacos incomparaveis e
  multiplicaria 2,24 GB de RAM por tenant, sem ganho.
- O download do modelo (~2,24 GB) acontece em tempo de execucao e e cacheado em
  `.model_cache/`, fora do controle de versao.
