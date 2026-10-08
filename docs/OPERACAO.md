# Subir o PubliBot: desenvolvimento e servidor

Dois ambientes, o MESMO Ollama. Em desenvolvimento ele roda na sua maquina; em
producao, na mesma maquina de sempre, alcancada por Tailscale. Muda uma URL.

Depois de atualizar, o roteiro para conferir cada funcao na tela esta em
[`CONFERENCIA.md`](CONFERENCIA.md).

---

## Parte 1 — Desenvolvimento

Pre-requisitos: Python 3.12+, PostgreSQL com pgvector, Redis, Ollama.

```bash
sudo apt install python3-venv postgresql postgresql-contrib \
                 postgresql-16-pgvector redis-server
```

### 1. Ambiente e configuracao

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

cp .env.example .env
```

Tres campos do `.env` precisam de valor seu. Os demais ja vem com default de
desenvolvimento:

```bash
# Gere cada um e cole no .env
python -c "import secrets; print(secrets.token_urlsafe(64))"                      # DJANGO_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # NODE_KEY_ENCRYPTION_KEY
```

E `POSTGRES_PASSWORD`, com a senha que voce vai dar ao papel do banco no passo
seguinte.

> **`NODE_KEY_ENCRYPTION_KEY` nao e descartavel.** Ela cifra as credenciais dos
> sites dos clientes guardadas no banco. Troca-la torna todas irrecuperaveis.
> Em desenvolvimento isso e so um aborrecimento; anote mesmo assim, para nao
> criar o habito.

### 2. Banco

```bash
./scripts/setup-db.sh
```

Cria o papel, o banco e — o que mais importa — instala `vector` e `unaccent`
num schema `extensions`, **nao** no `public`. Com um schema por tenant, uma
extensao so no `public` nao fica alcancavel ao criar o SEGUNDO tenant, e a
migration falha com `type "vector" does not exist`. O script tambem prepara o
`template1`, de onde o banco de teste do pytest herda.

### 3. Migrations e tenant raiz

```bash
python manage.py migrate_schemas --shared
python manage.py bootstrap_public
python manage.py createsuperuser
```

`bootstrap_public` nao e opcional: `migrate_schemas` cria as TABELAS do schema
`public`, mas nao a LINHA que o django-tenants consulta para resolver um host.
Sem ela a primeira requisicao devolve `No tenant for hostname`.

### 4. O worker de GPU

Tudo que precisa de placa — texto, imagem de capa e conversao de PDF — roda
num servico so, o **worker-gpu**, que vive em **outro repositorio**. Ele e um
arbitro: a placa e indivisivel, e um modelo de texto grande ja ocupa quase
toda a VRAM.

A instalacao esta no README dele. O resumo:

```bash
git clone <repo-do-worker> ~/codes/worker-gpu
cd ~/codes/worker-gpu
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env               # WORKER_SHARED_SECRET e BIND_HOST
./venv/bin/python baixar_modelo.py # os ~7 GB do modelo de imagem, uma vez
./deploy/instalar.sh
```

O Ollama passa a ser **interno**, em loopback: quem publica na rede e o
worker. Falar com o Ollama direto contorna o arbitro.

De volta aqui, no `.env`:

```
INFERENCIA_BASE_URL=http://127.0.0.1:8090
INFERENCIA_MODELO=<o nome exato do `ollama list`>
INFERENCIA_API_KEY=<o WORKER_SHARED_SECRET do worker>

CONVERSAO_BASE_URL=http://127.0.0.1:8090
CONVERSAO_SEGREDO=<o mesmo>

IMAGEM_BASE_URL=http://127.0.0.1:8090
IMAGEM_SEGREDO=<o mesmo>

# So para o `manage.py dev` subir o worker junto
WORKER_GPU_DIR=~/codes/worker-gpu
```

E, uma vez:

```bash
python manage.py configurar_inferencia --testar
python manage.py configurar_conversao --testar
python manage.py configurar_imagem --testar
```

E, para saber se funciona de verdade:

```bash
python manage.py conferir_worker
```

**Conferir:** os `--testar` conferem o ENDERECO; o `conferir_worker` confere o
CAMINHO. A diferenca importa: os tres `--testar` chamam `/health/` e
`/v1/models`, que nao tocam a placa, e passam com o modelo ausente no disco,
com os pesos da difusao faltando ou com o Docling explodindo na primeira
pagina. O `conferir_worker` gera texto, gera uma imagem e converte um PDF,
pelos mesmos adaptadores que os fluxos usam, e sai com codigo 1 se algum
falhar.

Leia a resposta dos `--testar` mesmo assim: `dispositivo=cpu` onde voce
esperava `cuda` nao e erro — e o trabalho levando minutos em vez de segundos,
em silencio.

#### As tres coisas que este projeto precisa saber

**As tres URLs sao a mesma.** Continuam separadas porque uma delas pode virar
um provedor pago, e porque a reserva por maquina
(`apps/inference/leases.py`) usa o host de cada uma para saber que elas
dividem hardware.

**O 503 nao e erro.** O worker recusa quando a placa esta ocupada, com
`Retry-After`. Deste lado vira `PassoAdiado`, que nao gasta tentativa. Se
voce vir trabalhos esgotando tentativas por 503, o defeito esta no
tratamento.

**Sem worker, o sistema nao para — e esse e o risco.** Sem conversao, o PDF
cai no extrator local, que devolve a camada de texto sem interpretar a
pagina: num artigo de coluna dupla as colunas se intercalam e o texto
continua parecendo correto. Por isso `PERMITIR_EXTRACAO_LOCAL` vem desligado
em producao. Sem geracao de imagem, o artigo sai igual, apenas sem capa — e
isso e legitimo.

### 5. Rodar

Um comando:

```bash
python manage.py dev
```

Ele sobe os tres processos do sistema — web, worker e beat — no mesmo
terminal, e o Ctrl+C encerra todos. Antes de subir, confere o banco: sem a
extensao `vector` o servidor subiria normalmente, o cadastro seria aceito, e a
falha so apareceria dentro de uma task, como um traceback de `CREATE TABLE`
que nao menciona extensao nenhuma.

Por que um comando e nao tres terminais: a falta de qualquer um dos dois
processos de fundo e **silenciosa**. Sem worker, o cadastro de um tenant nao
termina e tambem nao falha — a mensagem e publicada, fica na fila, e a tela
espera para sempre. Sem beat, tudo responde e simplesmente nada acontece
sozinho: conteudo aprovado nunca e publicado, trabalho parado nunca e
retomado, reserva vencida nunca e solta. Nenhum erro em lugar nenhum, nos dois
casos.

Quando quiser isolar um deles:

```bash
python manage.py dev --sem-worker    # as tarefas ficam na fila, sem executar
python manage.py dev --sem-beat      # nada roda por horario
python manage.py dev 127.0.0.1:8001  # outra porta (ajuste DEV_SERVER_PORT)
```

Um processo novo amanha entra em `_servicos()`, dentro do proprio comando, e
`manage.py dev` continua sendo o unico que voce precisa saber. No servidor a
lista equivalente sao as units de `deploy/systemd/` — a correspondencia e um
para um, de proposito.

Abra `http://publibot.localhost:8000`. Use `publibot.localhost`, nao
`localhost`: com um unico rotulo o navegador DESCARTA o atributo `Domain` do
cookie, o login nao atravessa para o subdominio do tenant, e a tela de login
reaparece sem explicacao.

**Conferir:**

```bash
python manage.py check_db        # banco, extensoes, schemas, tenant raiz
python manage.py broker_status   # qual broker esta valendo, e se responde
curl -sI http://publibot.localhost:8000/healthz/ | head -1
```

#### O `dev` e as units do systemd na mesma maquina

Se voce instalou os servicos de GPU como unit (Parte 3) na maquina em que
tambem desenvolve, os dois convivem — e a regra e uma so: **o `dev` nao sobe o
que ja esta de pe.** Ele testa a porta antes e usa o servico existente, porque
subir um segundo daria "address already in use" e derrubaria o `dev` inteiro,
ja que qualquer processo que morre encerra todos.

O banner diz qual dos dois casos e o seu:

```
GPU:      ja de pe em http://127.0.0.1:8090 (servico proprio); nao subi outro.
```

Isso significa que os pedidos estao indo para as units do systemd, e nao para
processos filhos do `dev`. A consequencia pratica que mais confunde: **o log
deles nao aparece no terminal do `dev`**. Ele esta no journal.

```bash
journalctl --user -u worker-gpu -f
```

Se o banner disser outra coisa — "nenhuma", "em outra maquina", "worker-gpu/
venv nao existe" —, ai sim o `dev` esta usando (ou deixando de usar) algo
proprio, e a frase diz qual.

### 6. Primeiro tenant

```bash
python manage.py provision_tenant acme --name="ACME Ltda"
```

Depois entre em `http://acme.publibot.localhost:8000`.

### 7. Roteiro de aceitacao

O mesmo da Parte 2, passo 8 — vale igual aqui. E o unico teste que exercita a
cadeia inteira: enviar PDF, conferir que a conversao veio com analise de
layout, vetorizar blocos, criar pauta, gerar artigo, acompanhar em Operacao,
abrir o artigo com capa, aprovar.

Duas diferencas em desenvolvimento:

- o `PERMITIR_EXTRACAO_LOCAL=True` do `.env.example` deixa o PDF passar pelo
  extrator local quando nao ha Docling. Em producao ele e recusado. Se voce
  quer testar o caminho de producao, desligue-o aqui tambem;
- sem `IMAGEM_BASE_URL`, o artigo sai sem capa e o passo registra o motivo.
  Nao e falha: o texto e o produto.

---

## Parte 2 — Servidor

Cada passo termina com **como conferir**. A ordem importa e nao e arbitraria:
um passo que falha em silencio so aparece dois passos depois, com um erro que
nao menciona a causa — e esta secao esta escrita para isso nao acontecer.

Se algum comando de conferencia nao devolver o que esta escrito, pare ali. O
proximo passo vai funcionar mesmo assim e o problema vai reaparecer mais
tarde, disfarcado.

### Como funciona

A implantacao e o push na `main`. O job `deploy` do
`.github/workflows/ci.yml` (so depois da suite passar):

1. monta o `.env` a partir do secret `PRODUCTION_ENV_FILE`;
2. copia o codigo e o `.env` para `/home/ubuntu/publi-bot-envio`;
3. roda `deploy/scripts/release.sh` de la.

O `release.sh` e idempotente: na primeira vez prepara o servidor; nas
seguintes so confere o que ja existe. Em ordem:

| Passo | O que faz |
|---|---|
| Pacotes | Python 3.12 (PPA deadsnakes no Ubuntu 22.04), build-essential, rsync, gettext, cliente do PostgreSQL; Redis e Nginx so se nada atender |
| Banco | cria o banco se faltar, instala `postgresql-<versao>-pgvector` se faltar, e poe `vector` e `unaccent` no schema `extensions` |
| Dependencias | venv em Python 3.12; `pip install` so quando o `requirements.txt` muda. **Antes** de trocar o codigo: se nao instalar, a versao anterior segue no ar |
| Codigo | `rsync --delete` da pasta de envio para `/home/ubuntu/publi-bot` (preserva venv, `.env`, estaticos e o modelo baixado) |
| Django | `check --deploy`, migration faltando, `migrate_schemas`, tenant raiz, conexoes, prompts, `collectstatic` |
| Modelo | baixa o modelo de embedding (~2 GB) agora, e nao na primeira busca |
| Servicos | sincroniza as units do systemd, instala o site do Nginx (se o certificado ja existir, e so depois de `nginx -t`), recarrega e confere o `/healthz/` |

Nada de afrouxar o `requirements.txt` para caber num Python mais velho: o
servidor rodaria versoes que a suite nunca testou. Por isso o script instala o
3.12, o mesmo do CI.

As extensoes precisam estar em `extensions`, **nao** em `public`. Com um
schema por tenant, uma extensao so no `public` funciona para o primeiro tenant
e falha no segundo, com `type "vector" does not exist`.

### O que precisa existir antes

| | Por que |
|---|---|
| Ubuntu com o usuario `ubuntu` e `sudo` sem senha | o `release.sh` instala pacotes e mexe nas units |
| PostgreSQL alcancavel em `127.0.0.1` | usuario e senha vao no `.env` |
| DNS: `publibot.ekron.ia.br` e `*.publibot.ekron.ia.br` apontando para o servidor | cada tenant vive num subdominio (ADR-0003) |
| Uma maquina com placa, alcancavel por Tailscale | o Ollama, o Docling e a geracao de imagem rodam la (ADR-0007) |

A VM da nuvem nunca roda inferencia. Ela serve as telas, guarda o banco e faz
requisicoes HTTP para a maquina da placa.

### Passo 1 — Segredos do GitHub

Em Settings > Secrets and variables > Actions:

| Segredo | O que e |
|---|---|
| `SERVER_HOST` | endereco do servidor |
| `SERVER_USER` | `ubuntu` |
| `DEPLOY_KEY` | chave ssh **privada** desse usuario |
| `SERVER_PORT` | porta do ssh |
| `PRODUCTION_ENV_FILE` | o conteudo inteiro do `.env` de producao |

O `PRODUCTION_ENV_FILE` e reescrito no servidor a cada implantacao, sempre com
o mesmo texto. **Nunca troque a `NODE_KEY_ENCRYPTION_KEY` dele**: ela cifra as
credenciais guardadas no banco (chaves de API, senha dos sites), e com outra
chave todas ficam irrecuperaveis. Guarde uma copia do `.env` fora do GitHub.

O modelo das variaveis e o `.env.example`; as de producao que mudam em relacao
a ele: `DJANGO_SETTINGS_MODULE=core.settings.prod`, `ESQUEMA_PUBLICO=https`,
`USAR_X_ACCEL=true`, `MEDIA_ROOT=/home/ubuntu/storage/publi-bot` (e o caminho
que as units liberam para escrita). `EMAIL_HOST` e opcional: sem ele, o que o
Django tentar enviar sai no log.

**Conferir:** faca um push na `main` e acompanhe o job *Implantar* no GitHub.
A primeira vez demora (pacotes, dependencias, modelo de 2 GB).

### Passo 2 — Certificado e Nginx

O certificado precisa cobrir os subdominios dos tenants. Duas saidas:

- **Curinga** (`*.publibot.ekron.ia.br`): exige validacao por DNS. Se o seu
  DNS tem plugin do certbot (Cloudflare, Route 53...), a renovacao e
  automatica; com `--manual`, voce refaz a cada 90 dias.

  ```bash
  sudo certbot certonly --manual --preferred-challenges dns \
       -d publibot.ekron.ia.br -d '*.publibot.ekron.ia.br'
  ```

- **Um nome por tenant**, por HTTP (renova sozinho). Acrescente o subdominio a
  cada tenant novo:

  ```bash
  sudo certbot certonly --webroot -w /var/www/certbot --cert-name publibot.ekron.ia.br \
       -d publibot.ekron.ia.br -d publiteste.publibot.ekron.ia.br
  ```

  Na primeira vez, sem o site do PubliBot no Nginx, use `--nginx` ou
  `--standalone` no lugar de `--webroot`.

**O dominio raiz tambem precisa de certificado**: `*.publibot.ekron.ia.br` nao
cobre `publibot.ekron.ia.br` (e la ficam o login, a privacidade e o retorno
das conexoes com as redes). Ou inclua os dois nomes no certificado (o comando
acima), ou use um que ja cubra a raiz, como o curinga do dominio pai
(`*.ekron.ia.br`): o `release.sh` procura sozinho em `/etc/letsencrypt/live`
o certificado que cobre a raiz e usa esse so nela (`CERTIFICADO_DA_RAIZ` no
`.env` fixa a pasta, se precisar). Sem nenhum, o release avisa.

O nome da pasta do certificado precisa ser o `ROOT_DOMAIN`
(`/etc/letsencrypt/live/publibot.ekron.ia.br/`). Com ele la, o proximo
`release.sh` instala o site do Nginx sozinho (`deploy/nginx/publibot.conf`,
com dominio e caminhos trocados), confere com `nginx -t` e recarrega. Se o
`nginx -t` falhar, a configuracao anterior volta: o Nginx atende outros
projetos.

**Conferir:**

```bash
curl -sI https://publibot.ekron.ia.br/healthz/ | head -1
curl -sI https://publiteste.publibot.ekron.ia.br/ | head -1
```

O `/healthz/` responde **antes** da resolucao de tenant, de proposito.

O limite de envio e 50 MB (PDFs grandes), com uma excecao: `/redes/novo/` e
`/redes/fotos/` aceitam ate 250 MB (fotos em lote e video do celular). Esta no
mesmo `publibot.conf`; o `release.sh` aplica.

### Passo 3 — Conferir o que subiu

```bash
cd ~/publi-bot
systemctl is-active publibot celery-publibot celery-beat-publibot
venv/bin/python manage.py check_db
venv/bin/python manage.py conferir_instalacao
```

O `check_db` confere banco, extensoes, `search_path` e o tenant raiz. O
`conferir_instalacao` confere fila, worker, beat, midia e as contas externas
(`--completo` faz tambem as chamadas de uso real; ver `docs/CONFERENCIA.md`).

Os arquivos dos tenants ficam em `MEDIA_ROOT`, cada tenant numa subpasta com
o nome do schema. Para mudar a pasta, mude nos tres lugares que precisam
concordar: `MEDIA_ROOT` no `.env`, `ReadWritePaths` das units
(`deploy/systemd/`) e o `backup.sh` (que ja le o `MEDIA_ROOT` sozinho). O
`alias` do Nginx o `release.sh` monta a partir do `MEDIA_ROOT`.

### Passo 6 — Conexoes de inferencia

```bash
venv/bin/python manage.py configurar_inferencia --testar
venv/bin/python manage.py configurar_conversao --testar
venv/bin/python manage.py configurar_imagem --testar
```

Os tres sao idempotentes e **preservam** o que ja estiver no banco: a conexao
vive numa linha, e nao num arquivo, para trocar de modelo sem implantar
(ADR-0012).

Depois dos tres, a conferencia de verdade:

```bash
venv/bin/python manage.py conferir_worker
```

Ele chama as tres rotas para valer — uma geracao de texto curta, uma imagem
512x288, um PDF de uma pagina — pelos mesmos adaptadores dos fluxos. Leva
menos de um minuto numa maquina com placa (varios, na primeira vez, se o
Docling ainda for carregar os modelos dele). Sai com codigo 1 se algo falhar,
entao pode entrar no script de implantacao.

**Conferir:** os `--testar` conferem so o endereco. Leia a resposta deles
tambem, nao so o codigo de saida:

- `configurar_conversao` deve dizer `dispositivo=cuda`. Se disser `cpu`, o
  worker esta sem placa e a conversao vai levar minutos;
- `configurar_imagem` deve dizer `dispositivo=cuda` e `baixado=True`. Com
  `baixado` falso, a primeira geracao de capa baixa alguns GB **dentro da
  requisicao** — rode `./venv/bin/python baixar_modelo.py` na maquina da placa
  antes de usar.

### Passo 6b — Radar, fontes externas e Search Console (opcionais)

Nada disto e obrigatorio para publicar. Sem eles, o radar fica desligado e as
pautas continuam sendo criadas a mao. O passo a passo de cada conta (onde
criar, onde pegar a chave, como testar) esta em
[`CONTAS_EXTERNAS.md`](CONTAS_EXTERNAS.md).

**Fila da DataForSEO.** As rodadas usam a fila padrao (cerca de um terco do
preco ao vivo): a rodada fica "aguardando" e o batimento `colher-fila-do-radar`
do celery beat colhe os resultados a cada 5 minutos. Sem o beat rodando, a
rodada nao termina.

**Outros batimentos do radar.** `atualizar-contexto-dos-sites` (uma vez por
dia) busca a pagina inicial e as publicacoes de cada site, base da sugestao de
sementes e da canibalizacao. `descrever-oportunidades` (de hora em hora) pede
ao modelo a descricao das melhores oportunidades; sem placa no ar, tenta na
hora seguinte. Para usar o modelo de 30B so nessa descricao, escolha-o na
versao do prompt `opportunity_brief` (e em `seed_suggestion`, para as
sementes sugeridas).

**Batimentos diarios de artigo.** `conferir-fontes-vencidas` poe em Radar >
Atualizar artigos todo artigo no ar que cita fonte vencida (validade da
categoria) ou substituida por uma versao nova no acervo.
`coletar-metricas-dos-sites` busca leitura e conversoes nos sites que declaram
o recurso `insights` e alimenta Artigos > Desempenho no site e a parcela de
conversao do radar. O que o site precisa implementar para isso (e para a
chamada da oferta no meio do artigo) esta em
[`contrato/README.md`](contrato/README.md), secoes "Chamada para a oferta do
site" e "Leitura e conversoes"; o pacote `publi-bot-core-django` (sites em
Django) ja traz o script de medicao e as tags de template.

**Negocio primeiro.** Antes da primeira rodada do radar, preencha a tela
**Negocio** (tema, publico, oferta, dores, valores): e a referencia de tudo que
o PubliBot mede, e o que permite comparar o resultado com anuncios em Artigos >
Desempenho no site. Com Search Console e DataForSEO, cada coleta do Search
Console tambem busca o custo por clique das consultas com clique que ainda nao
tem preco (uma chamada, ate 300 palavras, valida por 90 dias).

**Buscador gratuito (SearXNG).** Suba uma instancia propria com a saida JSON
ligada (`search: formats: [html, json]` no `settings.yml` dele) e informe
`SEARXNG_URL` no `.env`. Cada site pode apontar outra na tela do Radar.

**DataForSEO e YouTube** sao cadastrados POR SITE, na tela do Radar > Contas
externas: o custo cai na conta do proprio cliente. `RADAR_TETO_MAXIMO_USD`
limita o teto mensal que um site pode escolher.

**Search Console.** Crie uma conta de servico no Google Cloud, ative a
"Google Search Console API" no projeto, baixe a chave JSON e aponte
`GSC_CONTA_DE_SERVICO_ARQUIVO` para ela (arquivo legivel so pelo usuario do
servico). Cada cliente adiciona o `client_email` dela como usuario da
propriedade, com permissao restrita; a tela do Radar mostra o e-mail e o
passo a passo.

**Transcricao de audio** depende da rota `/v1/audio/transcriptions` no
worker-gpu — especificacao em [`WORKER_TRANSCRICAO.md`](WORKER_TRANSCRICAO.md).
Sem ela, video sem legenda fica esperando e o envio de audio falha com uma
mensagem que aponta para esse arquivo.

**Reordenador da busca** (opcional): `RAG_RERANKER_MODEL=jinaai/jina-reranker-v2-base-multilingual`
baixa ~1,1 GB na primeira busca. Deixe vazio em maquina com pouca memoria.

### Passo 6c — Redes sociais e paginas publicas (opcionais)

Sem nada disto as redes funcionam no "copiar e postar". Para publicar sozinho,
cada rede pede um app criado uma vez (vale para todos os clientes): variaveis
`SOCIAL_*` no `.env` e o passo a passo em
[`CONTAS_EXTERNAS.md`](CONTAS_EXTERNAS.md), secao 8.

- **Paginas que as redes exigem** ja existem no dominio raiz:
  `/privacidade/`, `/termos/` e `/exclusao-de-dados/`, mais o callback de
  exclusao da Meta (`/exclusao-de-dados/meta/`) e o de desautorizacao
  (`/desautorizar/meta/`). Preencha no `.env` quem responde pelos dados:
  `OPERADOR_NOME`, `OPERADOR_DOCUMENTO` (opcional) e `PRIVACIDADE_EMAIL`.
- **Um endereco de retorno so** para cadastrar em todos os apps:
  `https://<ROOT_DOMAIN>/redes/retorno/` (`core/retorno_oauth.py`). O retorno
  segue sozinho para o cliente certo; cliente novo nao exige mexer nos apps.
- **Gasto com anuncios** pela API pede a revisao de `ads_read` na Meta; sem
  ela, cada cliente manda a planilha do Gerenciador (aba Diagnostico).
- **Audio de post proprio** usa a mesma rota de transcricao do worker
  ([`WORKER_TRANSCRICAO.md`](WORKER_TRANSCRICAO.md)); **descrever fotos**
  (opcional) precisa de uma conexao de inferencia com modelo de visao.

**Conferir:** `curl -sI https://<ROOT_DOMAIN>/privacidade/ | head -1` responde
200, e a pagina mostra o nome e o e-mail do `.env`.

### Passo 7 — Primeiro acesso

```bash
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py provision_tenant acme --name="ACME Ltda"
```

**Conferir:** abra `https://acme.exemplo.com.br/`, entre, e veja o painel.

Se o login reaparecer sem erro nenhum, o `ROOT_DOMAIN` tem um rotulo so: o
navegador DESCARTA o atributo `Domain` do cookie e a sessao nao atravessa para
o subdominio.

### Passo 7b — O site do cliente

O site que recebe os artigos implementa o contrato `/api/v1`
([`contrato/README.md`](contrato/README.md)). Se ele e Django, o contrato ja
vem pronto no pacote
[`publi-bot-core-django`](https://github.com/EduardoReolon/publi-bot-core-django):

```bash
pip install "publi-bot-core-django @ git+https://github.com/EduardoReolon/publi-bot-core-django@main"
```

Para o site acompanhar as versoes novas do pacote, o deploy do site deve
reinstala-lo de `@main` a cada vez (e rodar `migrate` e `collectstatic`); ver
"Atualizar" no `docs/IMPLANTACAO.md` de la.

O passo a passo de la (`docs/IMPLANTACAO.md`) cobre settings, rotas, HTTPS e
midia; o `docs/PARA_IA.md` diz as tabelas e tags que o template usa. A chave e
o segredo saem do cadastro em **Site e cadencia**. Para conferir as duas
pontas: `manage.py publibot_conferir` no site e `manage.py conferir_instalacao`
aqui (linha "site").

### Passo 8 — Roteiro de aceitacao

Ate aqui tudo esta de pe. Isto confere que o sistema **funciona**, e e o unico
passo que exercita a cadeia inteira. Vale a pena na primeira implantacao e
depois de qualquer mudanca grande.

| # | Faca | Confere que |
|---|---|---|
| 1 | Envie um PDF em **Documentos > Enviar** | upload, fila e worker |
| 2 | Abra o documento em instantes | a conversao rodou na maquina da placa |
| 3 | Veja se **nao** ha aviso de "texto extraido sem analise de layout" | o Docling atendeu; se houver, caiu no extrator local |
| 4 | Marque alguns blocos e vetorize | o modelo de embedding e o pgvector |
| 5 | Crie uma pauta e clique em **gerar artigo** | prompts semeados e o Ollama |
| 6 | Acompanhe em **Operacao** | o orquestrador avanca passo a passo |
| 7 | Abra o artigo pronto | as citacoes viraram links e a capa veio junto |
| 8 | Aprove e agende | a trava de autor e a cadencia |

Cada linha que falhar tem uma entrada na tabela de **Diagnostico**, no fim
deste arquivo.

Do passo 5 ao 7 o tempo depende da placa, nao da nuvem. Num artigo de seis
secoes com um modelo 7B, conte alguns minutos — e, se a capa entrar junto,
mais um ou dois. **Nao** clique de novo achando que travou: a tela de operacao
mostra o passo em curso.

### Toda implantacao, depois disso

Um push (ou merge) na `main`. Para implantar a mao, sem o GitHub:

```bash
# --exclude .env: o seu .env de desenvolvimento NAO pode ir como o de producao
rsync -a --delete --exclude .git --exclude .env --exclude venv --exclude media \
      --exclude .model_cache ./ ubuntu@servidor:publi-bot-envio/
scp .env.producao ubuntu@servidor:publi-bot-envio/.env   # opcional: sem ele, vale o que ja esta la
ssh ubuntu@servidor 'bash ~/publi-bot-envio/deploy/scripts/release.sh'
```

**Conferir** que a implantacao chegou (e nao so que o workflow ficou verde):

```bash
systemctl is-active publibot celery-publibot celery-beat-publibot
journalctl -u publibot -n 20 --no-pager
```

O job de teste sobe PostgreSQL e Redis de verdade, com a extensao `vector` no
schema `extensions` do `template1`, em Python 3.12 como o servidor. Um banco
falso passaria em tudo e nao diria nada sobre schema por tenant,
`search_path` ou prefixo de chave, que e exatamente o que este projeto tem de
mais fragil.

### Redis compartilhado

O servidor tem um Redis para varios projetos, e isso exige cuidado. O Celery
sem prefixo usa nomes genericos: a fila padrao e a chave `celery`, as mensagens
em voo ficam em `unacked`. **Duas aplicacoes na mesma base leem a MESMA fila** —
e o sintoma nao e um erro: o worker do outro projeto retira uma tarefa desta
aplicacao, nao reconhece o nome, e a descarta. O trabalho some sem rastro, e o
log que explicaria isso esta no servidor do outro projeto.

`REDIS_NAMESPACE=publibot` prefixa todas as chaves. Para conferir:

```bash
redis-cli --scan --pattern 'publibot:*' | head
redis-cli --scan --pattern 'celery*'     # deve vir vazio
```

Separar so por numero de base (`/0`, `/1`) dependeria de ninguem repetir o
numero, e um `FLUSHDB` de um projeto ainda levaria o outro junto.

---

### Servico de vetores (memoria)

O modelo de embedding ocupa cerca de 2 GB **por processo** que o abre. Antes,
cada worker do Gunicorn e cada processo do Celery abria a sua copia: numa VM
pequena isso esgota a memoria e a maquina inteira para (foi o que travou o
servidor em 01/10). Agora um processo so segura o modelo,
`vetores-publibot.service` (`manage.py servir_vetores`, em 127.0.0.1:8601), e
os outros pedem os vetores a ele (`ServicoDeVetoresClient`, padrao em
producao). Os vetores sao os mesmos de antes: o codigo que roda e o mesmo.

    systemctl status vetores-publibot
    curl -s http://127.0.0.1:8601/saude      # {"pronto": true, ...}

Ao subir, ele leva cerca de meio minuto para abrir o modelo. Nesse tempo a
busca responde "servico de vetores carregando" e a indexacao volta para a fila
sozinha. O `release.sh` liga o servico e so o reinicia quando o codigo dele
(`servico_de_vetores.py`, `embeddings.py`) muda.

Se o `.env` tiver `EMBEDDING_CLIENT=...FastEmbedClient`, apague a linha: ela
volta ao modelo aberto em cada processo.

### Trechos-ruido no indice

Legenda de figura (`<!-- image -->`), linha de DOI, letras soltas ("F I G U R E")
nao entram mais no indice (`blocos.e_ruido`). Num trecho assim sobra quase so o
titulo do documento, e por isso ele aparecia no topo de qualquer busca do tema.
Para tirar os que entraram antes (marca como inativos, nao apaga):

    manage.py tenant_command desligar_trechos_ruidosos --schema=ekron --seco
    manage.py tenant_command desligar_trechos_ruidosos --schema=ekron

## Parte 3 — A maquina da placa

Ela roda o **worker-gpu**, que vive em **outro repositorio**. A placa e um
recurso da maquina, compartilhado por mais de um sistema — enquanto o codigo
dela morava aqui dentro, era so questao de tempo ate um segundo consumidor
precisar do mesmo e nao ter como.

O worker e um **arbitro**: um processo, um lock, e tudo passa por ele.

| Rota | O que faz |
|---|---|
| `POST /v1/chat/completions` | texto — repassa ao Ollama |
| `POST /v1/images/generations` | imagem de capa |
| `POST /parse/` | PDF para Markdown com analise de layout |
| `GET /health/` | estado, sem credencial |

O Ollama passa a ser **interno**: so o worker fala com ele, em loopback. Falar
com o Ollama direto contorna o arbitro, e ai a geracao de imagem volta a
encontrar a VRAM cheia — que foi o defeito que motivou tudo isto.

A instalacao, a configuracao e o diagnostico estao no README do worker. Aqui
fica so o que este projeto precisa saber.

### O que o PubliBot configura

As tres URLs apontam para o **mesmo** endereco, e continuam separadas porque
uma delas pode virar um provedor pago e porque a reserva por maquina
(`apps/inference/leases.py`) usa o host de cada uma para saber que dividem
hardware.

```
INFERENCIA_BASE_URL=http://<endereco>:8090
INFERENCIA_API_KEY=<o WORKER_SHARED_SECRET do worker>

CONVERSAO_BASE_URL=http://<endereco>:8090
CONVERSAO_SEGREDO=<o mesmo>

IMAGEM_BASE_URL=http://<endereco>:8090
IMAGEM_SEGREDO=<o mesmo>
```

E, uma vez:

```bash
python manage.py configurar_inferencia --testar
python manage.py configurar_conversao --testar
python manage.py configurar_imagem --testar
```

### Vetorizacao e legendas do YouTube no worker (opcional)

Duas cargas a mais que a conexao do worker pode marcar em **Inferencia**:

| Carga | Rota no worker | O que muda |
|---|---|---|
| Vetorizacao de documentos (`embedding`) | `/v1/embeddings` — [WORKER_VETORIZACAO.md](WORKER_VETORIZACAO.md) | a indexacao sai do servidor (1 CPU) e vai para a placa; a consulta continua no servidor. Liga tambem as **fontes provisorias**: pagina e resumo de artigo entram no indice antes da curadoria, e a curadoria so e pedida quando uma pauta for usa-los — e so se as ja curadas nao bastarem (3+ documentos curados que sustentam a pauta: o artigo sai so com eles), no maximo 3 por vez; na pauta, “Gerar so com as ja curadas” encerra a curadoria dela. |
| Legenda do YouTube (`youtube`) | `/v1/youtube/legenda` — [WORKER_YOUTUBE.md](WORKER_YOUTUBE.md) | a legenda e lida pelo IP de casa, que o YouTube costuma aceitar. |

Para marcar, no `.env` do servidor (o segredo `PRODUCTION_ENV_FILE`), quando o
worker ja tiver as rotas, e implantar:

```
CONVERSAO_CARGAS_EXTRAS=embedding,youtube
```

(ou so uma delas). O `configurar_conversao`, que roda em toda implantacao,
aplica na conexao existente. Sem as marcas, tudo roda no servidor, como antes. Com o worker fora do ar, a
vetorizacao espera na fila; a curadoria oferece "Vetorizar agora no servidor".

Para por no indice, como provisorio, o que ja esperava curadoria:

```bash
python manage.py tenant_command vetorizar_fontes_provisorias --schema=<schema> --seco
python manage.py tenant_command vetorizar_fontes_provisorias --schema=<schema>
# sem worker, ou para nao esperar por ele (lento: usa a CPU do servidor):
python manage.py tenant_command vetorizar_fontes_provisorias --schema=<schema> --local
```

Rode de novo depois que as paginas terminarem de converter (o comando pula o
que ja foi feito).

### Dois fluxos de artigo: A e B

Uma pauta pode ter um artigo de cada fluxo, para comparar; so um vai ao ar
(aprovar um arquiva o outro, como rejeitado e com o motivo). Em Radar >
Configuracao ("Fontes para os artigos") da para desligar um deles. Com os dois
ligados, "Gerar A e B" dispara os dois.

- **A · Fontes curadas**: o artigo usa o acervo (paginas, videos, artigos,
  documentos). Com o worker vetorizando, o que a busca acha entra no indice sem
  curadoria e a geracao para pedindo a curadoria do que vai usar. Artigos do
  OpenAlex vem pela busca semantica (por palavras, se ela falhar).
- **B · Pesquisa cientifica** (`apps/knowledge/pesquisa.py`): 3 paragrafos
  hipoteticos em ingles (mais 2 contrarios quando a ideia pode ser refutada)
  viram busca semantica no OpenAlex; os melhores puxam os relacionados; tudo e
  ordenado por sentido, citacoes e idade e agrupado em angulos (k-means), com o
  contraponto a parte; cada angulo tem uma sintese e os pedidos do texto
  completo. As fontes sao os resumos, curados automaticamente. Sem pesquisa,
  gerar o B pesquisa primeiro e gera sozinho ao terminar.
  O PDF pedido nao substitui o resumo: dele saem so os paragrafos mais perto
  de cada pedido (por vetor), acrescentados ao resumo como "Do texto completo
  (extraido automaticamente)". "Nao achei o PDF" segue com o resumo.

A busca semantica do OpenAlex custa US$ 1 por mil chamadas, com US$ 1 gratis
por dia: uma pesquisa usa 5 ou 6.

### Em desenvolvimento, na mesma maquina

`manage.py dev` sobe o worker junto quando o checkout esta aqui e a porta
esta livre:

```
WORKER_GPU_DIR=~/codes/worker-gpu
```

Se a unit do systemd ja estiver de pe, ele usa a que existe e diz isso no
banner. O log dela esta no journal, **nao** no terminal do `dev`:

```bash
journalctl --user -u worker-gpu -f
```

### O 503 nao e erro

O worker recusa quando a placa esta ocupada, com `Retry-After`. Deste lado
isso vira `PassoAdiado`, que **nao gasta tentativa** — nada deu errado, so
nao era a hora. Se voce vir trabalhos esgotando tentativas por 503, o defeito
esta no tratamento, nao no worker.

## Quando o Ollama cai

Ele vai cair: e a mesma maquina que voce desliga, e o Tailscale as vezes
oscila. O sistema foi construido supondo isso.

**Durante uma inferencia.** O passo levanta `ProviderTransientError`, que vira
`PassoAdiado` — e adiar **nao gasta tentativa**. O trabalho volta para
`WAITING_CAPACITY` com hora marcada, e o varredor do beat o retoma cinco
minutos depois. O contador de tentativas existe para erro de verdade, nao para
espera. (`tests/test_flows.py::test_provedor_fora_do_ar_adia_em_vez_de_falhar`.)

**Depois de cinco falhas seguidas.** O disjuntor abre por 15 minutos e nenhuma
tarefa tenta aquela conexao. Sem ele, uma conexao fora do ar consumiria
tentativa apos tentativa de todos os trabalhos da fila.

**Se o worker morrer no meio.** A reserva tem prazo. `release-expired-leases`
solta as vencidas e `sweep-stalled-jobs` devolve a fila os trabalhos cuja
reserva expirou — e por isso a queda de um worker vira caso comum, nao trabalho
perdido.

**Por que o progresso nao se perde.** O artigo e escrito em rodadas curtas, uma
secao por chamada, e cada rodada e gravada no `GenerationJob`. Desligar a GPU no
meio custa a secao em andamento, nao o artigo. Ao voltar, o trabalho retoma na
secao seguinte.

Depois de reconfigurar uma conexao que passou horas fora, `configurar_inferencia
--atualizar` reabre o disjuntor — senao voce ajustaria tudo, nada aconteceria
por mais 15 minutos, e concluiria que o comando nao funcionou.

---

## Diagnostico

```bash
python manage.py check_db          # banco, extensoes, schemas dos tenants
python manage.py broker_status     # qual broker esta valendo, e se responde
python manage.py conferir_worker   # chama as tres rotas do worker de verdade
python manage.py conferir_worker --rapido   # so o texto, em segundos
python manage.py configurar_inferencia --testar
python manage.py configurar_conversao --testar
python manage.py configurar_imagem --testar
python manage.py reservas          # quem esta segurando a capacidade
```

| Sintoma | Causa provavel |
|---|---|
| `No tenant for hostname` | faltou `bootstrap_public` |
| `type "vector" does not exist` no 2o tenant | extensao no `public`, nao em `extensions` |
| Login reaparece sem erro | `ROOT_DOMAIN` com um rotulo so (`localhost`) |
| POST devolve 400 sem explicacao | porta fora de `DEV_SERVER_PORT` (CSRF compara a origem inteira) |
| Trabalho parado em `WAITING_CAPACITY` | Ollama inacessivel, ou beat nao esta rodando |
| `SemModeloConfigurado` | faltou `configurar_inferencia` |
| `todas as conexoes ... estao ocupadas` | `manage.py reservas` diz quem segura; `--liberar` solta as presas |
| `o disjuntor esta aberto` | 5 falhas seguidas contra o LLM. A propria mensagem traz a ultima causa; conserte e `configurar_inferencia --atualizar` |
| `nenhuma versao ativa para o prompt ...` | tenant sem prompts; `manage.py semear_prompts --todos` |
| `nenhuma conexao de geracao de imagem disponivel` | faltou `configurar_imagem`; o artigo sai sem capa e o texto nao e afetado (secao 4a) |
| Gerar capa fica girando e depois da erro | a primeira geracao BAIXA o modelo (~7 GB). Rode `baixar_modelo.py` na maquina da placa |
| `sem_vram` ao gerar capa | a placa esta ocupada pelo modelo de texto. Ver "Quando a placa nao cabe" (secao 4a) |
| Uma geracao consumindo horas de CPU | `IMAGEM_PERMITIR_CPU=true` sem medir antes |
| `/health/` do worker da `timed out` (nao "refused") | ele esta ocupado gerando. Se persistir sem nada em curso, confira o journal da unit |
| `could not open extension control file` no release | falta `postgresql-<versao>-pgvector` |
| Unit de usuario nao sobe no boot | falta `sudo loginctl enable-linger $USER` |
| Log do worker de GPU nao aparece no `dev` | ele e unit do systemd: `journalctl --user -u worker-gpu -f` |
| `503 gpu_ocupada` num trabalho | funcionando como projetado: o worker arbitra a placa e o trabalho e adiado |
| Gerar capa leva minutos | caiu para CPU. `configurar_imagem --testar` mostra `ultimo=cpu`; a placa esta sendo disputada |
| Aviso de "texto extraido sem analise de layout" | faltou `configurar_conversao` (worker Docling) |
| `ProxyError` no meio da conversao | a rede do worker bloqueia `huggingface.co` |
| `ModuleNotFoundError: No module named 'cv2'` | venv do worker em Python 3.14 sem `opencv-python-headless` (reinstale o requirements) |
| `External data path escapes model directory` | cache do modelo em links; `rm -rf .model_cache` e deixe baixar de novo |
| Tarefas somem sem erro | Redis compartilhado sem `REDIS_NAMESPACE` |
| `relation "content_..." does not exist` a cada minuto | task do beat sem varredura por tenant (ver ARMADILHAS) |

Falhas ja encontradas e o que cada uma significa: [`ARMADILHAS.md`](ARMADILHAS.md).
