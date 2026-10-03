# Dados públicos

Números oficiais (IBGE, Banco Central, DATASUS...) que os artigos citam com
o valor exato, o período, o local e o link da instituição. Diferente das
fontes do acervo, que são texto para o modelo ler: um dado é um número, e o
modelo nunca o escreve de memória.

## Como funciona

```
catálogo (schema public)                  cliente (schema do tenant)
─────────────────────────                 ───────────────────────────
Instituição ─ adaptador                   Pauta ── DadoDaPauta (escolha)
   └─ Série ─ vetor (descrição)                         │
        └─ Valor (cache)        ──fato──▶  Article.dados_usados (congelado)
PedidoDeAdaptador                                       │
                                          [[DADO_N]] no texto → "(IBGE, 2019)"
                                          + referência com link; conferência do número
```

1. **Catálogo, no public.** Fatos iguais para todos os clientes: buscados e
   vetorizados uma vez. Curadoria (aprovar série, cadastrar à mão, marcar
   instituição confiável) só para superusuário. Tela: menu **Dados**.
2. **Um adaptador por instituição** (`apps/dados/adaptadores.py`). A estrutura
   muda por instituição, não por série: o adaptador do IBGE cobre todas as
   tabelas do SIDRA. Ele traduz o formato do lugar para `SerieEncontrada` e
   `ValorObservado`; a geração nunca vê a estrutura de cada site. Sem
   adaptador pronto, vale o valor gravado (cadastro à mão).
3. **Na pauta**, "Dados públicos": sugestões por embedding (título, palavra-
   chave e orientação da pauta × descrição da série aprovada), "Usar" e
   "tirar". Sem modelo de texto.
4. **Na geração**, `fatos_do_artigo` congela os valores no artigo e o bloco de
   fontes do prompt ganha a lista `[[DADO_N]] título — local, período: valor`.
5. **Na montagem**, `[[DADO_N]]` vira "(IBGE, 2019)" e o dado entra na lista
   de referências, com o link. Marcador de dado inexistente some.
6. **Na revisão**, dado citado cujo número não aparece igual no texto
   bloqueia a aprovação (aba Fontes mostra os dados e se foram citados).
7. **Acervo → catálogo** (`apps/dados/acervo.py`, uma vez por dia, todos os
   clientes somados): link de lugar de dados sem adaptador vira **pedido de
   adaptador** (com exemplos e o botão "Copiar para o desenvolvedor"); link
   que um adaptador reconhece (`codigo_do_link`) vira série sugerida, com o
   número de fontes que a citam.

Semente: `manage.py semear_dados` (o deploy roda) cria as instituições de
`apps/dados/catalogo_inicial.py` — só instituições, com domínios e notas de
onde estão os dados. Códigos de tabela não entram de memória: saem do
catálogo de cada instituição, conferidos.

## Pronto (estrutura)

- Modelos, migração, semente com 20 instituições (geral, saúde, IA, obras).
- Tela do catálogo (séries, instituições, pedidos), cadastro à mão, aprovar e
  recusar, confiável.
- Pauta: sugerir, usar, tirar. Geração: fatos no prompt. Montagem: citação e
  referência. Revisão: conferência do número.
- Varredura diária do acervo e pedidos de adaptador.

## A fazer (próxima etapa: explorar os sites)

Para cada item: explorar o site, escrever o adaptador com testes sobre
respostas gravadas (sem rede), marcar `pronto = True`.

1. **IBGE (SIDRA)** — `procurar` pelo catálogo de agregados
   (`servicodados.ibge.gov.br/api/v3/agregados`), `valores` pela
   `apisidra.ibge.gov.br`, `codigo_do_link` para `sidra.ibge.gov.br/tabela/N`.
   Recortes Brasil/UF/município. Comando para popular o catálogo com as
   tabelas dos nichos (PNS, PNAD TIC, PAIC, SINAPI...) como séries sugeridas.
2. **Banco Central (SGS)** — `valores` por código; uma lista curada de
   códigos (IPCA, Selic, INCC) na semente, conferida na API.
3. **OMS (GHO)** — API OData por indicador e país.
4. **DATASUS** — o mais trabalhoso: arquivos grandes; provavelmente um
   adaptador que pré-agrega (por ano e UF) e grava, em vez de consultar ao vivo.
5. **Cetic.br, CBIC/CUB, SINAPI (Caixa)** — planilhas: adaptador de planilha
   com mapeamento de colunas por publicação.
6. **Atualização** — tarefa que pede o período mais novo das séries em uso e,
   quando muda, sugere atualizar o artigo (reaproveitar o "fonte vencida").
7. **Instituição confiável** — usar `link_confiavel` na curadoria do acervo
   (documento dessas instituições dispensa a aprovação).

Ao explorar, cada pedido de adaptador da tela já traz domínio, exemplos de
links e quantas fontes citam: é o ponto de partida.
