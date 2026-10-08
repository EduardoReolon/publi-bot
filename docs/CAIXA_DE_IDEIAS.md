# Caixa de ideias — `apps/ideias`

Menu **Ideias**. A pessoa ouviu algo e tem uma opiniao; manda como vier, por
texto ou audio, com links se tiver ("dizem X, eu acho Y; pesquise nas
noticias"). O resto acontece em segundo plano.

## O caminho

1. **Guardar** (o clique so grava e dispara `tasks.processar_ideia`).
2. **Audio** vira texto no worker da placa (o mesmo do acervo). Placa ocupada:
   tenta de novo em 10 minutos.
3. **Ler** (`investigacao.ler`, prompt `ideia_leitura`, uma chamada): o modelo
   separa a **afirmacao** que circula, a **tese** do autor e de 3 a 6
   **frentes** — linhas de raciocinio, cada uma com papel, descricao,
   consultas, se cabe estudo cientifico ou video, e os links do autor que
   pertencem a ela. Diz tambem se rende **site, redes ou os dois**.
4. **Pauta** sugerida (`Topic.origin = ideia`), com o debate e as frentes em
   `Topic.debate`, resumidos na orientacao.
5. **Buscar** cada frente (`investigacao.buscar`), tudo como sugestao para a
   curadoria da pauta, marcado com a frente e o papel (`metricas`):

| Papel da frente | O que e | Vagas (paginas + estudos por consulta) | Papel da fonte |
|---|---|---|---|
| discurso | o que se diz: materias, posts, videos | 4 + videos se for o caso | **DISCURSO** |
| a_favor | sustentaria a tese do autor | 3 + 2 | evidencia |
| contra | a melhor evidencia contra a tese | **5 + 3** | evidencia |
| alternativa | outra explicacao, que nem a afirmacao nem a tese consideram | 3 + 2 | evidencia |

O contra tem mais vagas de proposito: a ideia so vale se sobreviver a melhor
evidencia contra ela. Toda leitura tem pelo menos uma frente de discurso e uma
contra (se o modelo nao montar, o PubliBot acrescenta).

**Voce comanda as frentes pelo texto**: "considere a frente Produtividade como
outra explicacao", "a frente X e contraria", "o link do g1 e da frente O que o
jornal diz". O modelo usa exatamente esses nomes e papeis e completa o resto.
Link colado sem frente vai para o discurso (e o que voce viu); link que o
modelo inventar e descartado (so valem os que voce colou).

6. **Curadoria** (Documentos > Fontes sugeridas, filtrada pela pauta; o botao
   "Fazer a curadoria" da ideia leva direto).
7. A pauta segue o caminho de sempre (aprovar, gerar A/B). Se quiser, **Levar
   as redes**: um post de "noticia ou estudo comentado" com as fontes
   aprovadas, cada uma com o seu papel.

## Refinar, aprovar, descartar

- **Investigar de novo e REFINAMENTO** (na pauta, "Refinar a investigacao"; na
  ideia, "Ler e buscar de novo"): a mesma ideia e lida de novo com as frentes
  atuais FIXAS (`fundir_frentes`): o nome e o papel ficam, porque as fontes
  achadas e decididas estao ligadas a elas; a leitura nova pode corrigir o que
  se diz e a suspeita, melhorar a descricao e as buscas das frentes e
  ACRESCENTAR frentes. So as novas sao buscadas, a nao ser que se marque
  "buscar de novo tambem nas frentes que ja existem". Para mudar as frentes,
  mande uma ideia nova.
- **Aprovar a ideia**: pronta para as redes, com ou sem artigo no site. Aprovar
  (ou publicar) um artigo que nasceu da pauta da ideia tambem a aprova. So
  ideias aprovadas entram na escolha do dia das redes (nunca as que estao em
  curadoria), e so enquanto a pauta nao tem artigo publicado.
- **Descartar** com "rejeitar a pauta tambem" (marcado quando a pauta ainda nao
  tem artigo): nada fica de lixo em Pautas.
- A lista de Ideias e em grupos: em andamento, esperando curadoria (abertos),
  aprovadas, viraram pauta e descartadas (recolhidos).

## Preparar com outra IA

"Preparar com outra IA" (ao mandar a ideia, ou depois, na propria ideia) nao
chama o modelo daqui: monta o pedido, que manda a outra IA CONVERSAR antes de
responder (reescrever o que se diz, a suspeita e o contraste; apontar as
ambiguidades; perguntar; confirmar as frentes) e so entao devolver o JSON. O
pedido (o mesmo prompt `ideia_leitura`, com a
licenca para pesquisar na web e trazer links reais) para colar numa IA grande.
A resposta colada de volta (JSON, pode vir dentro de texto ou bloco de codigo)
passa pelas mesmas regras da leitura daqui (frente "contra" garantida, papeis
validos, limites); os links que a outra IA achou entram como sugestoes,
marcadas "achado pela outra IA (confira)", e passam pela curadoria como
qualquer fonte. Depois a tarefa `buscar_ideia` cria a pauta e busca as frentes.
"Deixar o modelo daqui ler" volta ao caminho normal.

## Pedir um veredito a outra IA

No ramo Investigacao, "Pedir um veredito a outra IA" (`apps/ideias/veredito.py`)
monta um dossie para colar numa IA grande: a afirmacao, a suspeita, as frentes
e, de cada fonte, o CONTEUDO (o texto do acervo, o capturado na curadoria ou, na
falta, o resumo da busca, marcado), cortado para caber (~60 mil caracteres no
total). Nao vai so o link: um modelo de fora abre dois ou tres, no maximo.

Cada fonte leva o MEIO (video/fala, texto publicado, orgao oficial, estudo,
comunidade — pela mesma `natureza_sugerida` da curadoria), o veiculo ou canal,
o papel (o que se diz / evidencia) e se ja foi aprovada ou ainda espera. O
pedido manda: veredito com as fontes; separar o que a fonte MOSTRA do que ela
DIZ; comparar como cada meio conta a historia e para que publico (diferenca de
fato ou de enfase); o que falta; e a conclusao honesta para um artigo.
Recusadas ficam de fora.

A parte 5 do pedido e o **REFINAMENTO**: um bloco ```json no fim da resposta,
com as frentes que mudam ou entram (nome exato da existente para melhorar;
`buscar_de_novo` para buscar de novo nela), `capturar_texto` (os [Fn] que
merecem o texto completo), `sugestoes_de_curadoria` ([Fn], aprovar / recusar /
discurso, motivo) e, se precisar, a correcao da afirmacao e da tese. A resposta
INTEIRA e colada de volta no mesmo bloco ("Guardar o veredito e refinar",
`veredito.aplicar_veredito`): o texto fica na pauta ("Ultimo veredito"); as
frentes passam pelas mesmas regras do refinamento (nenhuma e apagada nem
renomeada); a tarefa `depois_do_veredito` captura os textos pedidos e busca so
as frentes novas e as marcadas; as sugestoes de curadoria aparecem nos cartoes,
sem decidir nada. Os [Fn] valem pela lista guardada quando o dossie foi gerado
(`debate.codigos_do_dossie`), mesmo que novas fontes cheguem depois.

- O pedido manda a IA fazer a **faxina completa**: uma sugestao (aprovar,
  recusar, discurso, com motivo de uma linha) para CADA fonte ainda nao curada
  — lixo, duplicata, velha, fora do escopo; nas ja aprovadas, so aponta o que
  for claramente lixo ("a IA sugere recusar", na lista de aprovadas).
- **Aplicar as sugestoes da IA** (botao no bloco do veredito): recusar e
  discurso sao aplicados; aprovar, so quando a IA viu o texto da fonte (nao so o
  resumo da busca) — as outras continuam como sugestao. Estudo sem PDF e video
  sem legenda caem no "esperando o arquivo" da frente, como sempre.
- **Pedidos de PDF** (`pedidos_de_pdf` no JSON: a fonte e o que procurar): ficam
  na fonte ("A IA quer deste texto completo"). Com o texto completo no acervo, os
  paragrafos que respondem sao achados por vetor, sem modelo (o mesmo
  `pesquisa.trechos_pedidos` do B): entram no proximo dossie e na geracao do A
  (`referencias.trechos_pedidos_da_pauta`, pela extensao `pedidos_de_texto`).

## A geracao com a investigacao

- **A** leva a investigacao: as evidencias aprovadas nas frentes (pelo acervo),
  os paragrafos dos pedidos de PDF e o bloco do debate, com o **plano das secoes
  na ordem das frentes** (`debate.plano`: o cenario, a favor, contra, outras
  explicacoes, conclusao). Na pauta, um rotulo vermelho recomenda a IA grande
  ("Artigo com outra IA (A)"), cujo pedido leva o mesmo debate; "pauta
  investigada" e um dos motivos de peso.
- **B** nao leva a investigacao: so a pesquisa cientifica dele, mais o debate (o
  que se diz, a suspeita, os exemplos do discurso). Rotulo vermelho na pauta. "Capturar o texto das paginas que faltam" baixa, em
segundo plano, o texto das paginas que so tinham o resumo da busca.

## Videos e estudos: o que acontece ao aprovar

- **Video**: a legenda e conferida ANTES da decisao (tarefa logo depois da
  busca das frentes, ou "Conferir a legenda agora" no cartao) e a transcricao
  fica guardada no candidato. O cartao diz: "com transcricao (N palavras)",
  "sem legenda: so com o audio", "legenda nao conferida (YouTube recusou)" ou
  "ainda nao conferida". Aprovado como evidencia, usa a transcricao guardada;
  sem legenda, fica esperando o audio. Aprovado como discurso, a fala entra
  (sem legenda, so titulo e descricao, e a lista de aprovadas avisa "sem
  transcricao"). Link do YouTube achado pela busca da web ou colado vira video.
- **Estudo sem PDF aberto**: aprovado, fica esperando o PDF.
- Quem espera o audio ou o PDF aparece **na propria frente**, com o envio ali
  mesmo (e tambem no topo de Fontes sugeridas). Quem falhou aparece com o motivo.

## A tela da pauta (arvore)

Tudo recolhido, um ramo por assunto: **Sobre a pauta** (no topo), **Artigo A**
e **Artigo B** (o status no titulo; abre sozinho se falhou; as referencias do
acervo e a pesquisa de artigos ficam dentro de cada um), **Investigacao** e
**Dados publicos**. Na Investigacao, uma linha cinza explica que ela nao e um
terceiro modo de gerar, e "Como o resultado entra em cada geracao" expande com
exemplos. Cada frente e um ramo com os mesmos cartoes da curadoria (abrir o
site, enviar PDF, aprovar como evidencia ou discurso, recusar); a decisao volta
para a mesma frente (`#frente-N`). "Abrir na tela de curadoria" leva as Fontes
sugeridas filtradas por pauta **e** frente, com as duas no topo. Em Dados
publicos, "Pre-visualizar o que vai para o modelo" carrega (so ao abrir) o
bloco exato que o modelo recebe.

## Modo investigativo em qualquer pauta

Tudo o que a ideia tem vale para a pauta comum (do radar, do link quebrado,
manual): ao ligar o modo investigativo, a pauta ganha uma ideia ligada a ela,
e dai em diante sao os mesmos recursos — frentes com curadoria, refinamento,
dossie e veredito da outra IA, aprovar e levar as redes. Na propria pauta,
"Com outra IA" (ao ligar ou ao refinar) mostra o pedido para copiar e o campo
para colar a resposta, sem ir para a tela de Ideias; o titulo da pauta nunca
muda.

Uma ideia e uma pauta com a leitura por frentes. O mesmo vale para a pauta que
ja existe: na tela da pauta e na revisao do artigo, o ramo **Investigacao** >
"Ligar o modo investigativo" (com um campo opcional: "dizem X, eu
acho Y, considere a frente Z como...", links tambem). Em segundo plano, a
mesma leitura e as mesmas buscas por frente; a pauta ganha o debate (o titulo
nao muda) e as fontes vao para a curadoria dela. Aparece tambem em Ideias.

O algoritmo sugere: quando a tese do artigo saiu com as fontes em
concordancia **parcial** ou **divergente** (`Article.consensus`, do passo de
consenso), o cartao avisa na revisao que e o caso tipico para investigar. O
artigo ja escrito nao muda sozinho: as frentes entram na proxima geracao
("Gerar de novo do zero"). Ligado ao nucleo pelos pontos de extensao
`blocos_da_pauta` e `blocos_do_artigo` (`apps/ops/extensoes.py`).

## Os dois tipos de fonte (e a trava)

- **Evidencia** — sustenta o que o texto afirma. Vai para o acervo, com
  citacao, como sempre.
- **Discurso** — o "o que se diz" em debate (materia, post, video). E citado e
  analisado ("segundo a materia X"), **nunca usado como prova**.

A garantia nao depende do modelo obedecer: depende de onde o texto fica.

1. Discurso aprovado **nao vira documento** (`fontes_web.aprovar_como_discurso`):
   o texto fica so no `CandidatoDeFonte` (`papel = discurso`), com a frente. A busca do
   acervo — a unica porta pela qual uma fonte vira evidencia de um artigo —
   so le documentos; portanto nunca o encontra, em nenhuma pauta.
2. `fontes_web.aprovar` desvia todo candidato com papel discurso para
   `aprovar_como_discurso`, venha o pedido de onde vier (tela, caminho
   confiavel, provisoria). `provisorias.acolher` recusa discurso. Busca de
   discurso nunca aprova sozinha (nem caminho APROVAR, nem canal confiavel).
3. O discurso chega ao artigo so pelo **bloco do debate** (`content/debate.py`),
   rotulado: "NAO sao evidencia: cite como o que se diz, sem link e sem numero
   de fonte; numero deles so atribuido". Ele entra no planejamento, em cada
   secao e na abertura/fecho. Sem colchete, para nao parecer marcador de
   citacao ([n] e so das fontes de evidencia).
4. Para usar uma pagina do discurso como evidencia, a pessoa troca o papel na
   curadoria ("E evidencia, na verdade") — decisao explicita, nunca automatica.

O bloco tambem pede a postura: a conclusao sai das fontes de evidencia; se
elas sustentam o que se diz, o texto diz isso; se apontam outra explicacao,
apresenta; e diz o que a evidencia NAO permite concluir. Estrutura sugerida:
o que se diz -> por que convence -> o que a evidencia mostra (a favor e
contra) -> conclusao honesta. Critica a afirmacao, nunca as pessoas.

## Investigacao: o que ja e algoritmo

- **Seguir as citacoes** (`fontes_web.seguir_citacoes`): ao aprovar um
  discurso, os links da pagina que parecem fonte primaria (PDF, DOI, pagina de
  estudo/relatorio/pesquisa, site .gov/.edu/.org, instituicao confiavel do
  catalogo de dados) viram sugestao de **evidencia** da mesma pauta e da mesma
  frente, ate 5.
  E a investigacao mais barata e mais util: a materia diz "segundo pesquisa
  X", e a pesquisa X diz outra coisa com frequencia.
- **Busca do contra com mais esforco** (vagas e consultas a mais).

Proximos passos possiveis, todos sem modelo grande (ver `docs/IDEIAS.md` para
o que ficou de fora e por que):

- **Trilha do numero**: o mesmo numero ("60% das empresas") em varias
  materias do discurso quase sempre vem de uma fonte so. Agrupar por numero e
  mostrar "estas 4 materias repetem a mesma pesquisa" separa eco de
  confirmacao independente.
- **Conferir o numero na fonte primaria**: depois de seguir a citacao, procurar
  o numero da materia no texto da pesquisa (busca exata e por vizinhanca).
  Nao achou: aviso "a materia cita um numero que a fonte nao tem".
- **Quem financiou / quem vende**: dominio da pesquisa igual ao de quem vende
  o produto do tema -> aviso de conflito de interesse.
- **Retratacao**: o OpenAlex marca artigo retratado; ja e lido na curadoria.

## Onde mexer

| Quero mudar | Onde |
|---|---|
| O que o modelo separa e as consultas | prompt `ideia_leitura` (Configuracao > Prompts) |
| Vagas por papel de frente | `investigacao.VAGAS`, `ESTUDOS`, `VIDEOS` |
| O que o artigo recebe do debate | `content/debate.py` |
| O que conta como fonte primaria | `fontes_web._PRIMARIA` |

Limites: Instagram, TikTok e LinkedIn nao deixam procurar posts de terceiros
pela API — o link colado na ideia resolve. Cada ideia custa uma chamada de
modelo, algumas buscas (busca web, OpenAlex, e YouTube se for o caso: 100
unidades da cota diaria de 10.000) e a curadoria.
