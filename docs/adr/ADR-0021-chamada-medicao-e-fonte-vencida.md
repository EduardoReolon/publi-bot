# ADR-0021 — Chamada para a oferta, leitura e conversao, e fonte que vence

**Status:** Aceito
**Data:** 2026-09-27

## Contexto

Um site de servico por assinatura (engenheiro que confere notas de material de
construcao pelo WhatsApp) quer que o conteudo sobre CUSTO seja o carro-chefe.
Tres lacunas apareceram:

1. **Dado que envelhece.** Pagina de preco vale pelo dado do mes. O PubliBot ja
   tirava da busca a fonte vencida (validade da categoria), mas nao avisava que
   o artigo publicado com ela precisava de versao nova.
2. **Chamada para a oferta.** O convite ficava so no fecho, escrito no texto.
   Chamada no contexto converte mais; chamada em todo artigo vira anuncio; e o
   bloco (botao, WhatsApp, rastreio) precisa ser do site, para trocar a oferta
   num lugar so.
3. **Sem retorno de negocio.** O Search Console diz quem clicou no Google, nao
   quem leu nem quem virou cliente. O radar escolhia temas pela busca, sem
   saber quais temas trazem cliente.

## Decisao

### 1. Fonte vencida ou substituida vira sugestao de atualizacao

- O documento ganha `replaces` (a versao anterior da mesma fonte). Ao concluir
  a curadoria da nova, a anterior vence na hora.
- Artigo no ar que cita fonte vencida entra em Radar › Atualizar artigos
  (tipo `fonte`), na curadoria e numa conferencia diaria.
- "Atualizar no PubliBot" cria a versao e troca cada citacao da fonte antiga
  pelo trecho mais parecido da nova (menor distancia de cosseno). O link e o
  nome no texto vem da citacao e mudam junto; os numeros, a revisao confere.
- A mesma combinacao de fontes, decidida, nao volta; outra fonte vencendo volta.

### 2. Chamada: o artigo diz se cabe e onde; o bloco e do site

- Tres modos: `none`, `end`, `inline`. Decididos no planejamento, **sem LLM**:
  proximidade (embedding) entre a **oferta** do Guia editorial e o tema, e
  entre a oferta e cada secao (a primeira fica de fora). Secao com aderencia
  >= 0,5 recebe a chamada no meio; tema com >= 0,2, so no fim; abaixo, nenhuma.
- A pauta pode forcar o modo; a revisao mostra o motivo e muda.
- No texto, a marca `[[CHAMADA]]`, que a pessoa pode mover ou apagar — o texto
  manda, e o modo e a secao se ajustam a ele. No HTML vira
  `<aside data-publibot="chamada"></aside>`, inserido DEPOIS da sanitizacao
  (`aside` nao esta na lista de permissao, entao o modelo nao consegue emitir
  um).
- Contrato: campo `call_to_action` e recurso `call_to_action`. Site que nao
  conhece a marca mostra um elemento vazio.
- Tema longe da oferta (`none`): o fecho tambem nao recebe o convite.

### 3. Leitura e conversao, medidas pelo site, sem dado de pessoa

- Recurso `insights`: `GET /api/v1/insights/?since=`, uma linha por
  publicacao e dia (aberturas, leituras com >= 10 s de tempo ATIVO, segundos
  ativos, chegaram ao fim, viram e clicaram na chamada) e as conversoes.
- **A jornada fica no navegador de quem le** (armazenamento local, 30 dias) e
  so viaja na conversao, sem identificador. O site nao precisa guardar id de
  visitante; o PubliBot nunca recebe um.
- Tempo ativo = aba visivel e interacao nos ultimos 30 s (o criterio do
  "tempo de engajamento" do GA4), com o corte de 10 s da "sessao engajada".
- Atribuicao no PubliBot, as tres ao mesmo tempo: ultimo artigo, participou,
  e linear (partes iguais). Leitura com menos de 10 s nao entra na jornada.
- O radar ganha a parcela **conversao** so quando ha dado: tema vizinho
  (distancia <= 0,20) de artigo que converte acima da media sobe, com peso de
  15%. Sem dado, a nota e identica a de antes.
- O no de referencia traz o script (`leitura.js`), as rotas que recebem do
  navegador e as tags de template do bloco.

## Alternativas descartadas

- **Google Analytics como fonte.** Exigiria conta e credencial por cliente,
  amostragem e bloqueadores de anuncio derrubam o script, e a jornada por
  artigo nao sai pronta. O proprio site mede, do proprio dominio.
- **Identificador de visitante no servidor.** Resolveria a jornada, com custo
  de privacidade (e de consentimento) que a jornada no navegador evita.
- **LLM escolhendo onde vai a chamada.** O embedding responde a mesma pergunta
  (qual secao esta mais perto da oferta) de forma deterministica, explicavel
  e sem placa de video.
- **Chamada escrita no texto pelo modelo.** Trocar a oferta exigiria reescrever
  todos os artigos.

## Consequencias

- O site precisa implementar dois recursos para tirar proveito: o bloco no
  template e a medicao. Sem eles, nada quebra.
- Os numeros de leitura vem de rota publica do site: indicativos, nao
  auditaveis.
- Os limiares (0,5 / 0,2 / 0,20 / 15%) sao pontos de partida; a revisao
  mostra a aderencia medida para calibrar com uso real.
