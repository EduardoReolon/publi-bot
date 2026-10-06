# Redes sociais: LinkedIn, Instagram e Perfil da Empresa no Google

Modulo `apps/social` (menu **Redes**). Separado do resto do PubliBot por
decisao (ver [ADR-0023](adr/ADR-0023-redes-sociais-como-modulo-isolado.md)):
o nucleo nao sabe que ele existe, e ele so le o nucleo por `apps/social/fontes.py`.

## Para comecar (2 minutos)

1. Abra **Redes**. Na primeira visita o PubliBot cria uma conta de cada rede
   (Instagram, LinkedIn pessoal, Google), ja ligadas e com o publico do
   Negocio, liga a sugestao diaria e calcula os temas em segundo plano.
2. Na aba **Estrategia**, siga "Primeiros passos desta conta": o unico
   essencial e aprovar o primeiro post (aba Para revisar). O resto (conectar
   a API, hashtags de referencia, orcamento) e opcional e explicado ali.
3. Nao usa uma das redes? Configurar > a conta > desmarque "ligado".
4. Conta que ja existe? Conecte-a (Configurar): o PubliBot importa o passado
   e abre o **Diagnostico** (o que funcionou e o que nao, e o gasto com
   anuncios).
5. Negocio que vive de foto real (barbearia, estetica)? Em Configurar, a
   conta > "posts do banco de fotos (%)", e suba as fotos na aba **Fotos**.
   Caso real ou novidade: **+ Novo post**.

Sem conectar nenhuma API, tudo funciona: os posts chegam prontos para copiar e
colar, e os cliques sao medidos pelo link do PubliBot.

## Como esta organizado (e por que nao nas Pautas)

Pauta e "um tema vira um artigo". Post e outra coisa: **um artigo vira varios
posts**, um por conta, cada um com o jeito da rede, em datas diferentes, e o
mesmo artigo volta meses depois com outra abordagem. Por isso o menu proprio,
no formato de fila das ferramentas do mercado (Buffer, Hootsuite, Later):

| Aba | O que tem |
|---|---|
| Para revisar | Os posts escritos, com **o porque** (como nas pautas), a abordagem, os avisos da conferencia e "de onde saiu" (o material do artigo). Editar, aprovar (no proximo horario livre ou numa data), reescrever com outra abordagem, descartar. |
| Agenda | Os aprovados, por horario. Conta conectada: sai sozinho. Sem API: "Copiar texto", "Copiar comentario com o link", baixar as laminas, postar e colar o endereco ("Ja postei"). |
| Publicados | Cliques, conversoes provaveis, curtidas, comentarios, alcance; se "funcionou". |
| + Novo post | Caso real ou novidade: texto ou audio, fotos ou video. |
| Fotos | O banco de fotos: subir em lote, o que foi descartado e por que, estoque por conta. |
| Diagnostico | O que funcionou e o que nao na conta (inclusive antes do PubliBot), o gasto com anuncios e o aviso de leitura parada. |
| Comentarios | Perguntas viram Perguntas do PubliBot; a resposta aprovada volta no proprio comentario. |
| O que funciona | O placar das abordagens em cada conta. |
| Configurar | Contas (publico, tom, aprovacao, teto, dias e horarios, cores), conexao com a API, regras, abordagens. |

No artigo publicado, o botao **"Levar as redes"** cria um post em cada conta
ligada, na hora. Com **"sugerir sozinho"** ligado (Configurar), o PubliBot faz
a escolha uma vez por dia.

## Estrategia (a aba principal) — `estrategia.py`, `temas.py`, `parametros.py`

Uma pagina por conta: **em que fase ela esta, o que fazer agora e por que**.

| Fase | Seguidores (ajustavel) | Objetivo | Impulso pago |
|---|---|---|---|
| Comeco | ate 300 | descobrir o que o publico quer | teste A/B: mesmo tema, 2 abordagens, mesmo valor e publico |
| Tracao | 300–1.000 | consistencia | so o que ficou entre os 25% melhores da conta em 48 h |
| Crescimento | 1.000–10.000 | ampliar | os melhores, para publico parecido com quem engaja |
| Escala | 10.000+ | converter | trafego para os artigos com chamada para a oferta |

Os seguidores vem da API (Instagram, pagina do LinkedIn) ou sao digitados na
pagina; a pessoa pode fixar a fase.

**Comecar sem seguidores (amostra pequena).** O que o mercado e a estatistica
recomendam, e o que o PubliBot faz:

1. **Sinal de fora antes do sinal de dentro.** Os *temas* (ideias que
   atravessam varios artigos) tem nota por sinais que nao dependem de
   seguidores: perto das dores do publico, buscado no Google (grupos de
   demanda do Radar, com volume), artigos que trazem clientes, quantos artigos
   tocam no tema e o que engaja no nicho (posts de outras contas nas hashtags
   de referencia, lidos uma vez por semana pela API). No Instagram, o post
   do dia nasce do melhor tema ainda nao usado.
2. **Ponto de partida informado.** O sorteio das abordagens comeca com o
   placar das outras contas do cliente e de todas as contas do PubliBot
   (so contagens por abordagem e rede, somadas uma vez por dia), em vez de
   do zero.
3. **Poucas coisas de cada vez.** No Comeco, so 3 abordagens no sorteio.
4. **Proporcao, nao total, e contra a propria conta.** A medida "taxa" e
   (salvos + compartilhamentos + comentarios + cliques) / alcance; post com
   alcance abaixo do minimo fica *inconclusivo*; "funcionou" e acima da
   mediana da propria conta — publico pequeno ou de nicho (poucos % com
   interesse) nao e julgado pela regua de conta grande. A taxa de referencia
   de mercado (~2% Instagram, ~3% LinkedIn) aparece so para situar.
5. **Teste pago A/B.** Com orcamento, o PubliBot gera o par (mesmo tema, duas
   abordagens) e diz quanto pagar em cada, por quantos dias, com que objetivo
   e publico. Depois dos dias do teste, a de taxa maior ganha; o resultado vale
   mais que posts organicos soltos. Impulsionados ficam fora da mediana
   organica.

**Impulso pago.** O PubliBot nunca gasta: recomenda qual post, quanto, por
quantos dias, objetivo e publico, e explica como pagar em cada rede (Instagram:
"Impulsionar" ou Gerenciador de Anuncios, cartao ou saldo pre-pago; LinkedIn:
Campaign Manager, so para post com chance de cliente; Google: sem impulso de
post). A pessoa registra o valor ("Registrei o impulso") e o gasto do mes
aparece contra o orcamento.

**Tudo ajustavel** em "Parametros da estrategia": limites das fases, alcance
minimo, dias para julgar, taxas de referencia, quantas abordagens no Comeco,
peso do placar coletivo, pesos da nota dos temas, orcamento, valores do teste
e do amplificar. Valor igual ao padrao volta a seguir o padrao.

## O caminho de um post

```
artigo no ar ─▶ escolha (regras) ─▶ material (vetores) ─▶ abordagem (sorteio que aprende)
                                                                     │
          conferencia (algoritmo) ◀── modelo escreve no formato da rede ◀┘
                 │  numero que o artigo nao tem / termo proibido / tamanho → 1 nova tentativa
                 ▼
          revisao ──▶ agenda ──▶ API da rede  (ou copiar e colar)
                                     │
             cliques (link do PubliBot) + conversoes do site + engajamento da API
                                     ▼
                   "funcionou?" (acima da mediana da conta, 7 dias) ──▶ placar
```

### 1. Escolha (sem modelo) — `escolha.py`

Cada conta recebe ate o **teto da semana** (padrao 3), um por dia, sem empilhar
o que ja espera revisao. Candidatos:

- **artigo novo** (no ar ha menos de 30 dias, ainda nao foi para a conta);
- **traz clientes** (aparece na jornada de quem converteu, 90 dias): volta depois
  de N meses (padrao 3), com outra abordagem;
- **subindo no Google** (o Radar marcou como quase na primeira pagina).

Nota = proximidade entre o artigo e o **publico da conta** (vetor) + peso do
motivo + o que a rede favorece (Google: cidade das regioes do Radar no texto;
Instagram: secoes em lista, que viram carrossel; LinkedIn: dado com numero).
O mesmo artigo nao volta a mesma conta antes de 30 dias (configuravel).

### 2. Material (sem modelo) — `material.py`

O modelo nao recebe o artigo inteiro nem escreve "no vazio". Recebe:

- **identificacao** — as frases do artigo mais parecidas com as **dores** do
  negocio e com o **publico da conta** ("e o meu caso");
- **achados** — as frases com numero, as mais centrais primeiro ("nossa, que
  incrivel");
- as dores que o artigo toca e os titulos das secoes.

### 3. Abordagem (sorteio que aprende) — `experimentos.py`

Sementes (genericas, editaveis): *E o meu caso*, *Nossa, que incrivel*, *Mito
ou verdade*, *O erro comum*, *Lista pratica*, *Pergunta que incomoda*, *Olhar
de quem pratica* (so LinkedIn). Cada post usa uma. Depois de 7 dias no ar, o
post "funcionou" se ficou acima da mediana **da propria conta** na medida
escolhida (cliques, conversoes ou engajamento). A escolha seguinte e um
**Thompson sampling**: favorece quem vem ganhando sem parar de testar as
outras; abordagem nova, sem placar, tem a mesma chance. O placar da mesma rede
em outras contas conta meio ponto.

Com **2 versoes por post** (Configurar), cada artigo ganha duas abordagens para
a mesma conta; aprovar uma descarta a outra (ou publique as duas em dias
diferentes, reescrevendo uma delas).

### 4. Escrita e conferencia — `redacao.py`, `prompts.py`

Um prompt por rede (`social_linkedin`, `social_instagram`, `social_gmn`), com o
**estilo do publico** de cada uma e o guia editorial do site. A resposta e
conferida **por algoritmo**: numero que o artigo nao tem, termo proibido do
guia, endereco no texto (sai), tamanho da rede, gancho maior que o "ver mais",
laminas fora do limite. Achou: uma nova tentativa dizendo o que corrigir.
Persistiu: o post vai para a revisao com os avisos — e nunca sai sozinho.

| Rede | Publico e formato | Onde vai o link | Imagem |
|---|---|---|---|
| LinkedIn | profissional; 1.000–1.500 caracteres, gancho de ~200, pergunta no fim, ate 3 hashtags | primeiro comentario | capa do artigo |
| Instagram | 2 segundos para parar; legenda curta, 4–8 laminas (uma ideia cada) | "link na bio" (pagina do PubliBot) | carrossel montado aqui |
| Google | quem procura o servico perto; 400–900 caracteres, local, sem hashtag | botao "Saiba mais" | capa do artigo |

### 5. Laminas — `laminas.py`

Carrossel 1080 x 1350 montado por **Pillow** (modelo de imagem escreve texto
mal): a capa do artigo escurecida com o gancho, uma ideia por lamina nas cores
da conta, a chamada final. Editaveis na revisao ("titulo | texto", uma por linha).

### 6. Medicao — `medicao.py`, `views.clique`

- **Cliques:** o link do post passa pelo PubliBot (`/redes/r/<chave>/`), que
  conta e segue para o artigo com `utm_source=<rede>&utm_medium=social&
  utm_campaign=<artigo>&utm_content=<abordagem>` (o mesmo que Buffer e
  Hootsuite fazem com os encurtadores deles).
- **Link na bio (Instagram):** `/redes/bio/<chave>/` lista os artigos dos posts
  recentes, cada um pelo link rastreado do proprio post.
- **Conversoes provaveis:** conversoes do site que entraram (ou voltaram) por
  rede social e passaram pelo artigo, desde a data do post.
- **Engajamento:** pela API (LinkedIn: curtidas e comentarios; Instagram:
  alcance, curtidas, comentarios, compartilhamentos, salvos).

### 7. Comentarios — `comentarios.py`

Uma vez por dia, nos posts dos ultimos 30 dias (onde a API deixa: Instagram e
pagina do LinkedIn; o LinkedIn nao libera os comentarios do perfil pessoal; o
Google nao tem comentario em post). Pergunta e decidida por algoritmo
(termina em "?" ou comeca como pergunta). Pergunta vira **Pergunta** do
PubliBot (a mesma fila das do site, com a pesquisa do OpenAlex); aprovada a
resposta, o PubliBot responde no comentario com o comeco dela e o link.

## Regras que valem sempre

- Nunca o artigo inteiro numa rede: o post leva ao artigo.
- Aprovacao configuravel por conta; o padrao e **todo post passa por voce**.
  Mesmo com "publicar sozinho", post com aviso da conferencia espera revisao.
- "Regras que nunca se quebram" (Configurar) vao em todo post: sem promessa
  de resultado ou cura, sem urgencia falsa, sem antes e depois, sem preco como
  chamariz, so o que o artigo afirma. Editavel por negocio.

## Material proprio: caso real, novidade e fotos — `proprio.py`, `fotos.py`

Nem todo post nasce de artigo. Uma consultoria quer contar um caso; uma
barbearia vive de foto real. O "artigo" desses posts e a **Entrada** — o que a
pessoa mandou —, e o resto (abordagem com placar, conferencia, agenda,
publicacao, medicao, diagnostico) segue igual.

**+ Novo post** (caso real ou novidade):

- o que aconteceu por escrito, **ou um audio** (transcrito no mesmo worker que
  transcreve o acervo; os posts nascem quando termina, e placa ocupada so
  adia);
- fotos ou video (opcional): Instagram em carrossel (ate 10) ou **reels**;
  LinkedIn e Google, a primeira foto. Video nessas duas redes nao sai pela
  API: o post fica marcado "copiar e postar" (sem contar como falha);
- para onde leva: o artigo mais proximo (escolhido por vetor, so se for bem
  proximo), um artigo escolhido, ou um link (contato, WhatsApp); sem nada, a
  pagina inicial do site;
- caixa obrigatoria "quem aparece ou e citado autorizou" para caso real e para
  fotos.

A redacao recebe a instrucao "este post nao e de um artigo: e um caso real",
com o relato como unico material. A conferencia barra numero que a pessoa nao
contou e **avisa o que pode identificar alguem** (nome com Sr./Dona/paciente,
idade, telefone, e-mail, CPF, @). Sem foto, o Instagram ganha laminas com o
texto, como num artigo.

**Fotos** (banco de fotos, para quem vive de foto real):

1. A pessoa sobe as fotos quando tiver, do celular, em lote (com a mesma
   autorizacao). Nota opcional ("degrade navalhado").
2. Por algoritmo, sem IA: gira pela orientacao da camera, reduz, **apaga os
   metadados** (inclusive a localizacao), descarta tremida (variancia do
   Laplaciano), escura ou estourada (brilho) e quase iguais (dHash: fica a
   mais nitida). Descartada pode ser usada mesmo assim.
3. Fotos tiradas com ate 20 minutos de diferenca sao do mesmo atendimento e
   viram um carrossel; video vai sozinho (reels).
4. Cada conta tem "posts do banco de fotos (%)" (Configurar). A rodada diaria
   ve o que falta para a proporcao (ultimos 30 dias) e tira o proximo
   atendimento do banco; sem artigo para levar, o banco cobre a vaga. O mesmo
   atendimento pode ir para o Instagram e para o Google; a mesma conta nao
   repete.
5. Recorte na proporcao de cada rede (Instagram 4:5 a 1,91:1, carrossel todo
   em 4:5; Google 3:4 a 16:9).
6. Estoque: a tela Fotos e o painel avisam quando o banco da para menos de 7
   dias de posts.

**A mistura aprende**: a Estrategia mostra quanto funcionou por tipo de post
(artigos, casos, fotos). Com a conta entre 1% e 99% de fotos, a cada 30 dias,
se um tipo ganhar do outro por 20 pontos (5 posts julgados de cada), a
proporcao anda 10 pontos para ele, entre 10% e 90% (parametro "ajustar a
mistura"; 0 desliga).

**Descrever a foto (opcional)** — Configurar > "descrever as fotos com o
modelo": foto sem nota ganha uma descricao do trabalho mostrado (nunca da
pessoa) por um modelo que enxerga imagem, e ela entra no material. Precisa de
uma conexao de inferencia com modelo de visao (o pedido vai com a imagem no
formato da OpenAI ou da Anthropic); modelo sem visao recusa, e a foto segue
so com a nota. Prompt `social_foto`, editavel.

Parametros novos (Estrategia > parametros > "Fotos e mistura"): nitidez
minima, brilho minimo e maximo, ajustar a mistura.

## Conta que ja existe: historico e diagnostico — `historico.py`, `diagnostico.py`

Ao conectar um Instagram que ja tem vida (cliente que veio de outra agencia,
ou que postava por conta), o PubliBot **importa o passado sozinho** e abre a
aba **Diagnostico**:

| Vem sozinho (API) | Nao vem |
|---|---|
| Ate 500 posts: legenda, formato (carrossel, imagem, reels), data, link, curtidas, comentarios | Stories antigos (a Meta so guarda 24 h) |
| Por post: alcance, salvos, compartilhamentos | Alcance de post feito antes de a conta virar **profissional** (fica so curtidas e comentarios) |
| Comentarios de cada post, e se o dono respondeu | Mensagens diretas |
| Seguidores hoje (o historico de seguidores comeca no dia da conexao) | Seguidores de meses atras |

A importacao anda em lotes de 60 posts (as APIs limitam chamadas por hora); a
medicao diaria continua o resto, e depois traz todo dia os posts feitos direto
na rede, fora do PubliBot (o que ja esta no PubliBot — publicado pela API ou
marcado com "Ja postei" — nao duplica). Comentarios antigos **nao** viram Perguntas
(a fila nao e inundada de perguntas de anos atras).

**Nas outras redes** (`redes/linkedin.py`, `redes/gmn.py`):

| Rede | Vem sozinho (API) | Nao vem / como suprir |
|---|---|---|
| LinkedIn, **pagina** | Posts (texto, formato, data, link); por post: alcance (pessoas unicas), impressoes, reacoes, comentarios, compartilhamentos, cliques; comentarios; seguidores | Precisa da Community Management API aprovada. Ate la: a planilha de Analytics da pagina |
| LinkedIn, **perfil pessoal** | Nada (o LinkedIn fechou a leitura do perfil pessoal) | **Planilha**: Analytics > Exportar (.xlsx com os 50 posts de mais engajamento e os 50 de mais impressoes, ate 365 dias) + Shares.csv da copia dos dados (o texto de todos os posts). O PubliBot junta os dois |
| Perfil da Empresa no Google | Posts (texto, data, link); o resultado **da conta** mes a mes, ate 18 meses (visualizacoes na busca e no mapa, ligacoes, rotas, cliques no site, conversas); avaliacoes (total, nota, sem resposta) | O Google nao mede cada post. O diagnostico compara **meses com post x meses sem post** (3+ meses em cada grupo, 30% de distancia) e aponta avaliacoes de 1 a 3 estrelas sem resposta |

**Planilha de posts** (Diagnostico > "Pelo arquivo exportado da rede",
`historico.importar_planilha`): .csv ou .xlsx, em portugues ou ingles, de
qualquer rede (LinkedIn pessoal e pagina, Meta Business Suite). O cabecalho e
procurado nas primeiras linhas de cada aba, e tabelas lado a lado (a aba TOP
POSTS do LinkedIn) sao lidas uma a uma. O mesmo post em dois arquivos (texto
num, numeros no outro) e juntado pelo link ou, sem link igual, pelo dia
quando so ha um post naquele dia. Mandar de novo atualiza, nao duplica. Sem
curtidas separadas, o total de engajamentos da planilha mede o post.

O historico tambem calibra a conta: "funcionou" e contra a mediana dela, e a
mediana passa a ter historia desde o primeiro dia (na medida "taxa"; nas
medidas de clique e conversao, posts importados ficam de fora, porque nao
tinham o link rastreado).

O **diagnostico** e so algoritmo (sem modelo), com o numero e o tamanho da
amostra em cada ponto: ritmo (posts por semana, pausas, irregularidade, conta
parada), taxa mediana contra a referencia, os 3 melhores e os 3 piores, e
medianas por formato, dia da semana, horario, tamanho da legenda, hashtags e
pergunta na legenda. So aponta diferenca com 3+ posts em cada grupo e 30% de
distancia. Mais: comentarios respondidos, e no gasto com anuncios, o custo por
clique, o mais caro x o mais barato e **quanto do impulso foi em post que ja
ia mal no organico** (o desperdicio mais comum). "Copiar o diagnostico" da o
texto corrido para mandar ao dono da conta.

## Gasto com anuncios — `anuncios.py`, `redes/meta_anuncios.py`, `planilhas.py`

Meta (Instagram): dois caminhos para o mesmo lugar, porque a permissao de ler
anuncios depende de uma revisao da Meta que pode nao sair (ou ser retirada um
dia):

1. **Automatico** — Diagnostico > "Conectar anuncios": a conexao da Meta e
   refeita pedindo tambem `ads_read`. Com a permissao, o PubliBot acha a conta
   de anuncios (escolhe sozinho se houver uma) e, uma vez por dia, le cada
   anuncio do periodo todo (gasto, alcance, impressoes, cliques no link) e o
   post que ele impulsiona. O post fica marcado como impulsionado, com o valor.
2. **Planilha** — Gerenciador de Anuncios > aba *Anuncios* > periodo *Maximo* >
   *Relatorios* > *Exportar dados da tabela* (.csv) > enviar no Diagnostico.
   Portugues ou ingles; o anuncio e ligado ao post pelo comeco da legenda (a
   Meta nomeia o impulso "Publicacao do Instagram: <legenda>"). Mandar de novo
   nao duplica. O passo a passo esta na tela.

**LinkedIn Ads e Google Ads: so pela planilha.** As APIs de anuncio deles
pedem aprovacao a parte (Advertising API do LinkedIn; token de desenvolvedor
do Google Ads, com analise), que nao compensa para ler o gasto de uma conta
pequena. A mesma tela de envio aceita:

- **Campaign Manager do LinkedIn** > aba *Anuncios* > *Exportar* > *Desempenho
  do anuncio* (.csv/.xlsx; as linhas de titulo antes do cabecalho sao
  puladas). O anuncio patrocinado e ligado ao post pelo texto de introducao.
- **Google Ads** > *Campanhas* > *Todo o periodo* > *Fazer download* (.csv).
  Campanha do Google nao e de um post (busca, mapa): entra no gasto da conta,
  sem post; a linha "Total" fica de fora.

E o impulso de um post so continua valendo pela Estrategia ("Registrei o
impulso").

**Aviso de leitura parada** (painel do cliente, menu Redes e aba Diagnostico):
conta conectada sem ler ha mais de 2 dias, ou anuncios sem ler ha mais de 3,
com o ultimo erro da rede. Instabilidade passa sozinha; se continuar, o aviso
diz o que fazer (reconectar, ou mandar a planilha). Sem API de anuncios, um
lembrete quando a ultima planilha tem mais de 35 dias.

## Segunda opiniao de uma IA grande — `outra_ia.py`

Na Estrategia, "Copiar para outra IA" leva a um modelo grande (Claude,
ChatGPT, Gemini) o negocio, a conta, o placar das abordagens, os posts recentes
com resultado, o que engaja no nicho e os candidatos (temas `m-` e artigos
`a-`, com codigo). O pedido manda conversar antes e, **se a IA puder pesquisar
na web**, trazer eventos confirmados com link (campanhas do mes, datas da
area, regra nova, estudo que virou noticia) — sem pesquisa, nada de evento.

A resposta colada vira previa; aplicado o que estiver marcado:

- **PROPOSTAS** viram posts do tema ou artigo, com o gancho sugerido como
  *ideia* para quem escreve (o material do artigo continua sendo o limite);
- **EVENTOS** viram post do candidato ligado, com o evento como gancho (sem
  link de fonte, a linha e ignorada);
- **ABORDAGENS** entram no sorteio da rede e competem pelo placar.

Tudo fica marcado (motivo "Sugerido pela outra IA", abordagem "da outra IA"),
e a Estrategia mostra o **comparativo**: a proporcao de posts que funcionaram
entre os sugeridos pela outra IA e os escolhidos pelo PubliBot. Assim ela
ganha (ou perde) credito com dado, conta por conta.

## Conectar as APIs (o que o dono precisa fazer)

Sem conectar, tudo funciona no **copiar e colar** — a medicao de cliques
tambem. Para publicar sozinho, cada rede pede um **app** criado pelo dono do
PubliBot (uma vez, vale para todos os clientes) e, depois, cada conta conecta
pela tela (Configurar > Conectar a API). O endereco de retorno e um so, no
dominio raiz, para todos os clientes: `https://<ROOT_DOMAIN>/redes/retorno/`
(`core/retorno_oauth.py`). Politica de privacidade, termos e exclusao de dados
tambem ja existem: o que preencher em cada app esta em
[CONTAS_EXTERNAS.md](CONTAS_EXTERNAS.md), secao 8.

| Rede | Onde | O que pedir | Variaveis no `.env` |
|---|---|---|---|
| LinkedIn | linkedin.com/developers (app ligado a uma pagina) | "Share on LinkedIn" e "Sign In with LinkedIn using OpenID Connect" (perfil pessoal, liberacao imediata); "Community Management API" para pagina (revisao do LinkedIn) | `SOCIAL_LINKEDIN_CLIENT_ID`, `SOCIAL_LINKEDIN_CLIENT_SECRET` (`SOCIAL_LINKEDIN_VERSAO`, padrao 202509) |
| Instagram | developers.facebook.com (app tipo Empresa) | "Facebook Login" e "Instagram Graph API"; conta do Instagram **profissional** ligada a uma pagina do Facebook; revisao das permissoes `instagram_content_publish`, `instagram_manage_comments` e `instagram_manage_insights` para usar com contas de clientes. Opcional: produto "Marketing API" e revisao de `ads_read` (gasto com anuncios automatico; sem ela, planilha) | `SOCIAL_META_APP_ID`, `SOCIAL_META_APP_SECRET` (`SOCIAL_META_VERSAO`, padrao v21.0) |
| Google | formulario de acesso a Business Profile API (semanas; cota zero ate aprovar) + Google Cloud: ativar "My Business Account Management", "My Business Business Information" e "Google My Business" APIs; credencial OAuth "Aplicativo da Web" | — | `SOCIAL_GOOGLE_CLIENT_ID`, `SOCIAL_GOOGLE_CLIENT_SECRET` |

O acesso do LinkedIn e da Meta vence em ~60 dias: a tela marca a conta uma
semana antes ("acesso vence em"), e a Meta e renovada sozinha antes de vencer;
o LinkedIn pede reconectar. O do Google renova sozinho.

As APIs foram escritas pelas documentacoes oficiais e testadas sobre respostas
simuladas (`apps/social/tests/test_apis.py`); **a primeira publicacao de verdade
em cada rede e o teste que falta** — faca com um post de teste e confira.

## Mudar sem deploy

- O jeito de escrever de uma rede: o prompt dela em `/admin/content/prompttemplate/`
  (ou, para levar a semente nova do codigo:
  `manage.py semear_prompts --todos --atualizar --so social_instagram`).
- As abordagens: Redes > Configurar > Abordagens (criar, editar, desligar).
- O publico, o tom e as instrucoes de cada conta; as regras e a medida de
  sucesso: Redes > Configurar.

## Mudar com codigo

- Rede nova: um modulo em `apps/social/redes/` com `REDE` (formato, estilo,
  publicador, conexao) e uma linha em `apps/social/redes/__init__.py`, mais a
  semente do prompt em `apps/social/prompts.py`.
- Nunca importe o nucleo fora de `apps/social/fontes.py` (o teste
  `test_fronteira.py` recusa).

## Tarefas (Celery beat)

| Tarefa | Quando | O que faz |
|---|---|---|
| `sugerir-posts` | 1x/dia | escolha do dia (com "sugerir sozinho") |
| `publicar-posts` | 5 em 5 min | publica os aprovados vencidos (contas conectadas) e responde comentarios com resposta aprovada |
| `medir-posts` | 1x/dia | resultado, comentarios, seguidores, referencias do nicho (semanal), proximo lote do historico, gasto com anuncios (API), placar, mistura artigos x fotos e fotos que faltam descrever |
| `recalcular-temas` | 1x/dia | temas entre os artigos, com a nota |
| `placar-coletivo` | 1x/dia | soma do placar das abordagens entre todos os clientes |

## Ideias para depois

- **Plateia simulada** (personas de vozes reais que comparam as versoes antes
  de publicar, com calibracao contra o resultado real): desenho completo em
  [PLATEIA_SIMULADA.md](PLATEIA_SIMULADA.md).

- **O que estava DENTRO dos posts importados** (texto das laminas por OCR,
  fala dos reels pela transcricao que o worker ja faz): hoje o diagnostico usa
  legenda, formato e numeros. Se valer, so por amostra — os 10 melhores e os
  10 piores da conta, sob pedido, na fila de baixa prioridade do worker e com
  teto por conta —, nunca o acervo inteiro de uma vez.
- Melhor horario por conta, aprendido dos cliques (hoje: dias e horarios fixos).
- Abordagem por publico: o placar ja e por conta; dar para o modelo os 2
  melhores posts da conta como exemplo.
- Reels/video curto a partir do carrossel.
- Contrato com o site: mandar `utm_source`/`utm_campaign` da entrada na
  conversao, para atribuir a conversao ao post exato (hoje: ao artigo).
