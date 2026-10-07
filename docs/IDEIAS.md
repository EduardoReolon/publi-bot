# Ideias para depois

(Os links quebrados sairam daqui: estao em Radar > Imprensa e links,
`apps/radar/links_quebrados.py`.)

O que ja foi pensado e ficou de fora de proposito, com o motivo. Antes de
construir uma delas, releia o motivo: ele pode continuar valendo.

## Modelo julgando o artigo candidato de um link quebrado

**O que e.** Mandar a pagina que sumiu e o artigo candidato para um modelo
dizer se um substitui o outro. **Por que ficou de fora.** O algoritmo ja corta o
caso ruim (com um artigo so publicado, ele era o "mais perto" de tudo): exige
termo do assunto em comum, e o candidato so entra no lugar com o "Serve" da
pessoa; "Nao serve" nao volta. Rever se, com muitos artigos, os candidatos
errados continuarem comuns.

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

## Relatorio gratis em troca de link

**Por que nao.** A politica de spam do Google cita "trocar produtos ou
servicos por links" como esquema de links, mesmo sem dinheiro. A versao que
funciona e a mesma ideia sem a condicao: o relatorio (ou um levantamento
publico com os dados) oferecido como cortesia, citado por quem achar util.

## Plugin de WordPress

Implementar o contrato `/api/v1` como plugin de WordPress (a opcao
"WordPress (futuro)" da integracao). Abre o PubliBot para sites que nao sao
em Django — inclusive, um dia, como produto para outras pessoas.
