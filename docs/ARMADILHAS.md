# Armadilhas

Falhas reais deste projeto, com o sintoma literal, a causa e onde ela esta
tratada hoje. Todas custaram tempo de diagnostico, e quase nenhuma tem um erro
que aponte para a propria causa — varias tem mensagens que apontam para o lugar
errado.

Este arquivo existe para uma reimplementacao futura. As **decisoes** estao em
[`adr/`](adr/); aqui estao os **fatos** que so aparecem ao rodar.

Se algo nao estiver funcionando, comece por:

```bash
python manage.py check_db        # banco, extensoes, tenant public
python manage.py broker_status   # broker e profundidade da fila
```

---

## Banco de dados

### `type "vector" does not exist` — tres causas, uma mensagem

```
django.db.utils.ProgrammingError: tipo "vector" nao existe
LINE 1: ... "embedding" vector(1024)...
```

Aparece so ao provisionar o **primeiro tenant**, dentro de uma task do worker —
as migrations compartilhadas passam antes disso sem reclamar.

As tres causas produzem esse texto identico:

1. A extensao pgvector nao esta instalada.
2. Esta instalada, mas fora do `search_path` da conexao.
3. Esta no caminho, mas o usuario da aplicacao nao tem `USAGE` no schema dela.

A (3) e a pior: a extensao aparece em `pg_extension`, tudo *parece* certo, e o
erro e o mesmo de extensao ausente.

**Tratamento:** `manage.py check_db` nao consulta catalogo — ele CRIA uma coluna
`vector`, que e o que a migration faz, e so passa com as tres condicoes
satisfeitas. `manage.py dev` roda essa conferencia antes de subir.

### A extensao vai em `extensions`, nunca em `public`

Com um schema por tenant, uma extensao instalada so no `public` nao fica
alcancavel da forma que as migrations esperam ao criar o **segundo** tenant. O
primeiro funciona, o segundo falha. `PG_EXTRA_SEARCH_PATHS = ["extensions"]`
fecha o circuito. Ver [`ADR-0004`](adr/ADR-0004-postgres-pgvector.md).

### O `pytest` precisa da extensao no `template1`

`vector` nao e uma extensao *trusted*: cria-la exige superusuario, e o usuario
da aplicacao nao e (nem deveria ser). O pytest cria o banco de teste em tempo de
execucao **com o usuario da aplicacao**, entao ele nunca conseguiria instalar a
extensao sozinho. Todo banco novo herda do `template1`, entao preparar o
`template1` resolve de uma vez.

### `ALTER DATABASE CURRENT_DATABASE` nao existe

Nao ha palavra-chave para "o banco atual" em `ALTER DATABASE`. O jeito e um
bloco `DO $$ ... EXECUTE format(..., current_database()) ... $$;`.

### No Windows o pgvector precisa ser compilado

Nao ha binario oficial. Exige o "C++ support" do Visual Studio e o **x64 Native
Tools Command Prompt** como administrador — o prompt comum falha com
`error C2196: case value '4' already used`. O `check_db` imprime os passos.

---

## Tenancy e dominios

### `migrate_schemas --shared` nao registra o tenant `public`

Sintoma: a primeira requisicao devolve um 404 cru.

```
Page not found (404) — No tenant for hostname "publibot.localhost"
```

As migrations criam as **tabelas** do schema public; nao criam a **linha** em
`accounts_tenant` que o django-tenants consulta para resolver um host. Sao
passos distintos e o 404 e o mesmo que um subdominio inexistente devolve.

**Tratamento:** `manage.py bootstrap_public`, idempotente, tambem no
`release.sh`.

### `migrate_schemas` (todos) nao confere se o schema existe

Com `--schema=<nome>` ele confere e da um erro claro. Migrando **todos**, ele
pega toda linha de `accounts_tenant` e manda migrar, sem conferir. Como
`auto_create_schema = False` ([`ADR-0001`](adr/ADR-0001-proposito-e-escopo.md)),
existe uma janela em que a linha existe e o schema nao — e um unico tenant nessa
janela derruba a migracao de todos os outros.

O erro nao nomeia o tenant nem a causa. O `search_path` aponta para um schema
inexistente, o `CREATE TABLE django_migrations` cai no que sobrar, e o Postgres
reclama do que encontrar por ultimo. **Duas mensagens diferentes ja vistas para
a mesma causa:**

```
Unable to create the django_migrations table (nenhum esquema foi selecionado para cria-lo(a)
Unable to create the django_migrations table (permission denied for schema extensions
```

**Tratamento:** `apps/accounts/migration_executors.py`, ligado pelo
`GET_EXECUTOR_FUNCTION`. Nao da para sobrescrever o comando: o Django resolve
nomes pela ordem de `INSTALLED_APPS` e `django_tenants` precisa vir antes.

### `create_tenant` nativo nao cria o schema

Com `auto_create_schema = False` ele so chama `tenant.save()`. Use
`manage.py provision_tenant`, que tambem **retoma** um registro que ficou pela
metade.

### O dominio de dev precisa de dois rotulos

`publibot.localhost`, nao `localhost`. Verificado no Chromium: ao receber
`Set-Cookie: ...; Domain=.localhost`, o navegador **descarta** o atributo
`Domain` e grava o cookie como host-only, porque `localhost` e tratado como
sufixo publico.

O efeito e silencioso: o login funciona no apex, o cookie aparece no navegador,
e mesmo assim o subdominio do tenant devolve a tela de login.

### O subdominio e do dominio raiz inteiro

O tenant `acme` responde em `acme.publibot.localhost`, **nao** em
`acme.localhost`. Navegadores resolvem qualquer `*.localhost` para 127.0.0.1,
entao os dois "existem" e so um funciona.

### `request.get_port()` mente atras do nginx

Ele le `SERVER_PORT`, que e a porta **interna** do gunicorn. Sem
`USE_X_FORWARDED_PORT`, um link montado assim aponta para `https://dominio:8000/`
— porta nao publicada. A porta correta sai do cabecalho `Host`, via
`request.get_host()`. Ver `apps/accounts/enderecos.py`.

### `/healthz/` depois da resolucao de tenant devolve 404

O balanceador e o orquestrador consultam por IP ou nome interno, que nunca bate
com o dominio de um cliente. O `HealthCheckMiddleware` precisa vir **antes** do
middleware de tenant — senao a infraestrutura conclui que a aplicacao esta morta
e mata containers saudaveis.

---

## Celery e fila

### Sao dois processos, e sem o worker nada falha

O cadastro de um tenant depende do worker. Sem ele a mensagem e publicada com
sucesso e fica na fila para sempre. **Nao ha erro em lugar nenhum** — o despacho
funcionou.

**Tratamento:** `manage.py dev` sobe os dois; a tela de espera inspeciona a
profundidade da fila apos ~30s e nomeia a causa.

### Worker em outro broker: os dois lados parecem saudaveis

Cada processo le o `.env` **na hora em que sobe**. Um terminal aberto antes de
voce editar o `.env` continua no broker antigo. O sintoma e identico ao de nao
haver worker. `manage.py broker_status` mostra o broker deste processo; compare
com a linha `transport:` do banner do worker.

### `app.control.ping()` nao funciona no transporte `sqla`

O ping viaja por fanout (pidbox), que o transporte `sqla` nao entrega. **Medido:
com um worker de pe e consumindo, `ping()` devolveu lista vazia em 12 tentativas
seguidas.** Para saber se alguem consome, use `_size()` da fila — que faz parte
da base dos transportes virtuais e responde igual no Redis e no `sqla`. Ver
`apps/ops/broker.py`.

### O *result backend* trava o despacho, nao o broker

Com o Redis fora do ar, `.delay()` bloqueou **19,5s** dentro do `on_commit` do
cadastro e terminou em `RuntimeError: The Celery application must be restarted`.

A causa esta em `Celery.send_task`:

```python
if not ignore_result:
    self.backend.on_task_call(P, task_id)
```

Esse `on_task_call` abre a conexao do consumidor de resultados e, com o Redis
caido, entra num laco de 20 tentativas de 1s — na thread da request.

`provision_tenant` nunca teve o `AsyncResult` lido (quem responde pelo estado e
a coluna `status` do Tenant), entao ele leva `ignore_result=True`. A mesma
situacao passou a falhar em **0,66s**, com um `OperationalError` tratavel.

### `visibility_timeout` e exclusivo do Redis

O transporte `sqla` repassa `transport_options` direto ao `create_engine()` do
SQLAlchemy, que rejeita o que nao conhece com `TypeError` — e o worker nem sobe.
Por isso a opcao e aplicada condicionalmente em `base.py`.

### `CELERY_BROKER` colide com o namespace

`config_from_object(namespace="CELERY")` retira o prefixo, entao uma variavel
chamada `CELERY_BROKER` vira `broker` e e interpretada como URL — o worker
tentava AMQP num host chamado "postgres". Por isso a variavel se chama
`BROKER_BACKEND`, sem o prefixo.

### O Celery le `os.environ` antes das settings do Django

Confirmado em `celery/app/utils.py`. Um `CELERY_BROKER_URL` no `.env` vence o
que o settings calcula. Por isso o modo postgres reescreve as variaveis de
ambiente depois de montar a URL.

---

## Django, templates e HTML

### `{# #}` de varias linhas nao e comentario

So vale para uma linha. Um comentario multi-linha com essa sintaxe e **impresso
na pagina**. Use `{% comment %}`. Nenhum teste unitario pega isso — apareceu ao
abrir a home no navegador.

### Um campo chamado `timezone` sombreia o modulo

`default=timezone.now` passa a resolver para o `CharField`, nao para
`django.utils.timezone`. O campo do `Site` se chama `site_timezone`.

### `nh3` aborta o processo em vez de levantar excecao

Com `rel` entre os atributos permitidos e sem `link_rel=None`, o nh3 levanta
`PanicException`, que **encerra o processo**. Ver `apps/content/rendering.py`.

---

## Testes

### `DROP SCHEMA` no teardown falha com "pending trigger events"

`CREATE SCHEMA` e transacional no PostgreSQL e o pytest-django reverte a
transacao de cada teste. Um `DROP` explicito roda ainda dentro dessa transacao.
Nao faca o drop.

### O limiar de recuperacao medido e 0.16, nao 0.35

Com 0.35 uma **receita de bolo** passava, a distancia 0.2004, como fonte
cientifica valida. O valor foi medido com corpus real. Ver
[`ADR-0014`](adr/ADR-0014-limiar-de-recuperacao.md) e
`manage.py calibrate_retrieval`.
