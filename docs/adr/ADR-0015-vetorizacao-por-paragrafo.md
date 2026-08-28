# ADR-0015 — Vetorizar por paragrafo, com prefixo de contexto

- Situacao: aceita
- Data: 2026-08-28
- Decisores: Eduardo Reolon

## Contexto

A curadoria selecionava **um trecho por documento** — o "Super Chunk" — colado a
mao num campo de texto. A tela passou a mostrar os blocos que a extracao
reconhece, e com isso surgiu a pergunta: marcado um bloco, o que vai para o
indice — o bloco inteiro, ou pedacos dele?

Um bloco inteiro costuma passar de 480 tokens. Uma secao de Metodos passa
folgado.

## Decisao

**Cada paragrafo de um bloco marcado vira um vetor proprio.** O bloco e a
unidade que a pessoa marca; o paragrafo e a unidade que vai para o indice.

Cada paragrafo e vetorizado precedido de um prefixo de contexto — titulo do
documento e titulo do bloco, separados por travessao — que entra na contagem de
tokens.

Paragrafo que ainda assim exceda a janela e cortado em frases. Paragrafo curto
demais e juntado ao seguinte.

## Por que nao o bloco inteiro

Dois motivos, ambos mecanicos.

**Um embedding e um vetor unico de tamanho fixo.** Quanto mais longo e
tematicamente misto o texto, mais esse vetor vira uma media que nao fica perto
de nada em particular. Uma secao inteira, com objetivo, amostra, instrumento e
analise, produz um vetor equidistante de toda pergunta especifica.

**O modelo trunca em silencio.** O `multilingual-e5-large` tem janela de 512
tokens. Acima disso ele nao levanta erro: descarta o excedente. Vetorizar uma
secao de 1100 tokens seria vetorizar a primeira metade dela acreditando ter
vetorizado o todo — e sem nenhuma forma de saber qual metade entrou.

## Por que nao resumir com um modelo antes

Foi considerado: mandar o bloco longo para uma LLM resumir e vetorizar o resumo.
Recusado, e o motivo e especifico deste produto.

O trecho recuperado **nao e so chave de busca**. Ele e a evidencia que aparece
na tela de revisao, sob "ver o trecho usado", e e o texto que alimenta o modelo
que escreve o artigo. Se esse trecho for um resumo escrito por um modelo:

- o artigo passa a ser fundamentado numa parafrase, e nao na fonte;
- o revisor confere a afirmacao contra um texto que o documento nunca teve;
- a rastreabilidade da frase publicada ate o paper — que e a premissa do
  produto — deixa de existir.

Alem disso custaria uma inferencia por bloco de cada documento ingerido.

## Por que o prefixo de contexto

O preco conhecido de dividir por paragrafo e a perda de contexto. Recuperado
isolado, "esse efeito foi observado em 240 adultos" nao diz de que estudo nem de
que parte dele. O prefixo com titulo do documento e do bloco resolve isso por
alguns tokens e nenhuma inferencia.

O `content` guardado continua sendo o paragrafo puro: o prefixo serve a
vetorizacao, e mostra-lo colado no comeco de toda citacao seria ruido para quem
revisa.

## Consequencias

- **O limiar de distancia precisa ser remedido.** O 0.16 do
  [ADR-0014](ADR-0014-limiar-de-recuperacao.md) foi medido com um trecho longo
  por documento. Trechos menores e mais focados mudam a distribuicao das
  distancias. Rode `python manage.py calibrate_retrieval` com o acervo real
  antes de confiar no valor atual.
- Um documento passa a ter varios trechos no indice. A deduplicacao por
  documento em `recuperar()` deixa de ser refinamento e passa a ser essencial:
  sem ela, um unico artigo com muitos paragrafos ocuparia todas as vagas do
  top-k e o filtro de consenso trataria o mesmo estudo como confirmacao de si
  mesmo.
- `SuperChunk.kind` deixa de ser escolhido a mao. O titulo do bloco — em
  qualquer idioma, sem lista fixa — carrega o significado, no campo `heading`.
- Salvar a curadoria **substitui** o indice do documento. E seguro porque a
  citacao de um artigo publicado aponta para o trecho com `SET_NULL` e guarda
  titulo e URL copiados.
