# Caixa de ideias — `apps/ideias`

Menu **Ideias**. A pessoa ouviu algo e tem uma opiniao; manda como vier, por
texto ou audio, com links se tiver ("dizem X, eu acho Y; pesquise nas
noticias"). O resto acontece em segundo plano.

## O caminho

1. **Guardar** (o clique so grava e dispara `tasks.processar_ideia`).
2. **Audio** vira texto no worker da placa (o mesmo do acervo). Placa ocupada:
   tenta de novo em 10 minutos.
3. **Ler** (`investigacao.ler`, prompt `ideia_leitura`, uma chamada): o modelo
   separa a **afirmacao** que circula, a **tese** do autor, ate 3 **outras
   explicacoes** e as consultas de cada lado; diz se cabe estudo cientifico,
   se o tema tem discurso em video, e se rende **site, redes ou os dois**.
4. **Pauta** sugerida (`Topic.origin = ideia`), com o debate gravado em
   `Topic.debate` e resumido na orientacao.
5. **Buscar**, tudo como sugestao para a curadoria da pauta:

| Lado | Onde busca | Vagas | Papel |
|---|---|---|---|
| Discurso (o que se diz) | links colados, busca web, YouTube (se o tema tem video) | 4 + 2 videos | **DISCURSO** |
| A favor da tese | busca web, OpenAlex | 3 + 2 por consulta | evidencia |
| Contra a tese (e a favor das outras explicacoes) | busca web, OpenAlex | **5** + 3 por consulta, mais consultas | evidencia |

O contra tem mais consultas e mais vagas de proposito: a ideia so vale se
sobreviver a melhor evidencia contra ela.

6. **Curadoria** (Documentos > Fontes sugeridas, filtrada pela pauta; o botao
   "Fazer a curadoria" da ideia leva direto).
7. A pauta segue o caminho de sempre (aprovar, gerar A/B). Se quiser, **Levar
   as redes**: um post de "noticia ou estudo comentado" com as fontes
   aprovadas, cada uma com o seu papel.

## Os dois tipos de fonte (e a trava)

- **Evidencia** — sustenta o que o texto afirma. Vai para o acervo, com
  citacao, como sempre.
- **Discurso** — o "o que se diz" em debate (materia, post, video). E citado e
  analisado ("segundo a materia X"), **nunca usado como prova**.

A garantia nao depende do modelo obedecer: depende de onde o texto fica.

1. Discurso aprovado **nao vira documento** (`fontes_web.aprovar_como_discurso`):
   o texto fica so no `CandidatoDeFonte` (`papel = discurso`). A busca do
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
  catalogo de dados) viram sugestao de **evidencia** da mesma pauta, ate 5.
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
| Vagas por lado | `investigacao.VAGAS`, `ESTUDOS`, `VIDEOS_DO_DISCURSO` |
| O que o artigo recebe do debate | `content/debate.py` |
| O que conta como fonte primaria | `fontes_web._PRIMARIA` |

Limites: Instagram, TikTok e LinkedIn nao deixam procurar posts de terceiros
pela API — o link colado na ideia resolve. Cada ideia custa uma chamada de
modelo, algumas buscas (busca web, OpenAlex, e YouTube se for o caso: 100
unidades da cota diaria de 10.000) e a curadoria.
