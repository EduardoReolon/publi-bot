# ADR-0022 — Perfil do negocio, e o PubliBot medido como anuncio

**Status:** Aceito
**Data:** 2026-09-27

## Contexto

Duas perguntas de quem opera o sistema mostraram lacunas:

1. **"Aderencia a que?"** O que descrevia o negocio estava espalhado: o "nicho"
   no cadastro do site (usado, por engano, tambem como publico do artigo), a
   oferta no guia editorial, as dores no radar. As telas falavam em
   "aderencia" sem dizer a referencia.
2. **"Substitua o Google Ads pelo PubliBot."** Para vender isso, o PubliBot
   precisa falar a lingua do anuncio: cliques, custo por clique, conversoes e
   custo por conversao — no mesmo placar dos anuncios do cliente.

E uma terceira, sobre escopo: o PubliBot deveria gerir o site inteiro
(palavras-chave nas tags, sitemap)?

## Decisao

### 1. Um perfil do negocio, com uso declarado de cada campo

`PerfilDoNegocio` (tela **Negocio**): tema do site, publico, oferta, valores
(valor de uma conversao, investimento no PubliBot e em anuncios, cotacao do
dolar). As dores continuam guardadas no radar (sao buscadas como consultas),
mas editadas ali. O "nicho" do site e a oferta do guia migram para ca.

| Campo | Uso |
|---|---|
| tema + oferta + sementes | "perto do tema do site" (antiga aderencia) |
| oferta | a chamada no artigo ("perto da oferta") |
| publico | para quem o artigo e escrito |
| dores | oportunidades ("perto das dores do publico") |
| valores | comparacao com anuncios |

As parcelas da nota passam a ser mostradas com a referencia no nome.

### 2. O placar dos anuncios

- **Custo por clique guardado por palavra** (`CustoDaPalavra`), de toda
  chamada de volume — ja paga pelo radar.
- **Valor do trafego**: cliques do Search Console por consulta x custo por
  clique. As consultas com clique sem preco sao consultadas numa chamada so
  (ate 300 palavras), com validade de 90 dias.
- **Canal de entrada nas conversoes** (`first_channel`, `last_channel`),
  classificado no navegador pelo agrupamento de canais do Google Analytics
  (`gclid`/UTM pago = anuncio). Conversao de quem leu artigo e nao entrou por
  anuncio = PubliBot; entrou por anuncio = anuncio (com artigo, aparece a
  parte).
- **Custo por conversao dos dois lados**, retorno em reais, e "os cliques do
  PubliBot comprados em anuncio, por conversao".
- **Parcela comercial no radar**: com custo por clique conhecido, tema caro no
  anuncio sobe (10%). Sem o dado, a nota nao muda.

### 3. Escopo: o PubliBot decide o que depende de conhecer o conteudo

- **Links internos** (`related_articles`) saem do PubliBot: dependem de saber o
  assunto de todos os artigos e quais convertem.
- **Sitemap, canonical, dados estruturados e tags ficam com o site**, com uma
  lista de conferencia no contrato. O sitemap e um indice — o Google ignora
  `priority` e `changefreq` — e precisa das paginas que o PubliBot nao
  publicou. `<meta name="keywords">` e ignorada pelo Google desde 2009.
- `canonical_source` e documentado como o que e (a fonte principal) e com o
  aviso de nunca virar `rel=canonical`.

## Alternativas descartadas

- **PubliBot como CMS do site.** Duplicaria o que qualquer plataforma ja faz
  e prenderia o cliente; o valor do PubliBot esta no que depende do conteudo
  e dos dados de demanda e conversao.
- **Custo por clique pela API do Google Ads.** Exige conta de anunciante e
  aprovacao de token de desenvolvedor por cliente; a DataForSEO ja entrega o
  mesmo numero do Planejador de palavras-chave.
- **Gasto de anuncio pela API do Google Ads.** Mesmo motivo; o valor mensal
  digitado basta para o custo por conversao.

## Consequencias

- Valores em reais dependem de uma cotacao digitada: o custo por clique vem em
  dolar.
- O valor do trafego usa o retrato do Search Console (28 dias), e as
  conversoes o periodo escolhido na tela; a tela diz de qual janela vem cada
  numero.
- Os links internos valem na publicacao; artigos publicados depois entram
  quando o artigo for atualizado.
