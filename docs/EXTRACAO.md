# Extracao de PDF: como funciona e como ajustar

Este documento e para quem vai **mexer** nas heuristicas. Se voce so quer saber
por que um PDF saiu torto, comece pela ultima secao.

## A pergunta que vem antes

> "Da para fazer isso aprender sozinho?"

Nao aqui, e vale entender por que — a resposta define o que este documento e.

**Nao ha o que aprender com um botao "esse nao captou bem".** Isso e um sinal
negativo sem gabarito: diz que errou, nao diz qual era o certo. Aprendizado
supervisionado precisa do par (entrada, resposta certa), e a resposta certa e
justamente o que ninguem digitou.

**Onde ha gabarito, ele ja existe e nao precisa de botao.** A curadoria e
obrigatoria: quando a pessoa corrige o titulo, aquilo E o rotulo. O sistema
grava a sugestao original em `Document.metadata_suggested` e compara. Ver
`--acervo` abaixo.

**Mesmo com rotulos, treinar aqui seria refazer mal o que ja existe.** Extrair
titulo, autores e secoes de artigo cientifico e um problema resolvido por
ferramentas dedicadas — o Docling (que este projeto ja preve, ADR-0007) e o
GROBID. Elas usam o que estas heuristicas **nao tem**: tamanho de fonte, peso,
posicao na pagina, coluna. O extrator local joga tudo isso fora e recebe texto
corrido; e como reconhecer titulo pela forma da frase, no escuro.

Entao a divisao e esta:

| Caminho | Como acerta |
|---|---|
| **Docling** (com GPU) | le o layout. E a solucao de verdade. |
| **Extrator local** (sem GPU) | heuristica calibrada a mao, o que este doc descreve. |

As heuristicas sao uma **ponte** ate o worker existir. Investir num modelo
proprio para substituir uma ponte seria caro e pior que a alternativa pronta.
O que **vale** investir e em nao quebrar a ponte sem perceber — e disso trata o
resto do documento.

## As duas gramaticas

Sem layout, sobra o que esta no proprio texto. Dois estilos cobrem quase toda
revista cientifica:

| Estilo | Exemplo | Onde apareceu |
|---|---|---|
| Secao numerada | `1 Introduction`, `2.2.1 Roadway ...` | Climatic Change (Springer) |
| Secao em versal | `INTRODUCTION`, `DATABASE COMPONENT` | JAWRA |

Mais dois rotulos que nao seguem nenhum dos dois: a **abertura** (`Abstract`,
`Resumo`, `Keywords`), que vem colada ao texto na mesma linha, e as **secoes
finais** (`References`, `Acknowledgments`), que aparecem sozinhas.

Quando nada e reconhecido, o documento vira **um bloco so**. Isso e proposital:
inventar divisao onde nao ha estrutura daria a quem cura a impressao de que o
PDF foi entendido.

## As regras, e o que cada uma defende

Toda regra abaixo existe porque um PDF real quebrou sem ela. Nenhuma e
preventiva.

### Secao numerada (`PADRAO_DE_SECAO`)

| Regra | Constante | Impostor que ela barra |
|---|---|---|
| titulo curto | `MAXIMO_DE_TITULO_DE_SECAO` | paragrafo que comeca com numero |
| nao repete no documento | `MAXIMO_DE_REPETICOES` | `400 Climatic Change (2017) 145:397-412` |
| poucas virgulas | `MAXIMO_DE_VIRGULAS` | `1 School of Sustainability, Arizona State University, Tempe, AZ, USA` |
| sem fim de frase no meio | — (`". " in resto`) | `1 C2,C 3 and C4. Outer-dependence in the` |
| seguida de prosa | `MINIMO_DE_PALAVRAS_NA_PROSA` | `5 Discouraging` (celula de tabela) |
| numeracao progride | `_sequencia_faz_sentido` | `4 Flood storage Vegetated bioretention` |

A contagem de repeticao **ignora digitos** (`PADRAO_DE_DIGITOS`), senao cada
cabecalho de pagina seria unico por causa do numero da pagina.

### Secao em versal (`_e_versal`)

| Regra | Constante | Impostor que ela barra |
|---|---|---|
| so maiusculas | — | qualquer prosa |
| letras o bastante | `MINIMO_DE_FRACAO_DE_LETRAS` | `W11 W12 /C1/C1/C1 W1N` (formula) |
| tem palavra longa | `PADRAO_DE_PALAVRA_LONGA` | `C2`, `CN` |
| nao e trecho de linha frequente | `_e_pedaco_de_cabecalho` | `JOURNAL OF THE AMERICAN WATER RESOURCES ASSOCIATION` |
| vem depois da abertura | `PRIMEIRA_LINHA_PARA_VERSAL` | o proprio titulo do artigo |
| seguida de prosa **de verdade** | `MINIMO_DE_PALAVRAS_NA_PROSA` | `RWIS` (celula de tabela) |

O ultimo caso tem uma sutileza: para titulo numerado, "e seguido de outro
titulo" conta como prosa (`2.2` vem colado em `2.2.1`). Para versal, nao — numa
tabela, `RWIS` e seguido de `2 RWIS Discouraging`, que tem a forma de secao
numerada. Por isso `_vem_prosa_depois(..., aceitar_secao=False)`.

### Identificacao da obra (`flows.py`)

Ordem das fontes: **o que o arquivo declara** primeiro, heuristica depois.

| Regra | Constante | Caso real |
|---|---|---|
| `/Title` tem que ser feito de palavras | `MINIMO_DE_PALAVRAS_NO_TITULO` | `jawr_027 346..358` (codigo da grafica) |
| titulo junta linhas ate os autores | `_parece_linha_de_autores` | titulo quebra em 2-3 linhas |
| linha em versal nunca e de autores | `_e_caixa_alta` | `...URBAN WATERSHEDS1` tem marcador de nota |
| virgula so conta com nome abreviado | `PADRAO_DE_INICIAL` | titulo com virgula nao e lista de autores |
| lista de autores continua nas linhas seguintes | — | `... Eisenberg 2 &` quebra em 3 linhas |
| DOI aceita barra de fracao | `BARRAS_EQUIVALENTES` | `10.1111⁄ j.1752-1688...` |

## O ciclo de ajuste

O risco de calibrar a mao e conhecido: **consertar um artigo quebra outro**. O
comando existe para tornar isso visivel em um segundo.

```bash
# 1. Junte os PDFs num diretorio (fora do git: sao obras de terceiros)
mkdir -p casos/

# 2. Fotografe o comportamento atual, DEPOIS de conferir a olho que esta certo
python manage.py conferir_extracao --pasta casos/ --gravar

# 3. Mexa numa regra. Rode de novo.
python manage.py conferir_extracao --pasta casos/
```

A saida diz `IGUAL`, `MUDOU` (com o diff campo a campo) ou `NOVO`. `MUDOU` **nao
e erro por si so** — pode ser exatamente a melhora que voce acabou de fazer. Voce
olha, decide, e regrava com `--gravar`.

Os esperados vao para `fixtures/extracao/<nome>.json` e **entram no git**; os
PDFs, nao.

### Os casos que o acervo coleta sozinho

```bash
python manage.py tenant_command conferir_extracao --schema=acme --acervo
```

Lista todo documento em que a curadoria corrigiu a extracao, com o que ela
propos e o que era certo, mais a taxa de acerto por campo. Nao precisa de PDF
nem de ninguem reportar nada: a conferencia humana ja e o gabarito.

Trate essa lista como fila de trabalho. Para cada caso, copie o PDF para
`casos/`, reproduza o erro com `--pasta`, ajuste, e confira que os outros
continuam `IGUAL`.

### Quando NAO ajustar

- **Um caso so.** Uma regra nova a partir de um unico PDF costuma quebrar dois.
  Espere o padrao aparecer duas vezes.
- **O texto esta corrompido, nao a regra.** Se o pypdf devolveu
  `p o s e db yA h e r n(2011)`, nenhuma heuristica conserta. E caso para o
  Docling.
- **A regra ficaria especifica de uma revista.** `SECOES_FINAIS` e uma lista de
  palavras porque sao rotulos universais; "se comeca com JAWRA" nao seria.

## Por que meu PDF saiu torto

Confira nesta ordem:

1. **O aviso vermelho aparece na curadoria?** Entao rodou o extrator local. A
   solucao de verdade e subir o worker Docling (`worker-gpu/`) e cadastrar a
   conexao em Inferencia — nao mexer nas heuristicas.
2. **O texto do bloco esta legivel?** Se palavras vem emendadas ou aspas viraram
   simbolos (`Bsafe-to-fail^`), o problema e a extracao, nao a divisao.
3. **Saiu um bloco so?** Nenhuma das duas gramaticas foi reconhecida. Pode ser
   um artigo sem secoes marcadas — o que e verdade sobre ele.
4. **Saiu bloco a mais, no lugar errado?** Ai sim e caso de calibrar: rode
   `--pasta` com esse PDF e olhe qual regra deixou passar.
