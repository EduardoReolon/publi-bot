# ADR-0019 — Tres opcoes de capa, escolhidas por uma pessoa

**Status:** Aceito
**Data:** 2026-08-29

## Contexto

A imagem de capa existia no contrato (`cover_image`) e no prompt
(`image_prompt`), e em lugar nenhum no produto: nao havia provedor de imagem,
nao havia onde guardar o arquivo, e nao havia como escolher.

Ao construir isso, a pergunta que decide o desenho e **quem escolhe a imagem**.

Gerar uma e usa-la e o caminho obvio e o pior. Modelos de imagem erram muito, e
erram de formas que so um humano percebe — uma cena que sugere procedimento
clinico, uma composicao que nao combina com o texto, texto deformado embutido
no desenho. Com uma opcao unica, a revisao vira "aceita ou pede de novo": mais
lenta, e o que sai e a primeira que nao incomodou, nao a melhor.

## Decisao

### 1. Lotes de tres, e o lote anterior nao e descartado

Tres e o menor numero em que comparar significa alguma coisa. Com duas a
escolha vira "esta ou aquela"; acima de tres o custo cresce e a atencao de quem
revisa nao acompanha.

Pedir mais exemplos **acrescenta** um lote. Nada e apagado: a terceira do
primeiro lote pode ser melhor que tudo o que veio depois, e limpar a tela
jogaria fora uma imagem ja paga. A tela agrupa por lote porque comparar dentro
do lote e comparar entre lotes sao leituras diferentes.

Ha um teto de cinco lotes por artigo. Sem limite, "gerar mais" vira
caca-niquel e o proximo lote nunca e o ultimo.

### 2. Nenhuma capa e escolhida automaticamente

`ArticleImage.is_chosen` comeca falso em todas, e uma restricao do banco aceita
no maximo uma escolhida por artigo. Sem essa restricao, duas marcadas fariam a
publicacao escolher pela ordem da consulta — a escolha da pessoa ignorada em
silencio.

Um artigo aprovado sem capa escolhida sai **sem** `cover_image`, e nao com uma
imagem qualquer. Publicar com uma capa que ninguem olhou e como publicar um
texto que ninguem leu.

### 3. Duas etapas: descrever, depois gerar

Um modelo de texto escreve a descricao (`image_prompt`), e so entao o modelo de
imagem gera. Modelos de imagem leem mal um artigo inteiro, e mandar o titulo
cru produz sempre a mesma foto generica de banco de imagens.

A temperatura desse prompt e alta (0.8) de proposito: o mesmo artigo sera usado
varias vezes para pedir mais exemplos, e uma descricao identica a cada rodada
devolveria variacoes da mesma imagem.

### 4. `ImageClient`, separado de `LLMClient`

A assinatura nao cabe na mesma interface: uma chamada de imagem devolve VARIAS
opcoes, nao um texto, e nao tem contagem de tokens. Forcar as duas na mesma
classe produziria parametros ignorados dos dois lados.

O adaptador fala `POST /v1/images/generations`, o mesmo formato de OpenAI e dos
servidores locais que o imitam — a mesma aposta que o adaptador de texto faz, e
pelo mesmo motivo.

Duas particularidades do formato viraram codigo:

* **`n` nem sempre e respeitado** (o dall-e-3 e o caso conhecido). Como o ponto
  e ter opcoes para comparar, o cliente completa o que faltou com chamadas
  adicionais em vez de devolver menos do que foi pedido. O laco fecha em
  `quantidade` chamadas no pior caso.
* **`b64_json`, nunca `url`.** A URL do provedor expira em cerca de uma hora;
  guardada, levaria a uma imagem quebrada no artigo dias depois, na publicacao.

### 5. Uma rota publica, so para a capa escolhida

O contrato manda a imagem por referencia, entao o site de destino precisa
busca-la — sem sessao, do outro lado da internet. `/capas/<uuid>.webp` serve
**apenas** a opcao escolhida de um artigo ja aprovado.

O resto da midia continua fora de alcance: o Nginx serve `/protected-media/`
como `internal`, e os PDFs do acervo de todos os tenants nunca ficam expostos
por URL adivinhavel.

A URL absoluta e montada a partir do dominio primario do tenant, e nao de uma
configuracao separada. Um segundo lugar guardando o mesmo endereco divergiria,
e o sintoma seria uma imagem quebrada no site do cliente — descoberta dias
depois, por outra pessoa.

### 6. Q&A nao tem imagem

Uma resposta e um texto curto que vive numa listagem de perguntas. Ilustrar
cada uma custaria uma inferencia por pergunta para algo que ninguem pediu.

## Consequencias

* Gerar capas exige uma conexao de inferencia do tipo `image` cadastrada. Sem
  ela a tela explica o que falta, em vez de falhar.
* O arquivo guardado e WebP, como toda imagem do sistema (ADR-0017). O que o
  provedor devolve (PNG, quase sempre) e convertido na entrada.
* Uma opcao ilegivel nao derruba o lote: duas boas valem mais que um lote
  inteiro perdido.
* Cada lote custa inferencia. O teto de lotes e o que impede esse custo de ser
  ilimitado por artigo.

## Alternativas recusadas

**Escolher a primeira automaticamente e deixar trocar.** Na pratica ninguem
troca, e o efeito e publicar sempre a primeira — que e o desenho de uma opcao
so, com passos a mais.

**Guardar a URL do provedor em vez do arquivo.** Expira em cerca de uma hora, e
a publicacao acontece dias depois. Seria uma imagem quebrada garantida.

**Servir a capa pelo mesmo `internal` dos PDFs.** O site de destino nao tem
sessao nem credencial no PubliBot; ele precisa de um GET simples. A protecao
correta aqui e o escopo (so a escolhida, so de artigo aprovado, id nao
enumeravel), nao a autenticacao.
