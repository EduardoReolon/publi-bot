# Contas externas: o que criar, onde, e como testar

Nada aqui é obrigatório para publicar. Cada item liga uma parte do radar ou da
busca de fontes; sem ele, aquela parte fica desligada e o resto funciona.

| Serviço | Custo | Onde a chave fica | Liga |
|---|---|---|---|
| SearXNG | gratuito (servidor seu) | `.env` (`SEARXNG_URL`) ou tela do Radar | buscador gratuito |
| DataForSEO | pago por chamada | tela do Radar, **por site** | SERP, volume, concorrentes, avaliações |
| YouTube Data API v3 | gratuito até a cota | tela do Radar, **por site** | comentários e vídeos como fonte |
| Search Console | gratuito | `.env` (arquivo da conta de serviço) + convite em cada propriedade | "quase lá" e desempenho dos artigos |
| OpenAlex (+ Unpaywall) | gratuito, com chave | tela do Radar, **por site** | artigos científicos como fonte, e o PDF de acesso aberto |
| Legendas do YouTube | gratuito, sem conta | — | transcrição de vídeo |
| Transcrição no worker | sua GPU | worker-gpu | vídeo sem legenda, áudio enviado |

As chaves por site ficam cifradas no banco e nunca voltam para a tela. O
`.env` só tem o que é da instalação inteira.

---

## 1. SearXNG — buscador gratuito (opcional)

É um meta-buscador de código aberto (AGPL, projeto ativo desde 2021, fork do
Searx). Você hospeda; ele consulta Google, Bing, DuckDuckGo etc. e devolve
tudo junto. **Não traz "as pessoas também perguntam" nem volume** — para isso,
só a DataForSEO. Se você não quiser manter mais um serviço, pule: com o
buscador na tela em "DataForSEO", o SearXNG não é usado.

**Subir** (no mesmo servidor, só em `127.0.0.1`):

```bash
mkdir -p /opt/searxng && cd /opt/searxng
cat > settings.yml <<'EOF'
use_default_settings: true
server:
  secret_key: "troque-por-um-valor-aleatorio"
  limiter: false          # uso privado, sem visitantes
  bind_address: "0.0.0.0"
search:
  formats: [html, json]   # sem o json o publi-bot recebe 403
EOF
docker run -d --name searxng --restart unless-stopped \
  -p 127.0.0.1:8888:8080 -v /opt/searxng:/etc/searxng searxng/searxng
```

No `.env` do publi-bot: `SEARXNG_URL=http://127.0.0.1:8888`.

**Testar:**

```bash
curl -s 'http://127.0.0.1:8888/search?q=bdi+obra&format=json' | head -c 300
```

Tem que vir JSON com `"results"`. Depois, no Radar, faça uma busca manual: o
livro-caixa mostra a chamada com provedor "SearXNG".

**Saber se ele serve:** com a DataForSEO também configurada, ponha a "taxa de
comparação" em 10–20 %. O painel mostra quanto do top 10 pago o gratuito trouxe.

---

## 2. DataForSEO — SERP, volume, concorrentes (pago)

1. Crie a conta em <https://app.dataforseo.com/register>. Vem um crédito de
   teste (US$ 1 quando este texto foi escrito).
2. No painel, **API Access** (ou "API Dashboard"): ali estão o **login** (o
   e-mail) e a **API password** — que NÃO é a senha de entrar no site.
3. Para uso real, um depósito (mínimo de US$ 50 quando este texto foi escrito).
   O saldo não vence.

**Testar a conta** (esta rota é gratuita e mostra o saldo):

```bash
curl -s -u 'SEU_LOGIN:SUA_API_PASSWORD' \
  https://api.dataforseo.com/v3/appendix/user_data | head -c 400
```

`"status_code": 20000` é sucesso. `40100` é login/senha errados.

**No publi-bot:** Radar › Contas externas, login e API password. Depois:

- Busca manual com "buscar volume" marcado: confere SERP e volume ao vivo;
- "Rodar o radar agora": com o modo **fila** (padrão), a rodada fica
  "aguardando" e termina sozinha em alguns minutos;
- o livro-caixa mostra cada chamada com o custo que a DataForSEO informou.

**Custos de referência** (os usados para conferir o teto; o registrado é o da
resposta):

| O quê | Ao vivo | Fila padrão |
|---|---|---|
| Página de resultados (SERP, 10 itens) | US$ 0,002 | US$ 0,0006 |
| Volume, até 1000 palavras por tarefa | US$ 0,09 | US$ 0,06 |
| Buscas do concorrente (Labs) | ~US$ 0,01 + 0,0001/palavra | — (só ao vivo) |
| Avaliações no Google | a conferir | só existe na fila |

**Precisa ser conferido no primeiro uso real** (foi escrito pela
documentação, sem acesso à API durante o desenvolvimento):

- as rotas da fila (`task_post`/`task_get`);
- a do Labs (`ranked_keywords/live`, com o filtro por posição);
- a das avaliações (`business_data/google/reviews`);
- a lista de locais (`keywords_data/google_ads/locations/br`), usada pelo
  seletor de regiões;
- o campo `date_from` no volume de busca, que pede dois anos de histórico
  mensal (base do crescimento das Oportunidades);
- se o volume do Google Ads aceita todo código de cidade.

Se alguma responder erro, a mensagem da DataForSEO aparece na rodada e em
"Últimas chamadas externas" (Radar › Configuração e custos).

---

## 3. YouTube Data API v3 — comentários e vídeos (gratuito até a cota)

1. <https://console.cloud.google.com/> › crie um projeto (ex.: "publibot").
2. **APIs e serviços › Biblioteca** › "YouTube Data API v3" › **Ativar**.
3. **APIs e serviços › Credenciais › Criar credenciais › Chave de API**.
4. Em **Restringir chave**: restrição de API = só "YouTube Data API v3";
   restrição de aplicativo = endereço IP do servidor.

Cota gratuita: 10 000 unidades/dia por projeto. Uma busca de vídeos gasta
100; uma página de comentários gasta 1. A intensidade "intenso" usa algumas
centenas por rodada.

**Testar:**

```bash
curl -s 'https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=1&q=bdi+obra&key=SUA_CHAVE' | head -c 400
```

**No publi-bot:** Radar › Contas externas › chave do YouTube; marque
"comentários do YouTube" na configuração.

---

## 4. Search Console — o retorno do que foi publicado (gratuito)

Uma conta de serviço serve para todos os sites da instalação; cada cliente só
convida o e-mail dela.

1. No mesmo projeto do Google Cloud: **Biblioteca** › "Google Search Console
   API" › **Ativar**.
2. **IAM e administrador › Contas de serviço › Criar conta de serviço**. Nome
   livre; não precisa de papel nenhum.
3. Na conta criada: **Chaves › Adicionar chave › JSON**. Baixa um arquivo.
   (Em organização do Google Workspace, a política
   `iam.disableServiceAccountKeyCreation` pode bloquear isso; em conta pessoal
   não bloqueia.)
4. No servidor:

   ```bash
   # da sua maquina, uma vez (fora da pasta do codigo, que o deploy reescreve):
   ssh ubuntu@servidor 'mkdir -p ~/publibot-config && chmod 700 ~/publibot-config'
   scp chave.json ubuntu@servidor:publibot-config/gsc-conta-de-servico.json
   ssh ubuntu@servidor 'chmod 600 ~/publibot-config/gsc-conta-de-servico.json'
   ```

   e no `.env` (o secret `PRODUCTION_ENV_FILE`):
   `GSC_CONTA_DE_SERVICO_ARQUIVO=/home/ubuntu/publibot-config/gsc-conta-de-servico.json`.
   Vale na proxima implantacao.
5. Em cada site: <https://search.google.com/search-console> › a propriedade ›
   **Configurações › Usuários e permissões › Adicionar usuário** › o
   `client_email` do JSON, permissão **Restrita**. A tela do Radar mostra
   esse e-mail.
6. Na tela do Radar, a propriedade: `sc-domain:exemplo.com.br` (propriedade
   de domínio) ou `https://exemplo.com.br/` (prefixo de URL) — igual ao que
   aparece no Search Console.

**Testar:** Radar › "Coletar agora" no bloco do Search Console. Sem o convite,
a mensagem diz qual e-mail adicionar.

**Indexação dos artigos.** Com a mesma conta (permissão Restrita basta), o
PubliBot pergunta ao Google, pela URL Inspection API, se cada artigo no ar
entrou no índice: 1 dia depois de publicar, todo dia no primeiro mês enquanto
não entrar, depois por semana; o que já entrou, uma vez por mês. A página do
artigo mostra o estado, o motivo ("Detectada, mas não indexada"...), se a URL
está num sitemap enviado e o botão para a inspeção no Search Console.

Pedir a indexação continua sendo um clique ali ("Solicitar indexação"): o
Google não tem API para isso em artigo (a Indexing API é só para vaga de
emprego e transmissão ao vivo, e o ping do sitemap foi desligado em 2023). O
que acelera de verdade é o sitemap do site com `lastmod` real, apontado no
`robots.txt` (`Sitemap: https://.../sitemap.xml`; ver o `IMPLANTACAO.md` da
biblioteca), e os links internos. Enviar o sitemap uma vez em **Search Console
› Sitemaps** é recomendado para ver status e erros; em propriedade de domínio,
digite o endereço completo, com `https://`.

---

## 5. Legendas do YouTube (sem conta)

A biblioteca `youtube-transcript-api` lê a legenda que o YouTube mostra no
player. Não é API oficial: o YouTube costuma recusar pedidos vindos de IP de
datacenter. Quando recusa, o vídeo aprovado fica "esperando áudio" e a pessoa
envia o áudio (item 6). Nada a configurar.

## 6. OpenAlex e Unpaywall — artigos científicos (gratuito)

OpenAlex é uma base aberta com centenas de milhões de trabalhos científicos:
título, autores, resumo, citações e o link do PDF quando o acesso é aberto.
Unpaywall acha o PDF legal pelo DOI. Os dois são da mesma organização
(OurResearch).

1. Crie a conta em [openalex.org](https://openalex.org) e copie a chave de
   API (gratuita). Sem ela, a cota diária é mínima.
2. **No publi-bot:** Radar › Contas externas › chave do OpenAlex e o
   **e-mail para as bases acadêmicas** (os dois serviços pedem um e-mail de
   contato; sem ele, o Unpaywall fica de fora).
3. Configuração do radar › "buscar artigos científicos" (vem ligado).

O que acontece:

- pauta sem fonte: busca artigos no OpenAlex, além da busca na web;
- rodada do radar: artigos para os temas que o acervo ainda não cobre, até o
  limite "artigos científicos por rodada";
- o bloco "Google Acadêmico" que o Google mostra em algumas buscas vem nas
  páginas de resultado que o radar já paga à DataForSEO; cada artigo dele é
  completado pelo OpenAlex (DOI, resumo, PDF), sem chamada paga a mais;
- aprovar um artigo baixa o PDF de acesso aberto. Sem PDF livre, ou com o
  site recusando o download, ele fica em **Artigos aguardando o PDF**: baixe
  pelo navegador (ou pela biblioteca da sua instituição) e envie.

**Testar:** `manage.py conferir_instalacao`, linha "OpenAlex".

## 7. Transcrição de áudio no worker-gpu

Rota `/v1/audio/transcriptions` no worker — especificação em
[`WORKER_TRANSCRICAO.md`](WORKER_TRANSCRICAO.md). Usa o mesmo
`WORKER_SHARED_SECRET` das outras rotas.

## 8. Redes sociais — LinkedIn, Instagram e Google (gratuito)

Sem estas chaves, as redes funcionam no **copiar e colar** (o post fica pronto,
com o link rastreado). Com elas, cada conta conecta pela tela e o post sai
sozinho no horário. Um app por rede, criado **uma vez** pelo dono do PubliBot,
vale para todos os clientes. Passo a passo e permissões em
[`REDES_SOCIAIS.md`](REDES_SOCIAIS.md#conectar-as-apis-o-que-o-dono-precisa-fazer).

```
SOCIAL_LINKEDIN_CLIENT_ID=      SOCIAL_LINKEDIN_CLIENT_SECRET=
SOCIAL_META_APP_ID=             SOCIAL_META_APP_SECRET=
SOCIAL_GOOGLE_CLIENT_ID=        SOCIAL_GOOGLE_CLIENT_SECRET=
```

### O que preencher em cada app (as páginas já existem no PubliBot)

Troque `<raiz>` pelo `ROOT_DOMAIN` do `.env` (o domínio do PubliBot, sem
subdomínio de cliente). Antes, preencha no `.env` `OPERADOR_NOME` (seu nome ou
empresa), `OPERADOR_DOCUMENTO` (CPF/CNPJ, opcional) e `PRIVACIDADE_EMAIL`:
aparecem nas páginas.

| Campo | Valor |
|---|---|
| Domínio do app | `<raiz>` |
| Site / página inicial | `https://<raiz>/` |
| Política de privacidade | `https://<raiz>/privacidade/` |
| Termos de serviço | `https://<raiz>/termos/` |
| Exclusão de dados (instruções) | `https://<raiz>/exclusao-de-dados/` |
| **Endereço de retorno (redirect / callback OAuth)** — um só, para todos os clientes | `https://<raiz>/redes/retorno/` |

**Meta (developers.facebook.com)**

- Tipo do app: *Empresa*. Categoria: *Negócios e páginas*.
- Configurações > Básico: domínio do app, política, termos (tabela acima),
  ícone 1024×1024 e e-mail de contato.
- *Exclusão de dados do usuário*: escolha **URL de retorno de chamada de
  exclusão de dados** e use `https://<raiz>/exclusao-de-dados/meta/`. O
  PubliBot confere a assinatura do pedido, apaga o que veio da conta da
  pessoa em todos os clientes e devolve à Meta o código de confirmação e a
  página de acompanhamento. (A opção "URL de instruções" também serve:
  `https://<raiz>/exclusao-de-dados/`, mas aí o pedido é manual.)
- Login do Facebook > Configurações: em *URIs de redirecionamento do OAuth
  válidos*, `https://<raiz>/redes/retorno/`; em *URL de retorno de chamada
  de cancelamento de autorização*, `https://<raiz>/desautorizar/meta/`.
  Clique em *Salvar alterações* antes de usar o "Verificar URI".

**App do tipo Empresa → "Login do Facebook para Empresas"** (o que a Meta
oferece hoje para app de empresa):

1. *Início rápido* > Web > *Site URL*: `https://<raiz>/` > Save. Os passos
   seguintes do início rápido (SDK de JavaScript, botão) **não se aplicam**:
   o PubliBot conecta pelo servidor. Pode fechar.
2. *Configurações* (a primeira, das definições): ligue *Login do OAuth do
   cliente* e *Login do OAuth na Web*, ponha a URI de retorno acima e salve.
3. *Configurações* (a segunda, as "configurações de login") > *Criar
   configuração*:
   - nome: `PubliBot - Instagram`;
   - tipo de token: **Token de acesso do usuário**;
   - ativos: **Páginas** e **Contas do Instagram**;
   - permissões: `instagram_basic`, `instagram_content_publish`,
     `instagram_manage_comments`, `instagram_manage_insights`,
     `pages_show_list`, `pages_read_engagement`, `business_management`.
   Copie o **ID da configuração** para `SOCIAL_META_CONFIG_ID` no `.env`.
4. Opcional (gasto com anúncios): outra configuração igual, mais o ativo
   **Contas de anúncios** e a permissão `ads_read`; o ID vai em
   `SOCIAL_META_CONFIG_ID_ANUNCIOS`.

Para publicar o app (contas de clientes): [ANALISE_DA_META.md](ANALISE_DA_META.md)
tem os textos de cada permissao, o tratamento de dados e as instrucoes para os
analistas.

Com o `SOCIAL_META_CONFIG_ID` preenchido, o PubliBot pede o login por essa
configuração; vazio, usa o Login do Facebook clássico (lista de permissões).

**LinkedIn (linkedin.com/developers)** — dois apps, porque o LinkedIn exige
que a *Community Management API* (páginas de empresa) fique sozinha num app:

1. **App do perfil pessoal** (o essencial): produtos *Sign In with LinkedIn
   using OpenID Connect* e *Share on LinkedIn* (liberados na hora). Chaves em
   `SOCIAL_LINKEDIN_CLIENT_ID` / `SOCIAL_LINKEDIN_CLIENT_SECRET`.
2. **App da página de empresa** (opcional, com revisão do LinkedIn): só o
   produto *Community Management API*. Chaves em
   `SOCIAL_LINKEDIN_PAGINA_CLIENT_ID` / `SOCIAL_LINKEDIN_PAGINA_CLIENT_SECRET`.

Nos dois: ligados a uma página de empresa sua no LinkedIn, logo, a política
de privacidade (tabela acima) e, em Auth > *Authorized redirect URLs for your
app*, `https://<raiz>/redes/retorno/` (exato, com a barra final).

**Google (console.cloud.google.com)**

- Tela de consentimento OAuth: página inicial, política, termos; em
  *Domínios autorizados*, a raiz do seu domínio (o Google pede que ela esteja
  verificada no Search Console — o mesmo onde está o site).
- Credenciais > ID do cliente OAuth (Aplicativo da Web) > *URIs de
  redirecionamento autorizados*: `https://<raiz>/redes/retorno/`.
- Escopo: *Google Auth Platform* > **Acesso a dados** > *Adicionar ou remover
  escopos* > *Adicionar escopos manualmente*:
  `https://www.googleapis.com/auth/business.manage`. Enquanto o app estiver
  em teste, ponha seu e-mail em *Usuários de teste*.
- APIs (Biblioteca): My Business Account Management, My Business Business
  Information e Google My Business.
- **Pedido de acesso à API do Perfil da Empresa** (formulário "Applying for
  Google Business Profile API access"): sem ele a cota é zero. Escolha um
  perfil **verificado há mais de 60 dias** e informe o **site que está nesse
  perfil** (não o endereço do PubliBot); o e-mail considerado é o da conta
  Google logada (de preferência no domínio do site). Pedido certo abre um
  chamado de suporte, com análise em ~7 a 10 dias úteis; pedido fora dos
  critérios é recusado na hora. Até sair, a conta do Google fica cadastrada
  sem conectar: o post aprovado aparece para copiar e colar.

Por que um endereço só: as redes só devolvem a pessoa para endereços
cadastrados exatamente. O PubliBot recebe no domínio raiz e segue para o
cliente certo (o pedido de conexão leva o cliente, assinado), sem você
cadastrar nada a cada cliente novo.

**Gasto com anúncios (opcional).** Para o PubliBot ler sozinho quanto cada
cliente gastou em cada anúncio (Meta Ads), o mesmo app da Meta precisa do
produto **Marketing API** e da permissão **`ads_read`** aprovada na revisão
do app (App Review > Permissões e recursos > `ads_read` > pedir acesso
avançado, explicando: "ler o gasto e o resultado dos anúncios das contas dos
clientes para relatório"). Enquanto não sai — ou se um dia a Meta retirar —,
cada cliente manda a planilha exportada do Gerenciador (aba Diagnóstico de
Redes), e o resto funciona igual. Sem variável nova no `.env`.

---

## Análise de concorrentes

Na configuração do Radar, um concorrente por linha:

```
concorrente.com.br | Nome do Negócio no Google Cidade
outro.com.br
```

| Opção | Custo | O que faz |
|---|---|---|
| O que os concorrentes publicam | gratuito | lê o sitemap público; cada página vira um tema (pelo endereço, sem baixar a página) |
| Buscas em que os concorrentes aparecem | DataForSEO Labs | palavras em que o domínio está entre os 20 primeiros, com volume |
| Avaliações no Google | DataForSEO (fila) | reclamações (nota até 3) e perguntas; precisa do nome depois do `|` |

A lacuna aparece sozinha: temas que o site já cobriu perdem nota pela
canibalização; o que sobra é o que o concorrente tem e o site não.
