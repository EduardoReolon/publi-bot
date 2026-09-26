# Contas externas: o que criar, onde, e como testar

Nada aqui é obrigatório para publicar. Cada item liga uma parte do radar ou da
busca de fontes; sem ele, aquela parte fica desligada e o resto funciona.

| Serviço | Custo | Onde a chave fica | Liga |
|---|---|---|---|
| SearXNG | gratuito (servidor seu) | `.env` (`SEARXNG_URL`) ou tela do Radar | buscador gratuito |
| DataForSEO | pago por chamada | tela do Radar, **por site** | SERP, volume, concorrentes, avaliações |
| YouTube Data API v3 | gratuito até a cota | tela do Radar, **por site** | comentários e vídeos como fonte |
| Search Console | gratuito | `.env` (arquivo da conta de serviço) + convite em cada propriedade | "quase lá" e desempenho dos artigos |
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
documentação, sem acesso à API durante o desenvolvimento): as rotas da fila
(`task_post`/`task_get`), a do Labs (`ranked_keywords/live`, com o filtro por
posição) e a das avaliações (`business_data/google/reviews`). Se alguma
responder erro, o livro-caixa guarda a mensagem da DataForSEO.

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
   sudo install -o publibot -g publibot -m 600 chave.json /etc/publibot/gsc-conta-de-servico.json
   ```

   e no `.env`: `GSC_CONTA_DE_SERVICO_ARQUIVO=/etc/publibot/gsc-conta-de-servico.json`.
   Reinicie web e celery.
5. Em cada site: <https://search.google.com/search-console> › a propriedade ›
   **Configurações › Usuários e permissões › Adicionar usuário** › o
   `client_email` do JSON, permissão **Restrita**. A tela do Radar mostra
   esse e-mail.
6. Na tela do Radar, a propriedade: `sc-domain:exemplo.com.br` (propriedade
   de domínio) ou `https://exemplo.com.br/` (prefixo de URL) — igual ao que
   aparece no Search Console.

**Testar:** Radar › "Coletar agora" no bloco do Search Console. Sem o convite,
a mensagem diz qual e-mail adicionar.

---

## 5. Legendas do YouTube (sem conta)

A biblioteca `youtube-transcript-api` lê a legenda que o YouTube mostra no
player. Não é API oficial: o YouTube costuma recusar pedidos vindos de IP de
datacenter. Quando recusa, o vídeo aprovado fica "esperando áudio" e a pessoa
envia o áudio (item 6). Nada a configurar.

## 6. Transcrição de áudio no worker-gpu

Rota `/v1/audio/transcriptions` no worker — especificação em
[`WORKER_TRANSCRICAO.md`](WORKER_TRANSCRICAO.md). Usa o mesmo
`WORKER_SHARED_SECRET` das outras rotas.

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
