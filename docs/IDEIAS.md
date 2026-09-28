# Ideias para depois

O que ja foi pensado e ficou de fora de proposito, com o motivo. Antes de
construir uma delas, releia o motivo: ele pode continuar valendo.

## Backlinks dos concorrentes ("link intersect")

**O que e.** Listar os sites que linkam para dois ou mais concorrentes e nao
para voce. Quem ja linka o concorrente aceita o assunto; a chance de linkar
voce tambem e maior. E pratica comum de mercado.

**Por que ficou de fora.** Exige uma base de backlinks (DataForSEO Backlinks,
Ahrefs, Semrush), cobrada a parte. O diagnostico "Precisa de backlinks?" do
Radar, pelo Search Console, ja diz se falta autoridade sem essa base.

**Se um dia valer:** com a API de backlinks da DataForSEO, a interseccao dos
dominios que apontam para os concorrentes confirmados, menos os que ja
apontam para o site, vira uma lista em Possiveis parceiros, com a pagina que
linka e o texto do link.

## Links quebrados ("broken link building")

**O que e.** Achar, em sites do seu assunto, links que apontam para paginas
que nao existem mais (erro 404), e oferecer o seu artigo que cobre o mesmo
tema no lugar. O dono do site ganha (conserta um erro); voce ganha o link.

**Como caberia no PubliBot, devagar pelo Celery.** As paginas dos parceiros
provaveis e dos vizinhos ja estao nas buscas guardadas do radar. Uma tarefa
de fundo baixaria algumas por hora, extrairia os links de saida, conferiria
cada um (so um HEAD, com limite por dominio) e guardaria os quebrados cujo
texto do link e perto de um artigo publicado. A tela mostraria: "a pagina X
tem um link quebrado para Y; o seu artigo Z cobre o mesmo", com o e-mail
pronto para copiar.

**Estado:** proposto, esperando decisao.

## Relatorio gratis em troca de link

**Por que nao.** A politica de spam do Google cita "trocar produtos ou
servicos por links" como esquema de links, mesmo sem dinheiro. A versao que
funciona e a mesma ideia sem a condicao: o relatorio (ou um levantamento
publico com os dados) oferecido como cortesia, citado por quem achar util.

## Plugin de WordPress

Implementar o contrato `/api/v1` como plugin de WordPress (a opcao
"WordPress (futuro)" da integracao). Abre o PubliBot para sites que nao sao
em Django — inclusive, um dia, como produto para outras pessoas.
