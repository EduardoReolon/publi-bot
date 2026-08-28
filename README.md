# PubliBot

Orquestrador multi-tenant que transforma literatura cientifica em conteudo web
fundamentado, com revisao humana obrigatoria e publicacao agendada em sites de
terceiros.

> ### Status
>
> Este README descreve **o que existe hoje**, nao o que esta planejado.
>
> | Bloco | Estado |
> |---|---|
> | Fundacao: configuracao, Celery, PostgreSQL, infraestrutura de dev | **Pronto** |
> | Tenancy: schema por tenant, cadastro por subdominio, isolamento | **Pronto** |
> | Base de conhecimento: documentos, curadoria, RAG com pgvector | **Pronto** |
> | Inferencia: conexoes, reserva de capacidade, retomada de trabalhos | **Pronto** |
> | Conteudo: prompts versionados, tese, redacao, sanitizacao | **Pronto** |
> | Contrato `/api/v1` e no de referencia | **Pronto** |
> | Cadencia, perguntas e respostas, sondas de saude, deploy | **Pronto** |
> | Pipeline: os componentes ligados num fluxo que roda | **Pronto** |
> | Interface do tenant: painel, acervo, pautas, revisao, site, operacao | **Pronto** |
> | Ligacao ponta a ponta com GPU real | A fazer — depende de hardware |
> | Geracao de imagem | A fazer |
> | Metricas de desempenho do conteudo publicado | A fazer |
>
> As decisoes que sustentam tudo isso estao em [`docs/adr/`](docs/adr/), com o
> raciocinio e as consequencias de cada uma. As falhas que so aparecem ao rodar
> — e os erros que apontam para o lugar errado — estao em
> [`docs/ARMADILHAS.md`](docs/ARMADILHAS.md).
>
> **303 testes**, em tres suites (`./scripts/test-all.sh`).

## O problema

Gerar conteudo com um modelo de linguagem sem fundamentacao produz texto
generico e sujeito a alucinacao. Fundamenta-lo com documentos reais esbarra em
dois obstaculos praticos: extratores de PDF falham em artigos cientificos de
duas colunas, e injetar trechos avulsos no prompt produz paragrafos que se
contradizem entre si.

## A abordagem

1. **Analise de layout por visao.** O PDF e convertido em Markdown estruturado
   por um modelo que enxerga a pagina, em vez de um extrator de texto.
2. **Indexacao por resumo, com curadoria humana.** Em vez de fatiar o documento
   as cegas, uma pessoa seleciona os trechos de maior valor — tipicamente
   resumo e conclusao — e apenas eles sao vetorizados.
3. **Tese antes da redacao.** Antes de escrever, o sistema le os trechos
   recuperados e constroi uma tese unica, registrando explicitamente quando as
   fontes divergem entre si.
4. **Revisao humana obrigatoria.** Nenhum conteudo e publicado sem aprovacao,
   e o esforco editorial e registrado e mensuravel.

## Arquitetura em uma frase

Um SaaS Django multi-tenant na nuvem coordena o trabalho; a inferencia pesada
roda em endpoints HTTP — uma GPU local numa rede privada, ou APIs hospedadas —
cada um com limite proprio de concorrencia; a fila e a fonte da verdade vivem
no banco, nunca no broker.

Detalhes em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) e nos ADRs.

## Stack

| Camada | Tecnologia |
|---|---|
| Aplicacao | Python 3.12, Django 5.2 LTS |
| Multi-tenancy | django-tenants (um schema PostgreSQL por tenant) |
| Fila | Celery 5.6 + Redis, com tenant-schemas-celery |
| Agendamento | django-celery-beat (`DatabaseScheduler`) |
| Banco | PostgreSQL 17 + pgvector (indice HNSW, distancia de cosseno) |
| Embeddings | `intfloat/multilingual-e5-large` (1024 dim), em CPU via ONNX |
| Inferencia | Ollama local e APIs compativeis com OpenAI |

## Ambiente de desenvolvimento

Pre-requisitos: Python 3.12, PostgreSQL com pgvector, e Redis. Tudo nativo — o
projeto nao depende de container em nenhuma etapa (ver passo 3).

### 1. Clonar e criar o ambiente

**Linux / macOS**

```bash
git clone https://github.com/EduardoReolon/publi-bot.git
cd publi-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/EduardoReolon/publi-bot.git
cd publi-bot
py -3.12 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

### 2. Configurar

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Cole o valor gerado em `DJANGO_SECRET_KEY` e defina `POSTGRES_PASSWORD`.

### 3. PostgreSQL (e Redis, se voce quiser)

O caminho padrao e **nativo, sem container**. No Ubuntu o pgvector esta no
repositorio oficial da distribuicao:

```bash
sudo apt install postgresql postgresql-contrib redis-server
sudo apt install postgresql-16-pgvector      # ajuste 16 para a sua versao
./scripts/setup-db.sh
```

<details>
<summary><strong>Desenvolvendo no Windows? Da para rodar sem Redis.</strong></summary>

Nao existe build oficial de Redis para Windows. Como o PostgreSQL ja e
necessario, ele pode servir tambem de fila em desenvolvimento — basta uma
variavel no `.env`:

```ini
BROKER_BACKEND=postgres
```

A URL da fila e montada a partir das credenciais `POSTGRES_*` que voce ja
configurou, no **mesmo banco** da aplicacao. Nenhuma variavel adicional, e
nenhum servico a mais para manter.

Instale a dependencia de desenvolvimento (ja incluida em
`requirements-dev.txt`):

```bash
pip install -r requirements-dev.txt
```

**Isto e so para desenvolvimento.** Producao usa Redis. O modo Postgres nao
reproduz o `visibility_timeout` do Redis — o comportamento de reentrega de
mensagem ja reservada, que e justamente o risco de gerar o mesmo artigo duas
vezes — e a latencia da fila e maior por causa do polling (medido: ~2100 ms
contra ~3 ms). Para este produto isso e irrelevante, porque as tarefas reais
levam dezenas de segundos, mas as diferencas estao documentadas em
[`ADR-0013`](docs/adr/ADR-0013-broker-postgres-em-dev.md).

Para o PostgreSQL e o pgvector no Windows, use o `compose.yaml` (abaixo) ou
WSL2 — que tambem devolve o pool `prefork` do Celery, sem suporte oficial no
Windows nativo.

</details>

O script cria o papel, o banco e — o passo que nao pode ser esquecido — instala
a extensao `vector` num schema **`extensions`** dedicado, nao no `public`.

Isso nao e preciosismo: com um schema por tenant, uma extensao instalada apenas
no `public` nao fica alcancavel da forma que as migrations esperam ao criar o
segundo tenant. O primeiro funciona e o segundo falha com
*type "vector" does not exist*. O `PG_EXTRA_SEARCH_PATHS = ["extensions"]` do
settings fecha o circuito.

<details>
<summary>pgvector no Windows, sem container</summary>

Nao ha binario oficial do pgvector para Windows: ele precisa ser compilado uma
vez. Com o "C++ support" do Visual Studio instalado, abra o **x64 Native Tools
Command Prompt** como administrador (o prompt comum falha com
`error C2196: case value '4' already used`) e rode:

```bat
set "PGROOT=C:\Program Files\PostgreSQL\16"
git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

Ajuste o `16` para a sua versao do PostgreSQL. Depois, no `psql` como
superusuario, rode o SQL que o `python manage.py check_db` imprime — ele monta
os comandos com o nome do seu banco e do seu usuario.

</details>

<details>
<summary>Alternativa em container (util no Windows)</summary>

Compilar o pgvector no Windows exige MSVC e os headers do PostgreSQL. Se voce
ainda desenvolve no Windows, o container evita esse trabalho:

```bash
docker compose up -d
```

O `compose.yaml` sobe **apenas** PostgreSQL com pgvector e Redis. O Django
nunca e containerizado — ele roda no virtualenv nativo, preservando depurador,
recarga automatica e stack trace direto.

</details>

### 4. Migrar e rodar

```bash
python manage.py migrate_schemas --shared
python manage.py bootstrap_public
python manage.py createsuperuser
python manage.py dev
```

O `dev` confere o banco antes de subir e recusa sair do lugar se faltar alguma
coisa, imprimindo os comandos exatos. A verificacao avulsa e:

```bash
python manage.py check_db
```

Vale rodar isso primeiro num PostgreSQL que voce nao preparou. A verificacao
principal nao consulta catalogo: ela CRIA uma coluna `vector`, que e o que a
migration faz. So passa quando a extensao existe, esta no `search_path` E o
usuario tem `USAGE` no schema dela — tres condicoes que, quando faltam,
produzem a MESMA mensagem, e uma que nao menciona extensao nenhuma:

```
django.db.utils.ProgrammingError: tipo "vector" nao existe
LINE 1: ... "embedding" vector(1024)...
```

Pior ainda, esse erro chega tarde: as migrations compartilhadas passam sem
reclamar, e a falha so acontece ao provisionar o primeiro tenant, dentro de uma
task do worker.

**`dev`, e nao `runserver`.** Este sistema sao dois processos: o servidor web e
o worker do Celery. O `dev` sobe os dois no mesmo terminal e o Ctrl+C encerra
os dois — os filhos herdam o grupo de processo do console, entao isso funciona
sem supervisor nenhum, no Linux, no macOS e no Windows. No Windows ele ja
acrescenta `-P solo` ao worker, porque o pool `prefork` nao tem suporte oficial
desde o Celery 4 e falha de forma erratica.

Rodar os dois a mao continua valendo (`runserver` num terminal, o `celery`
abaixo em outro) — o `dev` so evita o esquecimento:

```bash
celery -A core worker -l INFO --concurrency=1 --prefetch-multiplier=1
```

O worker nao e um extra para "quando for gerar artigo": **o cadastro de um
tenant ja depende dele**. Criar o schema e rodar as migrations leva dezenas de
segundos, tempo demais para uma request HTTP, entao o cadastro publica uma
mensagem e a tela fica esperando (ADR-0001).

Sem worker nada falha — e esse e o problema. O despacho funciona, a mensagem
entra na fila e fica la. A tela de espera detecta isso: depois de ~30s ela
inspeciona a profundidade da fila e, se a mensagem continua parada, nomeia a
causa e o comando. A mensagem nao se perde: assim que um worker sobe, ela e
consumida e a mesma tela vira "ambiente pronto", sem refazer o cadastro
(verificado no Chromium: 6s depois de subir o worker).

Dois brokers diferentes tem exatamente o mesmo efeito e sao piores de achar:
com o servidor web em `BROKER_BACKEND=redis` e o worker em `postgres`, cada um
fala com uma fila e os dois parecem saudaveis. Cada processo le o `.env` na
hora em que sobe, entao um terminal aberto antes de voce editar o `.env`
continua no broker antigo. Para conferir, de dentro do mesmo virtualenv:

```bash
python manage.py broker_status
```

Ele mostra o broker deste processo e quantas mensagens esperam um worker. O
numero nao cair e a prova de que ninguem esta consumindo aquela fila; compare
com a linha `transport:` do banner do worker.

Note `migrate_schemas`, nao `migrate`: o comando puro do Django nao percorre os
schemas dos tenants.

O `bootstrap_public` nao e opcional e nao da para deduzir que falta. As
migrations criam as TABELAS do schema `public`; elas nao criam a LINHA em
`accounts_tenant` que o django-tenants consulta para descobrir qual schema
atende um host. Sem ela a primeira requisicao devolve um 404 cru — o mesmo
404 que um subdominio inexistente devolve:

```
Page not found (404)
No tenant for hostname "publibot.localhost"
```

O comando e idempotente, entao pode entrar no roteiro de deploy junto do
`migrate_schemas`, nao so na primeira instalacao.

Acesse **`http://publibot.localhost:8000/`** — nao `http://localhost:8000/`.
Um tenant chamado `acme` responde em `http://acme.publibot.localhost:8000/`,
e **nao** em `http://acme.localhost:8000/`: o subdominio e do dominio raiz
inteiro, nao de `localhost`. A home lista os ambientes com o endereco completo
e com link, entao nao ha o que adivinhar.
Navegadores resolvem qualquer `*.localhost` para 127.0.0.1, entao nada precisa
ser adicionado ao `/etc/hosts`. Em `DEBUG`, abrir `localhost:8000` por reflexo
redireciona para o dominio raiz em vez de dar 404 — mas so em `DEBUG`: em
producao um host desconhecido continua recebendo 404 seco, sem dizer o que
existe.

O dominio de desenvolvimento tem **dois rotulos** de proposito. Verificado com
o Chromium: ao receber `Set-Cookie: ...; Domain=.localhost`, o navegador
descarta o atributo `Domain` e grava o cookie como host-only, porque
`localhost` e tratado como sufixo publico. O login funcionaria no apex e o
subdominio do tenant devolveria a tela de login, sem nenhuma mensagem que
apontasse a causa.

### 5. Worker do Celery

Ja sobe junto no `manage.py dev`. Em producao ele e uma unit do systemd
(`deploy/systemd/`), com `--concurrency=2` — la os dois processos sao
independentes de proposito, e o `dev` recusa rodar fora do `DEBUG` por isso: ele
amarra o ciclo de vida dos dois, e derrubar o site porque o worker morreu seria
o oposto do que se quer em producao.

### Quando algo nao funciona

```bash
python manage.py check_db        # banco, extensoes, tenant public
python manage.py broker_status   # broker deste processo e profundidade da fila
```

[`docs/ARMADILHAS.md`](docs/ARMADILHAS.md) lista as falhas reais deste projeto
com o sintoma literal, a causa e onde ela esta tratada. Vale a leitura antes de
diagnosticar do zero: varias tem mensagens de erro que apontam para o lugar
errado, e tres causas diferentes chegam a produzir texto identico.

### Testes e lint

```bash
pytest
ruff check .
ruff format .
pre-commit install    # uma vez, para rodar tudo isso a cada commit
```

## Como se opera

Tudo acontece dentro do subdominio do tenant (`acme.publibot.localhost:8000`).

| Tela | Para que serve |
|---|---|
| **Painel** | O que espera uma pessoa e o que quebrou, separados. Cada numero leva a tela que resolve. |
| **Documentos** | Envio, conversao em Markdown, curadoria e selecao do trecho que vai para o indice. |
| **Pautas** | O tema a ser buscado no acervo, e o botao que dispara a geracao. |
| **Artigos** | A fila de revisao e a tela de leitura: texto ao lado das fontes, edicao, aprovacao. |
| **Perguntas** | Duvidas importadas do site, com resposta gerada do mesmo acervo. |
| **Site e cadencia** | Credenciais do site de destino, teste de conexao e quando publicar. |
| **Operacao** | Trabalhos, passos, chamadas ao modelo e tentativas de publicacao. |

O caminho completo, na ordem:

1. **Documentos > Categorias**: crie ao menos uma.
2. **Documentos > Enviar**: um PDF, `.txt` ou `.md`. A conversao roda no worker.
3. **Documentos > (o documento)**: confira titulo, autores, ano e **URL de
   origem** — sao esses campos que viram o link publicado, e a URL e a unica
   que o documento nao tem como informar sozinho. Abaixo aparecem os blocos que
   a extracao reconheceu: marque os que podem sustentar um artigo e conclua.

   Cada **paragrafo** de um bloco marcado vira um vetor proprio, e nao o bloco
   inteiro ([ADR-0015](docs/adr/ADR-0015-vetorizacao-por-paragrafo.md)). Se um
   PDF aparecer como um unico bloco disforme, foi lido sem analise de layout —
   a propria forma da tela diz isso.

   Atencao a licenca: concluir a curadoria de documento proprietario ou de
   licenca desconhecida **apaga o texto integral** e nao ha como remarcar blocos
   depois sem reenviar o arquivo. A tela avisa antes.
4. **Pautas**: crie uma e clique em *Gerar artigo*.
5. **Artigos**: revise, preencha o autor e aprove. Sem autor identificado nao
   ha publicacao.
6. **Site e cadencia**: cadastre o site e a cadencia para o agendador publicar.

Nada disso funciona sem uma **conexao de inferencia** cadastrada no admin
(`/admin/inference/inferenceconnection/`), do tipo *Compativel com OpenAI*
apontando para o seu Ollama, com a carga `text` marcada. Para converter PDF com
analise de layout, cadastre tambem uma do tipo *Docling* apontando para o
`worker-gpu/`; sem ela, PDF cai no extrator local, que embaralha coluna dupla e
nao le documento digitalizado.

## Comandos de tenant

```bash
# Cria um tenant COMPLETO: registro, schema fisico e migrations dentro dele.
# E o comando a usar hoje — o `create_tenant` nativo do django-tenants NAO
# basta neste projeto: ele so grava o registro. A criacao do schema fisico
# so acontece automaticamente quando `auto_create_schema=True` no model, e
# este projeto desliga essa flag de proposito (ADR-0001) para o
# provisionamento nao travar a request HTTP do cadastro. Este comando e o
# equivalente sincrono dessa rotina, para terminal e scripts — a Entrega 2
# fara a mesma coisa de forma assincrona, por tras do cadastro web.
python manage.py provision_tenant acme --name="ACME Ltda"

# O mesmo comando RETOMA um tenant que ficou pela metade — o caso de um
# cadastro feito sem o worker rodando: a linha existe, o schema nao. Passe o
# schema_name que aparece na home.
python manage.py provision_tenant teste1

# Aplica migrations em public e em todos os tenants ja provisionados.
# Tenants sem schema fisico sao IGNORADOS, com aviso: sem isso, um unico
# cadastro pela metade derruba o comando inteiro com um erro que nao nomeia
# o tenant nem a causa (ver apps/accounts/migration_executors.py).
python manage.py migrate_schemas

# Registra o tenant `public` e aponta ROOT_DOMAIN para ele. Idempotente.
python manage.py bootstrap_public

# Roda um comando dentro de um tenant especifico
python manage.py tenant_command shell --schema=acme
```

## Estrutura

```
apps/
  accounts/      Tenant, Domain, User, TenantMembership  (schema public)
  inference/     Conexoes de inferencia e reservas       (schema public)
  knowledge/     Documentos, trechos curados, RAG        (por tenant)
  content/       Prompts, artigos, perguntas, respostas  (por tenant)
  integrations/  Sites, contrato, cadencia               (por tenant)
  ops/           Trabalhos de geracao, sondas de saude   (por tenant)
core/
  settings/      base, dev, prod, test_contract
  celery.py      app do Celery, com propagacao de tenant
deploy/          Nginx, systemd, Gunicorn, scripts
docs/
  ARCHITECTURE.md    especificacao original, com as revisoes marcadas
  adr/               decisoes de arquitetura
  contrato/          contrato /api/v1, OpenAPI e implementacao de referencia
worker-gpu/      Servicos HTTP da maquina com GPU
tests/           Suite principal
tests_contrato/  Contrato exercitado nos dois lados
```

## Comandos uteis

```bash
# Cria um tenant completo: registro, schema e migrations
python manage.py provision_tenant acme --name="ACME Ltda"

# O mesmo comando RETOMA um tenant que ficou pela metade — o caso de um
# cadastro feito sem o worker rodando: a linha existe, o schema nao. Passe o
# schema_name que aparece na home.
python manage.py provision_tenant teste1

# Calibra o limiar de recuperacao com o corpus real de um tenant
python manage.py tenant_command calibrate_retrieval --schema=acme \
    --consulta "sua consulta de teste"

# Roda as tres suites
./scripts/test-all.sh
```

## Licenca

Nenhuma licenca foi concedida. Todos os direitos reservados. Ver
[`NOTICE.md`](NOTICE.md).
