# PubliBot

Orquestrador multi-tenant que transforma literatura cientifica em conteudo web
fundamentado, com revisao humana obrigatoria e publicacao agendada em sites de
terceiros.

> ### Status: em construcao — Fase 0 concluida
>
> Este README descreve **o que existe hoje**, nao o que esta planejado. O que
> ainda nao foi construido esta marcado como tal.
>
> | Bloco | Estado |
> |---|---|
> | Fundacao: configuracao, Celery, Postgres, infraestrutura de dev | **Pronto** |
> | Tenancy: Tenant, Domain, User, TenantMembership, isolamento por schema | **Pronto** |
> | Cadastro autonomo por subdominio | A fazer |
> | Base de conhecimento e RAG | A fazer |
> | Geracao de conteudo | A fazer |
> | Contrato de integracao com os sites | A fazer |
> | Motor de cadencia, perguntas e respostas, imagem, deploy | A fazer |
>
> As decisoes que sustentam tudo isso estao em [`docs/adr/`](docs/adr/).

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

### 3. PostgreSQL e Redis

O caminho padrao e **nativo, sem container**. No Ubuntu o pgvector esta no
repositorio oficial da distribuicao:

```bash
sudo apt install postgresql postgresql-contrib redis-server
sudo apt install postgresql-16-pgvector      # ajuste 16 para a sua versao
./scripts/setup-db.sh
```

O script cria o papel, o banco e — o passo que nao pode ser esquecido — instala
a extensao `vector` num schema **`extensions`** dedicado, nao no `public`.

Isso nao e preciosismo: com um schema por tenant, uma extensao instalada apenas
no `public` nao fica alcancavel da forma que as migrations esperam ao criar o
segundo tenant. O primeiro funciona e o segundo falha com
*type "vector" does not exist*. O `PG_EXTRA_SEARCH_PATHS = ["extensions"]` do
settings fecha o circuito.

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
python manage.py createsuperuser
python manage.py runserver
```

Note `migrate_schemas`, nao `migrate`: o comando puro do Django nao percorre os
schemas dos tenants.

Acesse `http://localhost:8000/admin/`. Um tenant chamado `acme` responderia em
`http://acme.localhost:8000/` — navegadores resolvem qualquer subdominio de
`.localhost` para 127.0.0.1 sem precisar editar `/etc/hosts`.

### 5. Worker do Celery

```bash
celery -A core worker -l INFO --concurrency=2 --prefetch-multiplier=1
```

No Windows, acrescente `-P solo`: o pool `prefork` nao tem suporte oficial
desde o Celery 4 e falha de forma erratica.

### Testes e lint

```bash
pytest
ruff check .
ruff format .
pre-commit install    # uma vez, para rodar tudo isso a cada commit
```

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

# Aplica migrations em public e em todos os tenants ja provisionados
python manage.py migrate_schemas

# Roda um comando dentro de um tenant especifico
python manage.py tenant_command shell --schema=acme
```

## Estrutura

```
apps/
  accounts/       Tenant, Domain, User, TenantMembership (schema public)
core/
  settings/       base, dev, prod
  celery.py       app do Celery, com propagacao de tenant
  urls_public.py  rotas do dominio raiz
  urls_tenants.py rotas de um tenant
deploy/
  postgres/init/  criacao do schema `extensions` e das extensoes
docs/
  ARCHITECTURE.md especificacao do sistema
  adr/            decisoes de arquitetura
tests/            isolamento entre tenants e propagacao de schema
```

## Licenca

Nenhuma licenca foi concedida. Todos os direitos reservados. Ver
[`NOTICE.md`](NOTICE.md).
