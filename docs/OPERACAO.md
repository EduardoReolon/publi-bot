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
```

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

### 6. Primeiro tenant

```bash
python manage.py provision_tenant acme --name="ACME Ltda"
```

Depois entre em `http://acme.publibot.localhost:8000`.

---

## Parte 2 — Servidor

### Uma vez por maquina

```bash
sudo mkdir -p /srv/publibot && sudo chown "$USER" /srv/publibot
git clone <repo> /srv/publibot && cd /srv/publibot
./deploy/scripts/bootstrap.sh
```

O bootstrap cria usuario de sistema, diretorios, venv, banco com as extensoes,
units do systemd habilitados no boot, rotacao de log, a regra de sudo da
implantacao, e **gera os segredos** em `/etc/publibot/env`.

A regra de sudo (`deploy/sudoers/publibot-deploy`) e o que permite implantar
por ssh sem ninguem na frente do terminal: um ssh nao interativo nao tem onde
digitar senha, e sem ela o `sudo systemctl` do release espera um prompt que
ninguem ve — a implantacao morre por timeout DEPOIS das migrations, com o
servico ainda no codigo antigo. Ela nomeia um a um os comandos permitidos, so
sobre as units deste projeto: um `NOPASSWD: ALL` daria ao `DEPLOY_KEY`
guardado no GitHub o poder de root sobre a maquina, e este servidor e
compartilhado.

Ele nunca sobrescreve esse arquivo. Nao e zelo excessivo: regenerar
`NODE_KEY_ENCRYPTION_KEY` por cima torna irrecuperaveis todas as credenciais de
site ja guardadas, e o erro so apareceria na proxima publicacao, como falha de
autenticacao contra o site do cliente.

Depois dele, sobram quatro coisas — e so uma vez:

1. Ajustar em `/etc/publibot/env` os campos marcados `AJUSTE`:
   `ROOT_DOMAIN`, `DJANGO_ALLOWED_HOSTS`, `INFERENCIA_BASE_URL` (o endereco
   Tailscale do Ollama) e `INFERENCIA_MODELO`.
2. Nginx e TLS — ver `deploy/nginx/publibot.conf`.
3. `./deploy/scripts/release.sh`
4. `venv/bin/python manage.py createsuperuser`

### Toda implantacao

```bash
./deploy/scripts/release.sh
```

Busca o codigo, instala dependencias, roda `check --deploy`, recusa model sem
migration, migra `public` e todos os tenants, confere o tenant raiz, semeia a
conexao de inferencia, coleta estaticos, **sincroniza os units** e recarrega os
servicos — nessa ordem, e a ordem importa: migrations antes de reiniciar, senao
o codigo novo consulta colunas que ainda nao existem.

A sincronizacao dos units resolve uma armadilha silenciosa. Eles sao
versionados no repositorio, mas o systemd le de `/etc/systemd/system`; sem
copiar, editar um `.service` nao tem efeito nenhum e nada avisa — a mudanca
esta no git, foi revisada, foi implantada, e o servico segue com a versao
antiga. O `daemon-reload` so acontece quando algo mudou de fato.

### Implantacao pelo GitHub

`.github/workflows/ci.yml` roda a suite a cada push e, quando o commit entra na
`main`, entra no servidor por ssh e roda **o mesmo** `release.sh` de cima.

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
reescrever `/etc/publibot/env` a cada implantacao — e um `NODE_KEY_ENCRYPTION_KEY`
diferente do que cifrou as credenciais as torna irrecuperaveis, com o erro
aparecendo dias depois, como falha de autenticacao contra o site do cliente.

O job de teste sobe PostgreSQL e Redis de verdade, com a extensao `vector` no
schema `extensions` do `template1` — a mesma preparacao que o `setup-db.sh` faz
na sua maquina. Um banco falso passaria em tudo e nao diria nada sobre schema
por tenant, `search_path` ou prefixo de chave, que e exatamente o que este
projeto tem de mais fragil.

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
```

| Sintoma | Causa provavel |
|---|---|
| `No tenant for hostname` | faltou `bootstrap_public` |
| `type "vector" does not exist` no 2o tenant | extensao no `public`, nao em `extensions` |
| Login reaparece sem erro | `ROOT_DOMAIN` com um rotulo so (`localhost`) |
| POST devolve 400 sem explicacao | porta fora de `DEV_SERVER_PORT` (CSRF compara a origem inteira) |
| Trabalho parado em `WAITING_CAPACITY` | Ollama inacessivel, ou beat nao esta rodando |
| `SemModeloConfigurado` | faltou `configurar_inferencia` |
| Aviso de "texto extraido sem analise de layout" | faltou `configurar_conversao` (worker Docling) |
| `ProxyError` no meio da conversao | a rede do worker bloqueia `huggingface.co` |
| `ModuleNotFoundError: No module named 'cv2'` | venv do worker em Python 3.14 sem `opencv-python-headless` (reinstale o requirements) |
| `External data path escapes model directory` | cache do modelo em links; `rm -rf .model_cache` e deixe baixar de novo |
| Tarefas somem sem erro | Redis compartilhado sem `REDIS_NAMESPACE` |
| `relation "content_..." does not exist` a cada minuto | task do beat sem varredura por tenant (ver ARMADILHAS) |

Falhas ja encontradas e o que cada uma significa: [`ARMADILHAS.md`](ARMADILHAS.md).
