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

### 5. Rodar

Tres processos, em tres terminais:

```bash
python manage.py runserver                       # 1. web
celery -A core worker -l INFO                    # 2. worker
celery -A core beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

O **beat** e necessario para publicar no horario, varrer trabalhos parados e
soltar reservas vencidas. Sem ele a aplicacao funciona, mas nada acontece
sozinho.

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
units do systemd habilitados no boot, rotacao de log, e **gera os segredos** em
`/etc/publibot/env`.

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
```

| Sintoma | Causa provavel |
|---|---|
| `No tenant for hostname` | faltou `bootstrap_public` |
| `type "vector" does not exist` no 2o tenant | extensao no `public`, nao em `extensions` |
| Login reaparece sem erro | `ROOT_DOMAIN` com um rotulo so (`localhost`) |
| POST devolve 400 sem explicacao | porta fora de `DEV_SERVER_PORT` (CSRF compara a origem inteira) |
| Trabalho parado em `WAITING_CAPACITY` | Ollama inacessivel, ou beat nao esta rodando |
| `SemModeloConfigurado` | faltou `configurar_inferencia` |
| Tarefas somem sem erro | Redis compartilhado sem `REDIS_NAMESPACE` |

Falhas ja encontradas e o que cada uma significa: [`ARMADILHAS.md`](ARMADILHAS.md).
