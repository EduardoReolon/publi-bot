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

## Conectar as APIs (o que o dono precisa fazer)

Sem conectar, tudo funciona no **copiar e colar** — a medicao de cliques
tambem. Para publicar sozinho, cada rede pede um **app** criado pelo dono do
PubliBot (uma vez, vale para todos os clientes) e, depois, cada conta conecta
pela tela (Configurar > Conectar a API). O endereco de retorno que cada app
pede aparece na tela: `https://<dominio do painel>/redes/conectar/retorno/`.

| Rede | Onde | O que pedir | Variaveis no `.env` |
|---|---|---|---|
| LinkedIn | linkedin.com/developers (app ligado a uma pagina) | "Share on LinkedIn" e "Sign In with LinkedIn using OpenID Connect" (perfil pessoal, liberacao imediata); "Community Management API" para pagina (revisao do LinkedIn) | `SOCIAL_LINKEDIN_CLIENT_ID`, `SOCIAL_LINKEDIN_CLIENT_SECRET` (`SOCIAL_LINKEDIN_VERSAO`, padrao 202509) |
| Instagram | developers.facebook.com (app tipo Empresa) | "Facebook Login" e "Instagram Graph API"; conta do Instagram **profissional** ligada a uma pagina do Facebook; revisao das permissoes `instagram_content_publish` e `instagram_manage_comments` para usar com contas de clientes | `SOCIAL_META_APP_ID`, `SOCIAL_META_APP_SECRET` (`SOCIAL_META_VERSAO`, padrao v21.0) |
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
| `medir-posts` | 1x/dia | resultado, comentarios, seguidores, referencias do nicho (semanal) e placar |
| `recalcular-temas` | 1x/dia | temas entre os artigos, com a nota |
| `placar-coletivo` | 1x/dia | soma do placar das abordagens entre todos os clientes |

## Ideias para depois

- **Plateia simulada** (personas de vozes reais que comparam as versoes antes
  de publicar, com calibracao contra o resultado real): desenho completo em
  [PLATEIA_SIMULADA.md](PLATEIA_SIMULADA.md).

- Melhor horario por conta, aprendido dos cliques (hoje: dias e horarios fixos).
- Abordagem por publico: o placar ja e por conta; dar para o modelo os 2
  melhores posts da conta como exemplo.
- Reels/video curto a partir do carrossel.
- Contrato com o site: mandar `utm_source`/`utm_campaign` da entrada na
  conversao, para atribuir a conversao ao post exato (hoje: ao artigo).
