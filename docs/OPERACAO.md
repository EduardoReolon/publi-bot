# Subir o PubliBot: desenvolvimento e servidor

Dois ambientes, o MESMO Ollama. Em desenvolvimento ele roda na sua maquina; em
producao, na mesma maquina de sempre, alcancada por Tailscale. Muda uma URL.

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

### 4. Ollama

```bash
ollama pull qwen2.5:7b-instruct
ollama list                       # confira o nome EXATO
```

Ponha esse nome em `INFERENCIA_MODELO` no `.env` e cadastre a conexao:

```bash
python manage.py configurar_inferencia --testar
```

O `--testar` faz uma requisicao de verdade e distingue os dois motivos de
falha: *nao consegui chegar ate voce* (rede, porta, servico parado) e *cheguei
e voce respondeu outra coisa* (endereco errado). Sem isso, o problema so
apareceria dentro do primeiro job, minutos depois.

O comando e idempotente e **preserva** o que ja estiver no banco — a conexao
vive numa linha, nao num arquivo, justamente para trocar de modelo sem mexer em
codigo. Use `--atualizar` quando quiser sobrescrever com o `.env`.

### 4a. Geracao de imagem de capa (opcional)

Sem isto o artigo sai igual — **sem capa**. O ultimo passo da geracao pede tres
opcoes de imagem; nao havendo conexao, ele registra o motivo e termina, e a
tela de revisao mostra por que veio sem imagem. O texto nao e afetado.

**O Ollama nao serve aqui.** Ele serve modelos de texto e nao tem endpoint de
imagem. O que o PubliBot fala e o dialeto da OpenAI —
`POST /v1/images/generations` com `b64_json` — e ha duas formas de atende-lo:

- **um provedor pago** (OpenAI e compativeis): cadastre a URL e a chave e pule
  o resto desta secao;
- **a sua placa**, pelo servico em `worker-gpu/imagem_api.py`. E o caminho
  descrito abaixo.

#### Subir o servico na maquina com placa

Na mesma pasta e no mesmo venv do Docling:

```bash
cd worker-gpu
./venv/bin/pip install -r requirements.txt   # traz diffusers e accelerate
./deploy/instalar.sh --imagem                # unit de usuario, porta 8101
```

O `.env` do worker (`worker-gpu/.env`) tem um bloco proprio. O que costuma
mudar:

| Variavel | Para que |
|---|---|
| `IMAGEM_MODELO` | qualquer modelo do diffusers. O padrao faz 1024x1024 nativo |
| `IMAGEM_DEVICE` | `auto` usa a placa se houver; `cpu` para so conferir o caminho |
| `IMAGEM_PASSOS` | 25 no SDXL; 1 a 4 nos modelos "turbo" (com `IMAGEM_GUIDANCE=0`) |
| `IMAGEM_OCIOSO_SEGUNDOS` | quanto tempo sem pedido ate devolver a placa |

No `.env` do PubliBot:

```
IMAGEM_BASE_URL=http://127.0.0.1:8101      # ou o endereco Tailscale da maquina
IMAGEM_SEGREDO=<o mesmo WORKER_SHARED_SECRET do worker>
```

E, uma vez:

```bash
python manage.py configurar_imagem --testar
```

O `--testar` bate em `/health/` e diz **em que dispositivo** o modelo rodou por
ultimo. Esse numero e o que importa: um worker que caiu para CPU continua
entregando imagem, so que em minutos — e sem essa linha a conclusao natural
seria "gerar imagem e lento mesmo".

Em desenvolvimento, `manage.py dev` sobe o servico junto com os outros quando
ele mora nesta maquina e a porta esta livre. Se a unit do systemd ja estiver de
pe, ele usa a que ja existe e diz isso no banner.

#### Dividir uma placa de 8 GB com o Ollama e o Docling

Nao ha arranjo perfeito, e o sistema nao finge que ha. Sao cinco camadas:

1. **A reserva conta vagas por MAQUINA**, e nao por conexao
   (`apps/inference/leases.py`). Como as tres conexoes apontam para o mesmo
   host, gerar texto e gerar imagem passam a se revezar. O
   `configurar_imagem` avisa disso no cadastro.
2. **Um pedido por vez dentro de cada servico.** O segundo recebe 503.
3. **Os pesos ficam na RAM, nao na VRAM** (`enable_model_cpu_offload`): o pico
   cai de ~7 GB para algo perto de 3,5 GB.
4. **A placa e devolvida depois de `IMAGEM_OCIOSO_SEGUNDOS` sem pedido.**
5. **Faltando VRAM, o pedido e refeito em CPU** — e o log diz, em WARNING, que
   isso aconteceu. Leva minutos em vez de segundos, mas nao falha.

O que nenhuma delas cobre: o Ollama decide sozinho quando carregar modelo e
nao participa de reserva nenhuma. Quando ele carrega um modelo grande no meio
de uma geracao, a camada 5 e a que entra — e e por isso que ela existe em vez
de o servico simplesmente falhar.

Se `manage.py configurar_imagem --testar` comecar a relatar
`ultimo=cpu`, a placa esta apertada: reduza `IMAGEM_OCIOSO_SEGUNDOS`, gere em
512x512, ou troque para um modelo menor.

### 4b. Conversao de PDF (Docling)

Sem isto o sistema **nao para** — e esse e o problema. Ele cai no extrator
local (`pypdf`), que devolve a camada de texto do arquivo sem interpretar a
pagina. O texto continua parecendo correto.

O que se perde, verificado no mesmo PDF gerado de duas formas:

| | `pypdf` | Docling |
|---|---|---|
| Tabela | vira coluna de numeros soltos: `Braco / n / Ganho / p / 0,8 g/kg / 40 / ...` | tabela em Markdown |
| Cabecalho e rodape da pagina | entram no corpo como se fossem conteudo | separados |
| Secoes | so sobrevivem se o texto as numerar ("1 Introducao") | cabecalhos `#` de verdade |
| Coluna dupla | **depende do arquivo** | sempre em ordem de leitura |

A ultima linha e a perigosa. A ordem que o `pypdf` devolve e a ordem em que o
produtor do PDF escreveu as operacoes de desenho — que num arquivo se encaixa
e no seguinte nao. No mesmo artigo, escrito na outra ordem, as duas colunas se
intercalam frase a frase:

```
E1. A suplementacao proteica em idosos tem sido
D1. Utilizou-se ANOVA de medidas repetidas com
estudada ha decadas.
correcao de Bonferroni.
```

Continua parecendo texto. Vai para o indice, e sai citado num artigo publicado.

**O Docling nao roda nesta maquina nem na VM da nuvem.** Ele roda onde esta a
placa (ADR-0007); a nuvem so faz a requisicao HTTP. Em desenvolvimento, "onde
esta a placa" e a sua propria maquina.

Na maquina do worker, uma vez:

```bash
cd worker-gpu
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # traz torch, ~3 GB
cp .env.example .env                     # defina WORKER_SHARED_SECRET
```

Em desenvolvimento, `BIND_HOST=127.0.0.1` serve. Em producao precisa ser o
endereco Tailscale — nunca `0.0.0.0`.

Suba como servico (o instalador confere tudo antes e mostra o `/health/`):

```bash
./deploy/instalar.sh          # unit de usuario, sem sudo
./deploy/instalar.sh --tudo   # junto com o servico de imagem (secao 4a)
```

Ou, para so experimentar, sem instalar nada:

```bash
WORKER_SHARED_SECRET=... uvicorn docling_api:app --host 127.0.0.1 --port 8100
```

De volta no PubliBot, ponha no `.env`:

```bash
CONVERSAO_BASE_URL=http://127.0.0.1:8100
CONVERSAO_SEGREDO=<o mesmo WORKER_SHARED_SECRET>
```

E cadastre:

```bash
python manage.py configurar_conversao --testar
python manage.py reservas          # quem esta segurando a capacidade
```

Se voce instalou a unit do systemd, o `dev` NAO sobe um segundo worker: ele
detecta a porta ja ocupada e usa o servico que esta de pe, dizendo isso no
banner. Sem essa conferencia, o `uvicorn` falharia com "address already in use"
e derrubaria o `dev` inteiro — porque qualquer processo que morre encerra
todos.

Feito isso, `manage.py dev` passa a subir o worker junto com os outros tres —
mas so quando ele mora AQUI: a URL precisa apontar para `127.0.0.1` e o
`worker-gpu/venv/` precisa existir. Apontando para outra maquina, ele nao sobe
copia nenhuma. Para desligar num dia especifico, `--sem-conversao`.

O `.env` do worker e lido junto, o mesmo arquivo que o systemd usa. Sem isso o
worker do `dev` ignoraria o seu `DOCLING_DEVICE` e se comportaria diferente do
configurado — e a diferenca apareceria so como "aqui esta mais lento".

O `--testar` chama `/health/` e imprime o dispositivo em uso. Isso importa:
trocar `DOCLING_DEVICE` e esquecer de reiniciar o worker nao gera erro nenhum
— so deixa a conversao lenta, e a conclusao natural vira "o Docling e lento
mesmo".

A **primeira** conversao baixa os modelos de layout do HuggingFace (algumas
centenas de MB) e falha com `ProxyError` numa rede que bloqueie
`huggingface.co` — no meio da conversao, nao no boot.

Documentos ja convertidos pelo extrator local continuam como estao. Use
**Converter de novo** na tela de curadoria para refaze-los: os trechos
indexados sao desativados (nao apagados) e os metadados que voce conferiu a
mao sao preservados.

#### Uma placa, dois servicos

O Ollama e o Docling costumam morar na mesma maquina, e o PubliBot trata os
dois como UM recurso: as conexoes que apontam para o mesmo host dividem o mesmo
limite de execucoes simultaneas. Enquanto um converte, o outro espera a vez — e
esperar aqui e adiamento, nao falha: nao gasta tentativa.

Isso nao e conservadorismo. Sem essa conta, uma conversao e uma geracao de
texto simultaneas estouram a VRAM de uma placa de 8 GB, e o processo cai para
CPU **em silencio** — dezenas de vezes mais lento, sem erro em lugar nenhum.

Em CPU o problema nao existe, mas a espera continua: manter a regra vale mais
que a vazao que ela custa num volume baixo.

#### CPU ou placa

O Docling **nao exige GPU**: a analise de layout roda em CPU, e a placa muda o
tempo, nao o resultado. Meca antes de decidir, na maquina que vai hospedar:

```bash
python medir.py um-artigo-de-verdade.pdf --cpu --threads 1   # pior caso
python medir.py um-artigo-de-verdade.pdf --cuda
```

O numero que decide e o de CONVERSAO, nao o de carga — a carga acontece uma
vez por processo. E a pergunta nao e se o tempo e "rapido", e sim se cabe no
seu ritmo de envio de documentos: o worker converte **um por vez**, e o
PubliBot adia o resto em vez de falhar.

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
Conversao:  ja de pe em http://127.0.0.1:8100 (servico proprio); nao subi outro.
Imagem:     ja de pe em http://127.0.0.1:8101 (servico proprio); nao subi outro.
```

Isso significa que os pedidos estao indo para as units do systemd, e nao para
processos filhos do `dev`. A consequencia pratica que mais confunde: **o log
deles nao aparece no terminal do `dev`**. Ele esta no journal.

```bash
journalctl --user -u imagem-api -f
journalctl --user -u docling-api -f
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

### O que precisa existir antes

| | Por que |
|---|---|
| VM Linux com acesso `sudo` | a VM ARM da Oracle serve; ela **nao** roda modelo nenhum |
| Dominio com DNS curinga (`*.exemplo.com.br`) | cada tenant vive num subdominio (ADR-0003) |
| Uma maquina com placa, alcancavel por Tailscale | o Ollama, o Docling e a geracao de imagem rodam la (ADR-0007) |
| Chave ssh para o GitHub Actions | so se voce for implantar pelo push |

A VM da nuvem nunca roda inferencia. Ela serve as telas, guarda o banco e faz
requisicoes HTTP para a maquina da placa. Dimensionar a nuvem para rodar
modelo e o erro caro deste projeto.

### Passo 1 — Pacotes do sistema

```bash
sudo apt update
sudo apt install -y git curl python3-venv \
     postgresql postgresql-contrib redis-server nginx
```

O `vector` **nao** vem com o PostgreSQL: e um pacote a parte, casado com a
versao do servidor.

```bash
psql --version                                  # anote a versao maior (ex.: 16)
sudo apt install -y postgresql-16-pgvector      # troque o 16 pela sua
```

**Conferir:**

```bash
systemctl is-active postgresql redis-server nginx   # active, active, active
redis-cli ping                                      # PONG
sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_available_extensions WHERE name='vector'"   # 1
```

A ultima linha e a que mais se esquece. Sem ela o `bootstrap.sh` vai ate o
meio e morre em `could not open extension control file` — depois de ja ter
criado usuario, venv e segredos. (Hoje ele confere antes e para com a linha do
`apt` certa; a conferencia aqui existe para voce nao descobrir isso pelo
erro.)

### Passo 2 — Codigo e bootstrap

```bash
sudo mkdir -p /srv/publibot && sudo chown "$USER" /srv/publibot
git clone <repo> /srv/publibot && cd /srv/publibot
./deploy/scripts/bootstrap.sh
```

O bootstrap cria o usuario de sistema, os diretorios, o venv, o banco com as
extensoes no schema `extensions`, as units do systemd habilitadas no boot, a
rotacao de log, a regra de sudo da implantacao, e **gera os segredos** em
`/etc/publibot/env`.

**Ele nunca sobrescreve esse arquivo.** Nao e zelo excessivo: regenerar
`NODE_KEY_ENCRYPTION_KEY` por cima torna irrecuperaveis todas as credenciais
de site ja guardadas, e o erro so apareceria na proxima publicacao, como falha
de autenticacao contra o site do cliente.

A regra de sudo (`deploy/sudoers/publibot-deploy`) e o que permite implantar
por ssh sem ninguem na frente do terminal: um ssh nao interativo nao tem onde
digitar senha, e sem ela o `sudo systemctl` do release espera um prompt que
ninguem ve — a implantacao morre por timeout DEPOIS das migrations, com o
servico ainda no codigo antigo. Ela nomeia um a um os comandos permitidos, so
sobre as units deste projeto: um `NOPASSWD: ALL` daria ao `DEPLOY_KEY`
guardado no GitHub o poder de root sobre a maquina.

**Conferir:**

```bash
sudo test -f /etc/publibot/env && echo "env existe"
sudo grep -c AJUSTE /etc/publibot/env       # quantos campos faltam preencher
systemctl is-enabled publibot.socket celery-publibot celery-beat-publibot
sudo -u postgres psql -d publibot -tAc \
  "SELECT extname, nspname FROM pg_extension e
     JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE extname IN ('vector','unaccent')"   # as duas em 'extensions'
```

As extensoes precisam estar em `extensions`, **nao** em `public`. Com um
schema por tenant, uma extensao so no `public` funciona para o primeiro tenant
e falha no segundo, com `type "vector" does not exist` — o erro classico deste
projeto.

### Passo 3 — Preencher o que o bootstrap nao sabe

```bash
sudo nano /etc/publibot/env
```

Os campos marcados `AJUSTE`:

| Variavel | Valor |
|---|---|
| `ROOT_DOMAIN` | `exemplo.com.br` — sem `www`, sem protocolo |
| `DJANGO_ALLOWED_HOSTS` | `exemplo.com.br,.exemplo.com.br` |
| `INFERENCIA_BASE_URL` | `http://<ip-tailscale-da-placa>:11434` |
| `INFERENCIA_MODELO` | o nome exato do `ollama list` |

E, se a maquina da placa tambem for converter PDF e gerar imagem (Parte 1,
secoes 4a e 4b):

```
CONVERSAO_BASE_URL=http://<ip-tailscale-da-placa>:8100
CONVERSAO_SEGREDO=<o WORKER_SHARED_SECRET do worker>
IMAGEM_BASE_URL=http://<ip-tailscale-da-placa>:8101
IMAGEM_SEGREDO=<o mesmo WORKER_SHARED_SECRET>
```

**Conferir:**

```bash
sudo grep -c AJUSTE /etc/publibot/env      # 0
tailscale status | grep <nome-da-placa>    # a maquina aparece
curl -s http://<ip-tailscale-da-placa>:11434/api/tags | head -c 200
```

O `curl` roda **da VM**, nao da sua maquina: o que interessa e se a nuvem
alcanca a placa. Alcancar do seu notebook nao diz nada.

### Passo 4 — Nginx e TLS

```bash
sudo cp deploy/nginx/publibot.conf /etc/nginx/sites-available/publibot
sudo sed -i 's/publibot.com.br/exemplo.com.br/g' /etc/nginx/sites-available/publibot
sudo ln -sf /etc/nginx/sites-available/publibot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

O certificado precisa ser **curinga** (`*.exemplo.com.br`): cada tenant e um
subdominio, e um certificado so para o dominio raiz faz o primeiro cliente ver
um aviso de seguranca. Certificado curinga exige validacao DNS-01:

```bash
sudo certbot certonly --manual --preferred-challenges dns \
     -d exemplo.com.br -d '*.exemplo.com.br'
```

**Conferir:**

```bash
sudo nginx -t                                    # syntax is ok
curl -sI https://exemplo.com.br/healthz/ | head -1
curl -sI https://qualquer-coisa.exemplo.com.br/ | head -1   # o curinga responde
```

O `/healthz/` responde **antes** da resolucao de tenant, de proposito: um
health check que passa pela resolucao devolveria 404 num dominio sem tenant, e
o balanceador concluiria que a aplicacao esta fora do ar.

### Passo 5 — Primeira implantacao

```bash
./deploy/scripts/release.sh
```

Busca o codigo, instala dependencias, roda `check --deploy`, recusa model sem
migration, migra `public` e todos os tenants, confere o tenant raiz, semeia as
conexoes de inferencia, semeia os prompts, coleta estaticos, **sincroniza as
units** e recarrega os servicos — nessa ordem, e a ordem importa: migrations
antes de reiniciar, senao o codigo novo consulta colunas que ainda nao
existem.

A sincronizacao das units resolve uma armadilha silenciosa. Elas sao
versionadas no repositorio, mas o systemd le de `/etc/systemd/system`; sem
copiar, editar um `.service` nao tem efeito nenhum e nada avisa — a mudanca
esta no git, foi revisada, foi implantada, e o servico segue com a versao
antiga. O `daemon-reload` so acontece quando algo mudou de fato.

**Conferir:**

```bash
systemctl is-active publibot celery-publibot celery-beat-publibot
cd /srv/publibot && venv/bin/python manage.py check_db
venv/bin/python manage.py broker_status
```

O `check_db` confere banco, extensoes, `search_path` e o tenant raiz de uma
vez. O `broker_status` diz **qual** broker esta valendo — a confusao entre
Redis e Postgres como broker produz uma fila que aceita mensagens que ninguem
consome.

### Passo 6 — Conexoes de inferencia

```bash
venv/bin/python manage.py configurar_inferencia --testar
venv/bin/python manage.py configurar_conversao --testar
venv/bin/python manage.py configurar_imagem --testar
```

Os tres sao idempotentes e **preservam** o que ja estiver no banco: a conexao
vive numa linha, e nao num arquivo, para trocar de modelo sem implantar
(ADR-0012).

**Conferir:** o `--testar` ja e a conferencia. Leia a resposta, nao so o
codigo de saida:

- `configurar_conversao` deve dizer `dispositivo=cuda`. Se disser `cpu`, o
  worker esta sem placa e a conversao vai levar minutos;
- `configurar_imagem` deve dizer `dispositivo=cuda` e `baixado=True`. Com
  `baixado` falso, a primeira geracao de capa baixa alguns GB **dentro da
  requisicao** — rode `./venv/bin/python baixar_modelo.py` na maquina da placa
  antes de usar.

### Passo 7 — Primeiro acesso

```bash
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py provision_tenant acme --name="ACME Ltda"
```

**Conferir:** abra `https://acme.exemplo.com.br/`, entre, e veja o painel.

Se o login reaparecer sem erro nenhum, o `ROOT_DOMAIN` tem um rotulo so: o
navegador DESCARTA o atributo `Domain` do cookie e a sessao nao atravessa para
o subdominio.

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

```bash
./deploy/scripts/release.sh
```

**Conferir:**

```bash
systemctl is-active publibot celery-publibot celery-beat-publibot
curl -sI https://exemplo.com.br/healthz/ | head -1
journalctl -u publibot -n 20 --no-pager
```

### Implantacao pelo GitHub

`.github/workflows/ci.yml` roda a suite a cada push e, quando o commit entra
na `main`, entra no servidor por ssh e roda **o mesmo** `release.sh` de cima.

Nao ha copia de arquivo nem sequencia repetida no workflow. Descrever a
implantacao duas vezes — uma no script, outra no YAML — cria dois caminhos que
divergem no dia em que alguem corrige so um deles, e a divergencia so aparece
em producao.

Quatro segredos no repositorio (Settings > Secrets and variables > Actions):

| Segredo | O que e |
|---|---|
| `SERVER_HOST` | endereco do servidor |
| `SERVER_USER` | usuario que roda o `release.sh` |
| `DEPLOY_KEY` | chave ssh **privada** desse usuario |
| `SERVER_PORT` | porta do ssh |

Nao existe um segredo com o `.env` de producao, e a ausencia e deliberada: os
segredos sao gerados **no servidor**, uma unica vez, pelo `bootstrap.sh`, e
nunca sobrescritos. Guardar o arquivo inteiro num secret significaria
reescrever `/etc/publibot/env` a cada implantacao — e um
`NODE_KEY_ENCRYPTION_KEY` diferente do que cifrou as credenciais as torna
irrecuperaveis, com o erro aparecendo dias depois, como falha de autenticacao
contra o site do cliente.

**Conferir que a implantacao chegou** (e nao so que o workflow ficou verde):

```bash
ssh servidor 'cd /srv/publibot && git rev-parse --short HEAD'
```

Compare com o commit da `main`. Um workflow verde diz que o ssh rodou, nao que
o servico reiniciou com o codigo novo.

O job de teste sobe PostgreSQL e Redis de verdade, com a extensao `vector` no
schema `extensions` do `template1` — a mesma preparacao que o `setup-db.sh`
faz na sua maquina. Um banco falso passaria em tudo e nao diria nada sobre
schema por tenant, `search_path` ou prefixo de chave, que e exatamente o que
este projeto tem de mais fragil.

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

## Parte 3 — A maquina da placa

Ela atende a nuvem e a sua maquina de desenvolvimento, e e a mesma nos dois
casos. Nao roda Django, nem Celery, nem banco: so tres servicos HTTP
(ADR-0007).

| Servico | Porta | Sobe como |
|---|---|---|
| `ollama serve` | 11434 | unit propria do Ollama |
| `docling-api` | 8100 | `worker-gpu/deploy/instalar.sh` |
| `imagem-api` | 8101 | `worker-gpu/deploy/instalar.sh --imagem` |

```bash
cd worker-gpu
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env          # defina WORKER_SHARED_SECRET e BIND_HOST
./venv/bin/python baixar_modelo.py    # os ~7 GB do modelo de imagem, uma vez
./deploy/instalar.sh --tudo
```

O `BIND_HOST` decide quem alcanca os servicos, e e onde se erra:

| Valor | Quem alcanca |
|---|---|
| `127.0.0.1` | so esta maquina — serve enquanto o PubliBot roda aqui do lado |
| `$(tailscale ip -4)` | a VM da nuvem tambem |
| `0.0.0.0` | a internet inteira. O instalador recusa |

**Trocar de `127.0.0.1` para o endereco da Tailscale exige reinstalar as
units** (`./deploy/instalar.sh --tudo`), porque o endereco entra na linha de
comando do uvicorn.

**Conferir**, da VM da nuvem:

```bash
curl -s http://<ip-tailscale>:8100/health/
curl -s http://<ip-tailscale>:8101/health/
```

### As units sobem sozinhas no boot?

Depende de como foram instaladas, e a diferenca pega todo mundo uma vez:

| Instalacao | Sobe quando |
|---|---|
| `./deploy/instalar.sh` (padrao, unit de **usuario**) | voce faz login na maquina |
| o mesmo, **mais** `sudo loginctl enable-linger $USER` | no boot, sem login |
| `./deploy/instalar.sh --sistema` | no boot, sempre |

Numa maquina pessoal que tambem serve a nuvem, `enable-linger` e o que voce
quer: reiniciar o computador e ter os servicos de volta sem abrir sessao
grafica.

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" | grep Linger      # Linger=yes
```

### Onde fica o log

No journal, **nao** no terminal do `manage.py dev`. Esta e a confusao mais
comum quando os dois convivem na mesma maquina:

```bash
journalctl --user -u imagem-api -f
journalctl --user -u docling-api -f
```

---

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
| `/health/` do worker da `timed out` (nao "refused") | ele esta ocupado gerando. Se persistir sem nada em curso, confira o journal da unit |
| `could not open extension control file` no bootstrap | falta `postgresql-<versao>-pgvector` |
| Unit de usuario nao sobe no boot | falta `sudo loginctl enable-linger $USER` |
| Log do worker de GPU nao aparece no `dev` | ele e unit do systemd: `journalctl --user -u imagem-api -f` |
| Gerar capa leva minutos | caiu para CPU. `configurar_imagem --testar` mostra `ultimo=cpu`; a placa esta sendo disputada |
| Aviso de "texto extraido sem analise de layout" | faltou `configurar_conversao` (worker Docling) |
| `ProxyError` no meio da conversao | a rede do worker bloqueia `huggingface.co` |
| `ModuleNotFoundError: No module named 'cv2'` | venv do worker em Python 3.14 sem `opencv-python-headless` (reinstale o requirements) |
| `External data path escapes model directory` | cache do modelo em links; `rm -rf .model_cache` e deixe baixar de novo |
| Tarefas somem sem erro | Redis compartilhado sem `REDIS_NAMESPACE` |
| `relation "content_..." does not exist` a cada minuto | task do beat sem varredura por tenant (ver ARMADILHAS) |

Falhas ja encontradas e o que cada uma significa: [`ARMADILHAS.md`](ARMADILHAS.md).
