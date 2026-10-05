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

## Catalogo global, escolha por site

O catalogo e o mesmo para todos os clientes. Cada site escolhe, em **Negocio >
Dados publicos que interessam a este site**, de quais nichos (saude, IA, obras,
geral) as pautas recebem sugestao; nenhum marcado, todos. Mesmo sem marcar, uma
serie so entra numa pauta se for muito proxima do tema dela.

## Regras que valem para todo adaptador

- `valores(serie, local=...)` recebe "Brasil" ou o nome do estado
  (`apps/dados/locais.py`) e traduz para o código da instituição. Sem o
  recorte, devolve `[]` e o artigo fica só com o Brasil.
- Contexto do modelo: no máximo 8 fatos por artigo e 4 por seção (os mais
  próximos dela), sempre marcados como opcionais.

## Pronto

- Modelos, migração, semente com 20 instituições (geral, saúde, IA, obras).
- Tela do catálogo (séries, instituições, pedidos), cadastro à mão, aprovar e
  recusar, confiável.
- Pauta: entrada automática (muito próximos, até 3), sugerir, usar, tirar (não volta).
  Recorte: estado do site (regiões do Radar) e Brasil. Geração: cada seção recebe
  até 4 fatos, os mais próximos dela, como OPÇÃO. Montagem: citação e
  referência. Revisão: conferência do número.
- Varredura diária do acervo e pedidos de adaptador.

## Adaptadores prontos

| Instituição | Busca (`procurar`) | Valores (`valores`) | Recortes | Link reconhecido |
|---|---|---|---|---|
| IBGE (SIDRA) | catálogo de agregados (`servicodados.ibge.gov.br/api/v3/agregados`), pelo nome da pesquisa e da tabela; cada variável vira uma série `tabela/variável` | `…/agregados/{tabela}/periodos/-N/variaveis/{v}?localidades=N1[all]` ou `N3[UF]`; pega o **total** de cada classificação e pula "-", "...", "X" | Brasil, estado | `sidra.ibge.gov.br/tabela/N`, `apisidra…/values/t/N` |
| Banco Central (SGS) | portal de dados abertos (CKAN, `package_search`): cada série traz o link `bcdata.sgs.{código}` | `api.bcb.gov.br/dados/serie/bcdata.sgs.{código}/dados/ultimos/N` | Brasil | `bcdata.sgs.N` |
| OMS (GHO) | `ghoapi.azureedge.net/api/Indicator` (nomes em **inglês**) | `…/api/{código}?$filter=SpatialDim eq 'BRA'`, ambos os sexos | Brasil | `ghoapi…/api/{código}` |

- O período é guardado numa forma que ordena ("2019", "2024-08", "2024-T1",
  "2024-08-15") e aparece legível no texto ("ago/2024", "1º tri/2024").
- Valor buscado há menos de um dia não é buscado de novo; série cadastrada à
  mão nunca chama o adaptador (o código dela é inventado).
- Testes: `tests/test_dados_adaptadores.py`, sobre respostas no formato
  documentado de cada API, sem rede.

### Series sugeridas: o que sao e o que fazer

Uma serie **Sugerida** e um indicador que o PubliBot achou no catalogo de uma
instituicao (pela busca dos nichos, pela sua busca ou porque uma fonte do
acervo cita o link). Ela ainda **nao entra em pauta nenhuma**: so as
**Aprovadas** sao oferecidas.

**Onde aprovar:** menu **Dados**, aba **Series**, filtro "Sugerida". So o
**superusuario** ve os botoes (o catalogo vale para todos os clientes); outra
conta ve o aviso de quem aprova. Criar um superusuario no servidor:
`python manage.py createsuperuser`.

- Em cada linha, na coluna **Situacao**: **Aprovar** ou **Recusar**.
- Varias de uma vez: marque as caixas da esquerda e use **Aprovar as
  marcadas** (fim da lista).
- Tudo de uma vez: **Aprovar todas as sugeridas deste filtro** (todas as
  paginas, respeitando nicho e busca). A lista mostra 50 por pagina.

**Na duvida, aprove.** Uma serie aprovada so e oferecida a uma pauta quando
e muito proxima do tema dela (vetor do titulo e da descricao), e so nos sites
que escolheram aquele nicho em Negocio. Uma serie fora do assunto (ex.: "Base
monetaria ampliada" para uma clinica) simplesmente nunca chega perto de uma
pauta. Recuse so o que claramente nao serve a cliente nenhum, para a lista
ficar limpa.

"Ultimo valor" vazio ("buscado ao usar") e normal: o valor e buscado na
instituicao quando uma pauta usa a serie, e guardado.

### Sem ninguem rodar nada

Uma vez por dia (tarefa `varrer-dados-do-acervo`), cada instituicao com
adaptador pronto que ainda nao tem nenhuma serie do proprio catalogo recebe as
**series iniciais dos nichos** (`TERMOS_POR_NICHO`, ate 5 por termo), como
"Sugerida". A tela Dados avisa quantas esperam aprovacao; marque as que fazem
sentido e "Aprovar as marcadas". Depois disso, so entra serie nova pelo acervo
(fontes que citam) ou por busca sua.

### Como popular e conferir

```bash
# As APIs de verdade respondem? (precisa de internet; nao grava nada)
manage.py conferir_adaptadores

# Procurar series e deixar como "Sugerida" para aprovar na tela Dados
manage.py explorar_dados ibge "plano de saude"
manage.py explorar_dados bcb IPCA
manage.py explorar_dados oms "obesity"
manage.py explorar_dados ibge --nichos      # os termos de TERMOS_POR_NICHO
```

Na tela **Dados → Instituições**, o superusuário tem o mesmo "Procurar séries"
por instituição. Série sugerida só vai para as pautas depois de **aprovada**.

### Atualização

Uma vez por dia (com as sugestões de atualização do Radar), os dados citados
nos artigos no ar são conferidos na instituição. Período mais novo vira a
sugestão **"Dado público com período mais novo"** (Radar → Atualizações), com
"de → para". *Atualizar no PubliBot* cria a versão nova já com o valor novo, e
a conferência do número na revisão passa a exigir o número novo no texto.

### Instituição confiável

Fonte achada na busca de fontes cujo link é de instituição marcada
**confiável** no catálogo entra aprovada, na categoria "Norma ou documento
oficial", sem esperar a curadoria.

## A fazer

1. **DATASUS** — arquivos grandes. O adaptador baixa com `arquivo_temporario`
   (apaga ao terminar, com teto de tamanho), resume por ano e estado e grava
   só o resumo em `Valor`: a base inteira nunca fica no servidor. Depende de
   escolher as bases (SIM, SINASC, SIH...) e o recorte que interessa.
2. **Cetic.br, CBIC/CUB, SINAPI (Caixa)** — planilhas, sem API: adaptador de
   planilha com mapeamento de colunas por publicação. Até lá, cadastro à mão.
3. **IPEA (ipeadata)** — tem API OData; mesma forma do adaptador da OMS.

Ao explorar, cada pedido de adaptador da tela já traz domínio, exemplos de
links e quantas fontes citam: é o ponto de partida.
