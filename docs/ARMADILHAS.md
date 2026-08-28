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

## Pipeline e interface

### Os componentes existiam e nada os montava

Por sete entregas, `recuperar`, `interpretar_tese` e `aplicar_rascunho` foram
funcoes testadas que **nenhum caminho alcancava**: `apps/content/tasks.py` e
`apps/knowledge/tasks.py` nao existiam, `registrar_fluxo` nunca era chamado e
`criar_job` nao tinha quem o chamasse.

Cada peca passava nos testes. A montagem nao existia, e nada acusava isso —
teste de unidade nao repara na ausencia de quem chama. So apareceu ao abrir o
produto para construir a interface e procurar o que o botao "gerar artigo"
acionaria.

Os fluxos sao registrados no `ready()` do app. **Sem esse import, `criar_job`
levanta KeyError e nada e gerado.**

### `connection.tenant` pode ser um `FakeTenant`

Dentro de um `schema_context` o django-tenants coloca ali um objeto que so tem
`schema_name`. Filtrar por chave estrangeira com ele levanta:

```
ValidationError: O valor "<FakeTenant object>" nao e um UUID valido
```

A mensagem aponta para o tipo do valor, nao para a causa. Resolva o `Tenant`
pelo `connection.schema_name`, consultando o public — que esta no `search_path`
de toda conexao. Ver `apps/content/inference.py`.

### `guardar_chave` nao grava

Apesar do nome, ela so cifra e preenche os campos do objeto. Quem chama decide
quando salvar. Esquecer o `save()` deixa a credencial em memoria e o sintoma e
"a conexao nao tem segredo cadastrado".

### O botao "Sair" e o primeiro `button[type=submit]` de toda pagina

Ele fica no cabecalho, dentro de um formulario. Em teste de navegador, um
`click("button[type=submit]")` generico **desloga** em vez de submeter o
formulario da tela. Use seletor por texto.

### `kill -9` no `manage.py dev` deixa o worker orfao

SIGKILL nao pode ser tratado, entao o `finally` que encerra os filhos nunca
roda e o worker e reparentado ao init. Ele continua consumindo a mesma fila, e
um worker antigo — com outra configuracao — passa a competir com o novo. O
sintoma sao trabalhos falhando por um motivo que nao existe mais na
configuracao atual.

Ctrl+C nao tem esse problema: o console manda SIGINT ao grupo inteiro.

### Indexar um trecho depende de baixar 2 GB

O modelo de embedding e baixado na primeira utilizacao. Sem rede — ou com o
download bloqueado — a excecao de HTTP sobe no meio da requisicao e vira 500.
A tela de curadoria trata isso e diz que o problema e o modelo, e nao o trecho.

---

## Testes

### `DROP SCHEMA` no teardown falha com "pending trigger events"

`CREATE SCHEMA` e transacional no PostgreSQL e o pytest-django reverte a
transacao de cada teste. Um `DROP` explicito roda ainda dentro dessa transacao.
Nao faca o drop.

### O modelo de embedding trunca em 512 tokens sem avisar

Nao levanta erro: descarta o excedente. Vetorizar uma secao de 1100 tokens
significa vetorizar a primeira metade dela acreditando ter vetorizado o todo — e
sem forma de saber qual metade entrou. E o motivo de o indice ser alimentado por
paragrafo, e nao por bloco
([ADR-0015](adr/ADR-0015-vetorizacao-por-paragrafo.md)).

### Um paragrafo recuperado sozinho nao diz de onde veio

"esse efeito foi observado em 240 adultos" — de que estudo, de que secao? Cada
trecho e vetorizado com um prefixo de contexto (titulo do documento e do bloco).
O `content` guardado continua sendo o paragrafo puro: o prefixo serve a busca, e
mostra-lo em toda citacao seria ruido.

### Concluir a curadoria pode apagar o texto integral

A licenca padrao e "Desconhecido", e ela **nao** permite guardar o documento
inteiro. Concluir com ela descarta o `markdown_full`, e sem ele nao ha como
remarcar blocos — so reenviando o arquivo. A tela avisa antes do clique, mas a
operacao e irreversivel.

### O limiar de recuperacao medido e 0.16, nao 0.35

Com 0.35 uma **receita de bolo** passava, a distancia 0.2004, como fonte
cientifica valida. O valor foi medido com corpus real. Ver
[`ADR-0014`](adr/ADR-0014-limiar-de-recuperacao.md) e
`manage.py calibrate_retrieval`.

**O valor precisa ser remedido depois do ADR-0015.** Ele foi medido com um
trecho longo por documento; trechos por paragrafo mudam a distribuicao das
distancias. Desde o [ADR-0016](adr/ADR-0016-limiar-por-tenant-e-saude-da-busca.md)
o limiar vive no schema do tenant, e a medicao se faz pela tela **Documentos >
Qualidade da busca**; `RAG_MAX_COSINE_DISTANCE` no `.env` virou o padrao de
fabrica com que um tenant novo nasce.

### Trocar o modelo de embedding invalida o limiar em silencio

Familias diferentes comprimem a similaridade de formas diferentes. O limiar
antigo nao erra por pouco: ele deixa de querer dizer alguma coisa. Por isso a
calibracao grava **com que modelo** foi feita, e o painel avisa quando os dois
divergem. Pela mesma razao, um indice com vetores de dois modelos e alertado: as
distancias deixam de ser comparaveis entre si e a ordenacao vira sorteio, sem
erro nenhum no caminho.

### O limiar errado nao produz sintoma nenhum

Apertado demais, as geracoes falham por "sem fonte" e alguem conclui que o
acervo e pequeno. Solto demais, o texto sai apoiado em trechos que so tangenciam
o assunto — e isso a tela de revisao nao denuncia, porque a citacao parece
legitima. Os dois numeros que denunciam sao a **fracao de buscas sem fonte** e a
**folga entre a distancia tipica aceita e o corte**; ambos ficam no painel do
tenant, e nao so na tela de busca, porque quem precisa ve-los nao esta
procurando por eles.

### A suite precisa do broker no ar

`test_redespachar_retoma_do_passo_em_que_parou` chama uma view que faz
`.delay()`. Sem Redis (ou o Postgres como broker) o despacho falha e o teste
quebra com uma mensagem que nao menciona broker nenhum. Se um teste de interface
falhar sozinho, confira `redis-cli ping` antes de procurar no codigo.

### Barra de grafico em % dentro de um pai de altura automatica some

Num flex em linha com `align-items: flex-end`, os itens ficam com altura
automatica — e uma altura em `%` no filho passa a nao ter contra o que resolver.
A barra colapsa para o `min-height`, o grafico some e **nao ha erro nenhum**: nem
no console, nem no HTML, que sai correto. O histograma da tela de busca depende
do `align-items` padrao (`stretch`).

### Calibrar nao pode passar por `recuperar()`

`recuperar()` grava `RetrievalQuery`. Se o teste de consulta da tela usasse esse
caminho, cada calibracao entraria nas metricas que a propria tela mostra — e
calibrar pioraria o diagnostico. A medicao roda por fora e nao grava nada.
