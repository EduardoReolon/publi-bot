# ADR-0020 — Radar de pautas, e a web como fornecedora de fontes

**Status:** Aceito
**Data:** 2026-09-26

## Contexto

O PubliBot nasceu forte da pauta para a frente — fundamentacao, citacao,
revisao por partes, contrato com o site — e fraco da pauta para tras: a pauta
nascia de uma pessoa digitando. E nasceu para artigo cientifico, com toda
fonte tendo URL, autores e ano. Um produto generico (uma clinica, uma empresa
de planilhas de orcamento de obra) precisa de outras fontes e de um jeito de
descobrir sobre o que vale escrever.

A tentacao obvia — deixar um agente "pesquisar na web" e escrever com o que
achou — e exatamente o que o produto existe para evitar: texto que parece
fundamentado e nao e.

## Decisao

### 1. A web tem dois usos, com confiancas diferentes

| Uso | Exemplo | Entra no texto? |
|---|---|---|
| Sinal de DEMANDA | "as pessoas perguntam X", "X tem 2 mil buscas/mes" | Nao — decide o tema |
| Candidato a FONTE | uma pagina, um video | So depois de curadoria |

A pergunta que as pessoas fazem nao pode estar "errada": ela e o dado. O fato
que o texto afirma continua saindo do acervo curado.

### 2. Radar de pautas (`apps/radar`)

Sementes -> perguntas relacionadas e buscas relacionadas (SERP) -> perguntas
dos visitantes -> comentarios do YouTube que perguntam -> "quase la" do
Search Console -> volume de busca numa chamada so -> agrupamento incremental
por embedding -> nota com parcelas gravadas -> pautas SUGERIDAS com a
evidencia junto. Nenhum LLM le dado bruto: o agrupamento e por embedding, e o
tamanho do grupo ja e sinal de demanda.

### 3. Contas pagas por tenant, com livro-caixa e teto

Cada site usa a propria conta da DataForSEO e a propria chave do YouTube. Nao
ha ganho de escala em juntar (a DataForSEO cobra por chamada), e separar tira
o operador do meio da cobranca. Toda chamada externa — paga ou nao, com
sucesso ou nao — vai para `ChamadaExterna` com o custo que o PROVEDOR
informou. O teto mensal do site, limitado pelo `RADAR_TETO_MAXIMO_USD` da
instalacao, e conferido ANTES de cada chamada paga.

### 4. Buscador trocavel, com comparacao por amostragem

A API de busca do Google fechou para clientes novos em 2025 e acaba em 2027;
o Bing encerrou a dele. Sobra SERP de terceiros (DataForSEO) ou meta-buscador
proprio (SearXNG). O gratuito e o padrao; falhando, cai para o pago; e uma
fracao configuravel das buscas e repetida no pago e comparada, para responder
com numero se o gratuito serve.

### 5. Perfil de fonte na categoria

A categoria deixa de ser so um nome: carrega natureza, se sustenta a ideia
central, como citar (link, atribuicao sem link, interna), confidencialidade e
validade. Fonte sem URL deixou de derrubar o texto; forum nao sustenta a
afirmacao central; fonte vencida sai da busca.

### 6. Web como fornecedora: candidatos e confianca em dois niveis

Quando o acervo nao sustenta uma pauta, a busca de fontes gera CANDIDATOS.
Aprovar manda para a curadoria normal; recusar a lembra. A confianca e por
CAMINHO, nao por dominio (sites tem areas abertas a usuarios), em dois niveis:
"preferir na busca" (continua passando por curadoria) e "aprovar
automaticamente" (entra curado, com aviso em vermelho e confirmacao). Em
plataforma aberta (YouTube, Medium, foruns) o dominio inteiro e recusado; a
confianca e no canal ou autor.

### 7. Nota do especialista, e nao "gerar sem fonte"

Para o tema sem artigo, a saida e a pessoa escrever o que sabe — e isso vira
fonte atribuida a ela. Gerar sem fonte nenhuma fica desenhado e desligado.

## Consequencias

- O custo de descobrir pautas passa a ser visivel e limitado por site.
- A curadoria ganha trabalho novo (fontes sugeridas), em troca de o acervo
  deixar de depender de alguem sair cacando PDF.
- Duas dependencias nao oficiais: a legenda do YouTube
  (`youtube-transcript-api`) e o SearXNG. As duas tem plano B: audio
  transcrito no worker (`docs/WORKER_TRANSCRICAO.md`) e a DataForSEO.
