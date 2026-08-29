# ADR-0018 — Embasamento estrito da ideia central, liberdade nos secundarios

**Status:** Aceito
**Data:** 2026-08-29

## Contexto

Ate aqui o prompt de redacao dizia, para todo o texto:

> Escreva SOMENTE o que as fontes sustentam. Nao complete lacunas com
> conhecimento proprio.

E uma regra defensavel e, na pratica, errada nos dois sentidos ao mesmo tempo.

**Ela produz texto ruim.** Exigir fonte para cada frase deixa o texto travado e
cheio de citacao — a cara de um trabalho academico. A publicacao aqui e outra
coisa: divulgacao bem apurada, para quem nao e da area. Frases como "a pressao
arterial varia ao longo do dia" ou "o exame e feito em jejum" sao conhecimento
corrente da area, e citar um artigo para cada uma delas nao acrescenta rigor,
so ruido.

**E ela nao e obedecida.** Um modelo pequeno nao consegue escrever uma secao
inteira sem preencher nenhuma lacuna. Ao pedir o impossivel, o prompt entrega o
resultado ao acaso: ora o modelo se cala sobre o que precisaria explicar, ora
completa sem avisar — e, no pior caso, completa **a afirmacao central**, que e
justamente a que nao pode ser inventada.

A regra global esconde a unica distincao que importa.

## Decisao

### 1. A divisao e por FUNCAO da frase, nao por parte do texto

**A ideia central** — a afirmacao que a publicacao existe para fazer — precisa
sair de um artigo de referencia e levar o marcador da fonte que a sustenta.
Sem excecao.

**Os paragrafos secundarios** — contexto, definicao de um termo, por que aquilo
importa, consequencia pratica — podem se apoiar em conhecimento geral da area,
sem fonte e sem marcador.

Em ambos os casos, e sempre: **numero, percentual, resultado de estudo, dose e
data so aparecem se estiverem na fonte**, e entao com marcador. E a fronteira
que separa "explicar o assunto" de "inventar dado", e ela nao se move.

### 2. O plano nomeia a ideia central e quem a sustenta

`article_outline` passa a devolver `ideia_central`,
`fontes_da_ideia_central` e, em cada secao, `sustenta_ideia_central`. O plano
tambem declara `temas`: **um por publicacao, dois no limite**, e so quando sao
faces do mesmo assunto. Texto que abraca cinco temas nao responde bem a nenhum
deles e, para busca organica, compete consigo mesmo.

Sem isso a regra nao teria como ser verificada: "a ideia central esta embasada"
so e uma pergunta respondivel se alguem disser qual e a ideia central.

### 3. Sem embasamento, a geracao PARA

`SemEmbasamentoCentral` e levantado quando o plano nao encontra fonte para a
ideia central, ou quando nenhuma secao assume afirma-la.

Nao ha degradacao graciosa aqui de proposito. As alternativas seriam escrever o
texto sem a tese (uma publicacao que nao diz nada) ou escrever a tese sem fonte
— que produz o pior resultado possivel: um texto que **parece** fundamentado,
com links e assinatura, e cuja afirmacao principal ninguem verificou. E
exatamente o que o produto existe para nao fazer.

O caminho de saida e humano: alguem acrescenta ao acervo o artigo de referencia
que falta, ou ajusta a pauta para o que o acervo ja sustenta, e manda gerar de
novo. A mensagem de erro diz isso.

Uma segunda trava, no momento da redacao: se a secao marcada como portadora da
ideia central sair sem nenhum marcador, o passo falha. Esse caso e diferente —
a fonte existe, o modelo e que nao a usou — e por isso e erro de geracao comum,
que o orquestrador trata como qualquer outro.

### 4. Poucas referencias, e a escolha de onde os links aparecem

O limite de **2 fontes distintas por publicacao** ja existia e passa a ter nome
(`MAXIMO_DE_FONTES_NO_ARTIGO`) e razao escrita: uma pagina cheia de links de
saida descaracteriza a curadoria que o formato imita, e dilui o valor de cada
link.

`Article.link_placement` decide onde eles aparecem: no meio do texto, ou numa
lista ao final. Os dois formatos sao legitimos — link no meio do paragrafo tira
o leitor da pagina no meio do raciocinio; uma lista ao fim mantem a leitura
inteira — e quem decide e quem conhece o publico do site.

No modo "ao final" o marcador vira o **nome da fonte em texto simples** e o link
vai para a lista. Apagar o marcador deixaria buracos do tipo "conforme , o
efeito": a atribuicao continua no lugar, so o destino muda de lugar.

## Consequencias

* Uma publicacao pode falhar por falta de embasamento mesmo com o acervo tendo
  material sobre o tema. Isso e o comportamento desejado, e a mensagem precisa
  ser boa o bastante para a pessoa saber o que fazer.
* O texto fica melhor de ler, e o custo e uma superficie nova: paragrafos sem
  fonte. A protecao contra invencao passa a ser a proibicao de dado especifico,
  nao a exigencia de citacao — e a revisao humana continua obrigatoria.
* O plano ficou mais dificil para o modelo produzir. Vale: um plano sem ideia
  central declarada e um plano que nao sabe o que o texto vai afirmar.

## Alternativas recusadas

**Manter a exigencia global de fonte.** Produz texto pior e, pior ainda, nao e
cumprida — o que da a falsa sensacao de que a regra existe.

**Marcar cada frase como "com fonte" ou "sem fonte".** Foi considerado e
descartado: exige do modelo uma anotacao por sentenca, dobra o tamanho da saida,
e o erro de anotacao passaria a ser um novo modo de falha silenciosa.

**Deixar o revisor decidir caso a caso, sem trava.** A revisao humana e
obrigatoria e continua sendo a ultima palavra, mas ela nao substitui a trava:
um texto fluente com tese sem fonte e exatamente o que passa por revisao sem
levantar suspeita.
