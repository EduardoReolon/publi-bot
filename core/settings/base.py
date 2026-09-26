"""Configuracao comum a todos os ambientes.

Nao importe este modulo diretamente: use `core.settings.dev` ou
`core.settings.prod`, que definem DEBUG e as politicas de seguranca.

Decisoes registradas em docs/adr/ — em especial ADR-0003 (schema por tenant),
ADR-0006 (usuarios compartilhados no public) e ADR-0009 (ingles no codigo,
pt-BR na interface).
"""

from __future__ import annotations

import os
from pathlib import Path

from core import env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env.load_env_file(BASE_DIR)

# ---------------------------------------------------------------------------
# Identidade
# ---------------------------------------------------------------------------
# Nome canonico do projeto (ADR-0002). Vira prefixo de chave no Redis, nome do
# app do Celery, nome de unit do systemd e namespace do contrato de API.
PROJECT_SLUG = "publibot"

# Dominio raiz do SaaS. A home fica no apex; cada tenant num subdominio.
# Em desenvolvimento use "localhost": navegadores resolvem qualquer
# *.localhost para 127.0.0.1 sem precisar editar /etc/hosts.
ROOT_DOMAIN = env.get("ROOT_DOMAIN", "localhost")

# ---------------------------------------------------------------------------
# Seguranca
# ---------------------------------------------------------------------------
SECRET_KEY = env.require("DJANGO_SECRET_KEY")

DEBUG = False

ALLOWED_HOSTS: list[str] = env.csv_list("DJANGO_ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Aplicacoes — divididas por schema (django-tenants)
# ---------------------------------------------------------------------------
# SHARED_APPS  -> tabelas criadas SOMENTE no schema `public`
# TENANT_APPS  -> tabelas replicadas em CADA schema de tenant
#
# Dentro de um tenant o search_path e "<schema>, public, extensions", entao as
# tabelas compartilhadas continuam visiveis de dentro do tenant. E por isso que
# os usuarios podem morar so no public e ainda assim serem alcancaveis por uma
# FK vinda de uma tabela do tenant.

SHARED_APPS = [
    # Precisa ser o primeiro: registra o backend e os comandos de schema.
    "django_tenants",
    # Tenant, Domain, User e TenantMembership (ADR-0006).
    "apps.accounts",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    # O agendamento vive no public: as entradas fixas de infraestrutura sao do
    # sistema, nao de um cliente. A cadencia por tenant fica em outra tabela.
    "django_celery_beat",
    # Guarda o resultado das tasks numa tabela do Django. Usado quando o broker
    # e o proprio PostgreSQL (ADR-0013); inofensivo quando o broker e o Redis.
    "django_celery_results",
    # Conexoes de inferencia: compartilhadas entre tenants, logo no public.
    "apps.inference",
]

TENANT_APPS = [
    # Necessario dentro do schema para o admin e as permissoes por tenant
    # resolverem corretamente.
    "django.contrib.contenttypes",
    "apps.knowledge",
    "apps.ops",
    "apps.content",
    "apps.integrations",
    "apps.editorial",
    "apps.radar",
    #   "apps.integrations",
    #   "apps.ops",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

TENANT_MODEL = "accounts.Tenant"
TENANT_DOMAIN_MODEL = "accounts.Domain"

# O `migrate_schemas`, ao migrar TODOS os tenants, nao confere se o schema
# existe no banco — so o caminho com `--schema=<nome>` confere. Com
# `auto_create_schema = False` (ADR-0001) existe uma janela em que a linha do
# tenant ja existe e o schema ainda nao, e um unico registro nessa janela
# derruba o comando inteiro com um erro que nao nomeia o tenant. Este executor
# filtra a lista e informa o que ficou de fora. Ver apps/accounts/migration_executors.py.
GET_EXECUTOR_FUNCTION = "apps.accounts.migration_executors.get_executor"

# A extensao `vector` do pgvector e criada UMA VEZ no schema `extensions` e
# alcancada por todos os tenants via search_path. Sem isto, a migration do
# segundo tenant falha com: type "vector" does not exist.
PG_EXTRA_SEARCH_PATHS = ["extensions"]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    # Primeiro de todos, para que TODA linha de log da requisicao — inclusive
    # as emitidas por middlewares seguintes — carregue o identificador.
    "apps.ops.middleware.RequestIDMiddleware",
    # ANTES da resolucao de tenant: o balanceador e o orquestrador consultam
    # por IP ou nome interno, que nunca corresponde ao dominio de um cliente.
    # Depois do TenantMainMiddleware, as sondas devolveriam 404 e a
    # infraestrutura concluiria que a aplicacao esta morta.
    "apps.ops.middleware.HealthCheckMiddleware",
    # Resolve o subdominio -> tenant e fixa o search_path da conexao.
    # Subclasse do TenantMainMiddleware: identica em producao, e em DEBUG
    # troca o 404 `No tenant for hostname` por um redirect (host errado) ou
    # por um erro que nomeia o comando que falta (`bootstrap_public`).
    "apps.accounts.middleware.TenantResolutionMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Precisa vir DEPOIS do AuthenticationMiddleware: depende de request.user.
    # Como os usuarios sao compartilhados e o cookie de sessao vale para todos
    # os subdominios, sem isto qualquer pessoa autenticada entraria no painel
    # de qualquer tenant apenas digitando o subdominio.
    "apps.accounts.middleware.TenantAccessMiddleware",
]

# O roteamento difere entre a home (apex) e um tenant (subdominio): a home tem
# cadastro e landing; o tenant tem o painel.
ROOT_URLCONF = "core.urls_tenants"
PUBLIC_SCHEMA_URLCONF = "core.urls_public"

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Contagens de pendencia no menu do tenant. Sai calado no
                # dominio raiz, onde as tabelas do tenant nem existem.
                "apps.ops.context.pendencias",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
# O ENGINE e o backend do django-tenants, nao o do Django: e ele que emite o
# `SET search_path` a cada uso de conexao.
DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": env.get("POSTGRES_DB", PROJECT_SLUG),
        "USER": env.get("POSTGRES_USER", PROJECT_SLUG),
        "PASSWORD": env.require("POSTGRES_PASSWORD"),
        "HOST": env.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": env.integer("POSTGRES_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# Direciona cada migration para o schema certo (public vs. tenant).
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Autenticacao
# ---------------------------------------------------------------------------
# Trocar isto depois do primeiro migrate exige cirurgia manual em
# django_content_type e auth_permission. Ver ADR-0006.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Internacionalizacao (ADR-0009)
# ---------------------------------------------------------------------------
# Codigo, nomes de campo e valores de choices em ingles; interface traduzida.
# O `locale/` existe desde o primeiro commit porque retrofitar gettext num
# painel ja escrito custa revarrer template por template.
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("pt-br", "Portugues (Brasil)"),
    ("en", "English"),
    ("it", "Italiano"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Arquivos estaticos e de midia
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
# Pode ficar fora da pasta do projeto (um storage centralizado no servidor).
# Mudando, mude junto o `alias` do /protected-media/ no Nginx e o
# ReadWritePaths dos servicos — ver docs/OPERACAO.md, "Midia fora do projeto".
MEDIA_ROOT = Path(env.get("MEDIA_ROOT", "") or BASE_DIR / "media")

# Cada tenant grava em MEDIA_ROOT/<schema_name>/... automaticamente. Com "%s"
# o schema_name e interpolado; trocar para S3 depois e so trocar a storage,
# mantendo o mesmo prefixo por tenant.
MULTITENANT_RELATIVE_MEDIA_ROOT = "%s"

STORAGES = {
    "default": {
        "BACKEND": "django_tenants.files.storage.TenantFileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# A capa escolhida precisa ser buscavel pelo site de destino: o contrato manda a
# imagem por REFERENCIA, e o no faz um GET nela. Quando o Nginx da frente tem o
# bloco `/protected-media/` (ver deploy/nginx/), a aplicacao delega o envio do
# arquivo a ele com X-Accel-Redirect em vez de streamar pelo worker.
#
# Desligado por padrao porque so funciona atras daquele Nginx; ligado sem ele, o
# navegador recebe uma resposta vazia com um cabecalho que ninguem interpreta.
USAR_X_ACCEL = env.boolean("USAR_X_ACCEL", False)
PREFIXO_X_ACCEL = env.get("PREFIXO_X_ACCEL", "/protected-media/")

# Esquema das URLs publicas montadas para terceiros (a capa que o site busca).
# Em desenvolvimento o dominio do tenant e http.
ESQUEMA_PUBLICO = env.get("ESQUEMA_PUBLICO", "https")

# Teto de upload. PDFs cientificos passam facil de 10 MB.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.integer("DATA_UPLOAD_MAX_MEMORY_SIZE", 52_428_800)
FILE_UPLOAD_MAX_MEMORY_SIZE = 5_242_880

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
# O broker pode ser Redis (producao) ou o proprio PostgreSQL (desenvolvimento).
# Ver ADR-0013 para o porque e para as diferencas semanticas entre os dois.
#
# `BROKER_BACKEND` aceita:
#   "redis"     -> usa CELERY_BROKER_URL, ou o default do Redis local
#   "postgres"  -> monta a URL a partir das MESMAS credenciais do banco
#                  principal, sem exigir nenhuma variavel a mais
#
# Montar a URL do Postgres aqui, em vez de pedir que o desenvolvedor a escreva
# no .env, evita a classe de erro mais chata deste arranjo: o broker apontando
# para um banco diferente do da aplicacao, sem nenhum sintoma alem de tasks que
# somem.


def _postgres_broker_url() -> str:
    """URL do transporte `sqla` a partir das credenciais do banco principal."""
    from urllib.parse import quote

    usuario = env.get("POSTGRES_USER", PROJECT_SLUG)
    senha = quote(env.require("POSTGRES_PASSWORD"), safe="")
    host = env.get("POSTGRES_HOST", "127.0.0.1")
    porta = env.get("POSTGRES_PORT", "5432")
    banco = env.get("POSTGRES_DB", PROJECT_SLUG)
    return f"sqla+postgresql+psycopg://{usuario}:{senha}@{host}:{porta}/{banco}"


# O nome NAO pode comecar com "CELERY_": o `config_from_object(namespace="CELERY")`
# retira esse prefixo e repassa a chave ao Celery. `CELERY_BROKER` viraria
# `broker`, que o Celery interpreta como URL — e o worker sobe tentando falar
# AMQP com um host chamado "postgres". Descoberto na pratica, subindo o worker.
BROKER_BACKEND = env.get("BROKER_BACKEND", "redis").strip().lower()

if BROKER_BACKEND == "postgres":
    CELERY_BROKER_URL = _postgres_broker_url()
    # Forcado, e nao apenas um default: se `CELERY_RESULT_BACKEND` do .env
    # continuasse valendo aqui, quem trocasse so o broker ficaria com o
    # resultado ainda no Redis — meio migrado, ainda exigindo o servico que se
    # queria evitar, e sem nenhum sintoma que denunciasse isso.
    #
    # `django-db` grava numa tabela do proprio Django (django-celery-results):
    # mesma conexao, mesmas migrations, visivel no admin.
    CELERY_RESULT_BACKEND = "django-db"

    # O Celery le `os.environ` ANTES do settings do Django. De celery/app/utils.py:
    #
    #     def broker_url(self):
    #         return (os.environ.get('CELERY_BROKER_URL') or
    #                 self.first('broker_url', 'broker_host'))
    #
    # Como o python-dotenv injeta o `.env` em os.environ, um `CELERY_BROKER_URL`
    # deixado la vence silenciosamente tudo o que se configure aqui: o settings
    # diz Postgres, o worker conecta no Redis, e nada denuncia a divergencia.
    #
    # Por isso as variaveis sao reescritas para concordar com a decisao tomada
    # acima, em vez de disputada com ela.
    os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL
    os.environ["CELERY_RESULT_BACKEND"] = CELERY_RESULT_BACKEND
else:
    CELERY_BROKER_URL = env.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    CELERY_RESULT_BACKEND = env.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# O default do Celery e 3 tentativas saturando em 10 minutos — bem menos do que
# a arquitetura promete. Estes valores dao ~300s, 600s, 1200s... saturando em
# 6h, o que cobre cerca de 3 dias de indisponibilidade de um No Final.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_DEFAULT_RETRY_DELAY = 300
CELERY_TASK_MAX_RETRIES = 20

# O default e 4: mesmo com --concurrency=1 o worker reserva 4 mensagens, e as
# 3 extras ficam presas contando para o visibility_timeout.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# `visibility_timeout` e uma opcao EXCLUSIVA do transporte Redis, e passa-la a
# outro transporte quebra o worker na inicializacao: o transporte `sqla`
# repassa transport_options direto ao create_engine() do SQLAlchemy, que
# rejeita argumentos que nao conhece com TypeError.
#
# Por isso a opcao e aplicada condicionalmente. Nao e cosmetico: e a prova de
# que os transportes tem superficies de configuracao diferentes, e de que
# "trocar o broker" nunca e uma troca neutra.
#
# Sobre o valor: o default do Redis e 3600s, entao uma inferencia mais longa
# que isso faz o broker REENTREGAR a mesma task — dois artigos gerados, GPU
# gasta em dobro. Isto e paliativo; a garantia real vem do GenerationJob no
# banco, que e a fonte da verdade e independe do transporte.
CELERY_BROKER_TRANSPORT_OPTIONS: dict[str, object] = {}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS: dict[str, object] = {}

# Prefixo de TODAS as chaves no Redis. Existe porque um Redis costuma ser
# compartilhado entre projetos, e o Celery, sem prefixo, usa nomes genericos:
# a fila padrao e a chave `celery`, e as mensagens em voo ficam em `unacked`.
#
# Duas aplicacoes na mesma base do Redis, ambas sem prefixo, leem a MESMA fila.
# O sintoma nao e um erro claro: o worker do outro projeto retira uma task
# desta aplicacao, nao conhece o nome dela e a descarta. O trabalho some sem
# rastro, e o log que explicaria isso esta no servidor do outro projeto.
#
# Separar por numero de base (`/0`, `/1`) resolveria em parte e depende de
# ninguem repetir o numero — e `FLUSHDB` de um projeto ainda levaria o outro
# junto. O prefixo e explicito e independe de combinacao.
#
# O backend de resultados precisa da sua propria opcao: ele nao le a do broker.
# O prefixo terminado em `:` e mantido como esta; sem um separador ao final, o
# Celery acrescenta `_`.
REDIS_NAMESPACE = env.get("REDIS_NAMESPACE", "publibot")

if CELERY_BROKER_URL.startswith(("redis://", "rediss://", "sentinel://")):
    CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] = env.integer(
        "CELERY_VISIBILITY_TIMEOUT", 86_400
    )
    if REDIS_NAMESPACE:
        CELERY_BROKER_TRANSPORT_OPTIONS["global_keyprefix"] = f"{REDIS_NAMESPACE}:"

if REDIS_NAMESPACE and CELERY_RESULT_BACKEND.startswith(("redis://", "rediss://")):
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS["global_keyprefix"] = f"{REDIS_NAMESPACE}:"

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# O beat tem poucas entradas fixas, todas de INFRAESTRUTURA. A cadencia de cada
# site vive no banco, em PublicationSchedule: o beat da o batimento cardiaco, o
# banco da a cadencia. Fosse ao contrario, mudar o horario de um cliente
# exigiria implantacao.
CELERY_BEAT_SCHEDULE = {
    "tick-publication-scheduler": {
        "task": "apps.integrations.tasks.tick_publication_scheduler",
        "schedule": 60.0,
        # Descarta o tique se o worker estiver ocupado: um acumulo de tiques
        # atrasados nao ajuda, e a proxima execucao pega tudo de qualquer forma
        # (a consulta e por `<=`, nao por igualdade de minuto).
        "options": {"expires": 55},
    },
    "check-publication-buffer": {
        "task": "apps.integrations.tasks.check_publication_buffer",
        "schedule": 900.0,
        "options": {"expires": 600},
    },
    "sweep-stalled-jobs": {
        "task": "apps.ops.tasks.sweep_stalled_jobs",
        "schedule": 300.0,
        "options": {"expires": 240},
    },
    "release-expired-leases": {
        "task": "apps.ops.tasks.release_expired_leases",
        "schedule": 300.0,
        "options": {"expires": 240},
    },
    "tick-radar": {
        "task": "apps.radar.tasks.tick_radar",
        # De hora em hora; a intensidade de cada tenant decide se roda.
        "schedule": 3600.0,
        "options": {"expires": 3000},
    },
    "colher-fila-do-radar": {
        "task": "apps.radar.tasks.colher_fila",
        # A fila padrao da DataForSEO devolve em minutos; colher e gratuito.
        "schedule": 300.0,
        "options": {"expires": 240},
    },
    "purge-expired-questions": {
        "task": "apps.integrations.tasks.purge_expired_questions",
        # Uma vez por dia: e uma obrigacao de retencao, nao algo urgente.
        "schedule": 86_400.0,
    },
}

# ---------------------------------------------------------------------------
# Interruptor geral de publicacao
# ---------------------------------------------------------------------------
# Checado no inicio de toda task que faz POST para um No Final. E a unica forma
# de parar uma publicacao equivocada sem derrubar servico.
PUBLISHING_ENABLED = env.boolean("PUBLISHING_ENABLED", True)

# Monta o payload completo e grava a tentativa, mas nao faz o POST. E o unico
# jeito seguro de validar o contrato contra um site real em desenvolvimento.
PUBLISH_DRY_RUN = env.boolean("PUBLISH_DRY_RUN", False)

# ---------------------------------------------------------------------------
# Recuperacao (RAG)
# ---------------------------------------------------------------------------
# multilingual-e5-large: 1024 dimensoes, MIT, ~100 idiomas, roda em CPU na
# nuvem via ONNX. Mesma dimensao do bge-m3, entao trocar de modelo depois nao
# exige ALTER TYPE nem recriar o indice HNSW — so re-embutir o corpus.
# Aceitar PDF sem o worker de conversao. O caminho local usa o pypdf, que le a
# camada de texto sem interpretar a estrutura da pagina — coluna, cabecalho,
# rodape e tabela viram texto corrido — e nao ve nada num PDF digitalizado.
#
# Fica ligado por padrao para o sistema ser testavel antes de a maquina com GPU
# existir, e DESLIGADO em producao (ver prod.py): la, indexar texto lido assim
# significaria publicar citando uma fonte cujo conteudo foi lido errado.
PERMITIR_EXTRACAO_LOCAL = env.boolean("PERMITIR_EXTRACAO_LOCAL", True)

# ---------------------------------------------------------------------------
# Semente da conexao de inferencia
# ---------------------------------------------------------------------------
# Estes valores NAO sao a fonte da verdade: a conexao vive numa linha do banco,
# para que trocar de modelo ou de endereco nao exija implantacao (ADR-0012).
#
# Eles existem para o `manage.py configurar_inferencia` conseguir criar essa
# linha numa instalacao nova. Sem isso, o unico caminho e um formulario no
# admin — e esquece-lo faz a aplicacao subir inteira e so falhar dentro do
# primeiro job, com o erro longe da causa.
INFERENCIA_NOME = env.get("INFERENCIA_NOME", "LLM principal")

# O WORKER de GPU, e nao mais o Ollama direto.
#
# O worker e o arbitro da placa: texto, imagem e conversao passam por ele e
# se revezam num lock so. Enquanto cada cliente falava com o Ollama direto,
# ninguem sabia quando a placa estava ocupada — e a geracao de imagem
# encontrava a VRAM cheia, caindo para CPU em silencio.
#
# As tres URLs (`INFERENCIA_`, `CONVERSAO_`, `IMAGEM_`) apontam para o MESMO
# endereco agora. Continuam separadas porque um dia uma delas pode ser um
# provedor pago, e porque a reserva por maquina do `leases.py` usa o host de
# cada uma.
#
#   dev :  http://127.0.0.1:8090
#   prod:  http://<nome-ou-ip-tailscale>:8090
INFERENCIA_BASE_URL = env.get("INFERENCIA_BASE_URL", "")

# Nome exato do `ollama list`. Um nome que nao existe no servidor devolve 404
# em toda chamada, e a mensagem do Ollama nao diz que o problema e o nome.
INFERENCIA_MODELO = env.get("INFERENCIA_MODELO", "")

# O segredo do worker (`WORKER_SHARED_SECRET` no `.env` dele). Viaja como
# `Authorization: Bearer`. Um provedor pago usa este mesmo campo para a chave
# da conta.
INFERENCIA_API_KEY = env.get("INFERENCIA_API_KEY", "")

# Uma inferencia por vez, por padrao. Nao e ajuste de desempenho: numa placa de
# 8 GB duas chamadas simultaneas estouram a VRAM e o Ollama cai em SILENCIO
# para CPU — dezenas de vezes mais lento, sem erro nenhum no log.
INFERENCIA_CONCORRENCIA = env.integer("INFERENCIA_CONCORRENCIA", 1)

# Precisa ser maior que a inferencia mais longa esperada. Menor que isso, duas
# tarefas passam a disputar a mesma placa.
INFERENCIA_RESERVA_SEGUNDOS = env.integer("INFERENCIA_RESERVA_SEGUNDOS", 3600)

# ---------------------------------------------------------------------------
# Semente da conexao de conversao (PDF -> texto)
# ---------------------------------------------------------------------------
# Mesma logica da conexao de inferencia: a fonte da verdade e uma linha no
# banco, e isto aqui so existe para o `manage.py configurar_conversao` criar
# essa linha sem ninguem abrir o admin.
#
# Quem converte e o worker de GPU — outro repositorio, na maquina da placa —
# e a conversa e HTTP. Em desenvolvimento aponta para a propria maquina; em
# producao, para o endereco que o Tailscale da a ela. A VM da nuvem nunca
# converte: ela so faz a requisicao.
CONVERSAO_NOME = env.get("CONVERSAO_NOME", "Conversao de PDF")
CONVERSAO_BASE_URL = env.get("CONVERSAO_BASE_URL", "")

# O mesmo valor de `WORKER_SHARED_SECRET` no `.env` do worker. Sem ele o worker
# devolve 401 e a conversao falha sem dizer que o problema e credencial.
CONVERSAO_SEGREDO = env.get("CONVERSAO_SEGREDO", "")

# ---------------------------------------------------------------------------
# Semente da conexao de imagem de capa
# ---------------------------------------------------------------------------
# Terceira conexao, mesma logica das duas acima: a linha no banco e a fonte da
# verdade, e isto so existe para o `manage.py configurar_imagem` cria-la.
#
# Vazio e um estado legitimo e comum: sem gerador de imagem o artigo sai
# igual, apenas sem capa, e o ultimo passo da geracao registra o motivo em vez
# de falhar. O Ollama NAO serve aqui — ele nao gera imagem.
#
# Quando preenchido, aponta para o MESMO endereco da conversao: hoje as duas
# rotas moram no mesmo worker, que arbitra a placa com um lock so. A reserva
# daqui conta vagas por maquina e continua valendo — ela evita mandar um
# pedido que ja se sabe que vai voltar 503.
IMAGEM_NOME = env.get("IMAGEM_NOME", "Geracao de imagem")
IMAGEM_BASE_URL = env.get("IMAGEM_BASE_URL", "")

# Nome do modelo. Para o worker proprio e informativo (quem manda e o
# `IMAGEM_MODELO` do `.env` dele); para um provedor pago, e o que seleciona o
# modelo de fato.
IMAGEM_MODELO = env.get("IMAGEM_MODELO", "stabilityai/stable-diffusion-xl-base-1.0")

# O mesmo `WORKER_SHARED_SECRET` do worker, ou a chave do provedor pago. Viaja
# como `Authorization: Bearer`, que e o que o dialeto de imagem da OpenAI usa.
IMAGEM_SEGREDO = env.get("IMAGEM_SEGREDO", "")

# Tamanho da capa, em pixels, no formato do dialeto da OpenAI.
#
# Viaja no pedido, entao mora AQUI e nao no `.env` do worker — trocar no lugar
# errado nao da erro, so nao muda nada.
#
# **Decide qualidade, nao so formato.** O SDXL foi treinado numa grade de
# PROPORCOES a area quase constante (~1,05 MP). Fora dela ele nao recusa: ele
# duplica o assunto, torce a geometria e perde a composicao, sem erro nenhum.
# A grade inteira (~40 formatos) e publicada pelo worker em
# `/health/` -> `imagem.grade`, e `configurar_imagem --testar` confere este
# valor contra ELA — nao contra uma copia daqui, que envelheceria calada.
#
# `1344x704` (1.909:1) e o padrao porque e o formato da grade mais proximo do
# que as redes pedem no `og:image`: 1200x630, ou 1.905:1. Publicar nessa
# proporcao evita que o corte automatico decida o enquadramento por voce.
#
# `1200x630` em si NAO passa, e nao e pela grade: 630 nao e multiplo de 16, e
# o worker recusa com 422 — o modelo arredondaria por dentro e devolveria outro
# tamanho sem avisar.
#
# Outros formatos uteis da grade: `1024x1024` (quadrado), `1344x768` (1.75:1),
# `1536x640` (panoramico).
#
# Tamanho quase nao muda o tempo: medido, 4x mais pixels custaram 23% mais
# relogio, porque o custo dominante e mover pesos entre RAM e VRAM. Nao ha
# economia em pedir pequeno — so perda de qualidade.
IMAGEM_TAMANHO = env.get("IMAGEM_TAMANHO", "1344x704")

# Quanto o cliente espera por um lote, em segundos.
#
# Precisa ser MAIOR que o prazo duro do worker (`imagem.tempo_travado` no
# `/health/`, 900s), e nao igual. Passado esse prazo o worker responde 500
# `worker_travado` e se encerra — mas so se o cliente ainda estiver ouvindo.
# Com os 300s de antes, quem desistia primeiro era este lado, e um worker
# travado virava um timeout indistinguivel de "demorou um pouco".
IMAGEM_TIMEOUT = env.integer("IMAGEM_TIMEOUT", 960)

# Prompt negativo, em ingles. Vazio nao viaja.
#
# Desde o contrato 2.6 o worker nao tem negativo proprio: o que for mandado
# aqui e o unico que vale. Modelos destilados (Z-Image-Turbo, FLUX schnell)
# rodam com guidance 0 e o ignoram — veja `imagem.guidance` no `/health/`.
IMAGEM_NEGATIVO = env.get("IMAGEM_NEGATIVO", "")

# ---------------------------------------------------------------------------
# Onde mora o worker de GPU, quando ele esta nesta maquina
# ---------------------------------------------------------------------------
# So o `manage.py dev` usa: ele sobe o worker junto quando o checkout esta
# aqui e a porta esta livre. Em producao isto fica vazio — la o worker roda na
# maquina da placa, como unit propria, e a nuvem so faz requisicao HTTP.
#
# Um caminho e nao um palpite: o worker e OUTRO repositorio, e adivinhar
# `../worker-gpu` acertaria na maquina de quem escreveu e erraria nas demais.
WORKER_GPU_DIR = env.get("WORKER_GPU_DIR", "")

EMBEDDING_MODEL = env.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EMBEDDING_DIM = env.integer("EMBEDDING_DIM", 1024)

# O modelo trunca em 512 tokens SEM AVISO. Este teto e validado com o
# tokenizer real na curadoria, nunca por contagem de caracteres.
EMBEDDING_MAX_TOKENS = env.integer("EMBEDDING_MAX_TOKENS", 480)

# Onde o modelo e carregado. O download tem cerca de 2 GB e acontece uma vez.
EMBEDDING_CACHE_DIR = env.get("EMBEDDING_CACHE_DIR", str(BASE_DIR / ".model_cache"))

# O cache do HuggingFace guarda cada arquivo em `blobs/<2 primeiros digitos do
# hash>/` e deixa no diretorio do modelo apenas um LINK SIMBOLICO para la. Dois
# arquivos do mesmo modelo caem, portanto, em pastas diferentes.
#
# Isso quebra o carregamento do ONNX. O modelo de embedding vem em duas partes
# — `model.onnx` e os 2 GB de pesos em `model.onnx_data` — e desde a versao
# 1.22 o onnxruntime valida o caminho da segunda: ele resolve o link da
# primeira, adota a pasta resultante como a unica permitida, e recusa a segunda
# por estar fora dela:
#
#   FAIL : External data path validation failed ... path escapes model directory
#   resolved path: ".model_cache/blobs/9e/9eac14..."
#   allowed directory: ".model_cache/blobs/29"
#
# Encontrado numa instalacao nova. Nao aparece em toda maquina: o fastembed
# tenta primeiro o proprio CDN, que entrega os arquivos lado a lado, e so cai
# no HuggingFace quando aquele falha. Ou seja, funciona ate o dia em que o
# download tomar o outro caminho — e ai a mensagem fala de "external data",
# nunca de cache.
#
# Com os links desligados, o HuggingFace escreve arquivos de verdade lado a
# lado e o carregamento passa. Verificado nas duas direcoes: com link, o erro
# acima; com arquivo real no mesmo lugar, a sessao abre.
#
# Precisa ser aqui. O `huggingface_hub` le esta variavel UMA VEZ, no import, e
# guarda numa constante de modulo: definida depois, nao tem efeito nenhum.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# Impede que o fastembed tente o HuggingFace quando o modelo ja esta em cache.
# Util em rede restrita e em CI.
EMBEDDING_LOCAL_FILES_ONLY = env.boolean("EMBEDDING_LOCAL_FILES_ONLY", False)

# Trocavel para `apps.knowledge.embeddings.FakeEmbeddingClient` nos testes, que
# evita carregar 2 GB de modelo a cada execucao da suite.
EMBEDDING_CLIENT = env.get("EMBEDDING_CLIENT", "apps.knowledge.embeddings.FastEmbedClient")

# Licencas cujo texto integral e APAGADO ao concluir a curadoria. Vazia por
# padrao: o sistema guarda tudo.
#
# O descarte existia embutido no codigo, com a justificativa de que o Brasil nao
# tem fair use e a citacao de pequeno trecho (Art. 46 VIII da Lei 9.610) nao
# cobre guardar a obra inteira. Continua sendo verdade e continua sendo uma
# decisao de quem opera o acervo, nao do software — que nao tem como saber que
# acordo existe com cada editora.
#
# O efeito e irreversivel: sem `markdown_full` nao ha como remarcar blocos sem
# reenviar o arquivo. Valores possiveis sao os codigos de `Document.License`:
# cc_by, cc_by_nc, open_access, proprietary, own, unknown.
LICENCAS_QUE_DESCARTAM_TEXTO_INTEGRAL = env.csv_list("LICENCAS_QUE_DESCARTAM_TEXTO_INTEGRAL")

RAG_TOP_K = env.integer("RAG_TOP_K", 3)

# MEDIDO, nao herdado de recomendacao generica.
#
# Com `multilingual-e5-large`, as distancias de cosseno se concentram numa
# faixa estreita. Medicao real feita neste projeto, para a consulta
# "monitoramento de pressao alta na gravidez":
#
#   passagem PT relevante ......... 0.1193
#   passagem EN relevante ......... 0.1445   (cross-lingual funciona)
#   passagem PT tangente .......... 0.1678
#   passagem de outra area ........ 0.1911
#   passagem absurda (bolo) ....... 0.2004
#
# Um limiar de 0.35 deixaria passar TODAS, inclusive a receita de bolo — o
# filtro seria decorativo. Modelos da familia e5 comprimem a faixa de
# similaridade; a distancia absoluta nao e comparavel entre modelos, e por isso
# este valor precisa ser recalibrado sempre que EMBEDDING_MODEL mudar.
#
# 0.16 separa as passagens relevantes das demais nessa amostra. E um ponto de
# partida, nao uma verdade: recalibre com o corpus real usando
# `manage.py calibrate_retrieval`.
RAG_MAX_COSINE_DISTANCE = env.decimal("RAG_MAX_COSINE_DISTANCE", 0.16)

# Busca hibrida: a textual (termo exato) corre ao lado da vetorial, e os
# resultados se fundem por posicao (RRF). O limiar acima continua sendo a
# trava; o trecho que casa por texto ganha so esta folga sobre ele.
RAG_BUSCA_HIBRIDA = env.boolean("RAG_BUSCA_HIBRIDA", True)
RAG_FOLGA_TEXTUAL = env.decimal("RAG_FOLGA_TEXTUAL", 0.03)

# Cross-encoder que reordena os candidatos antes do corte. Vazio: desligado.
# Sugestao: jinaai/jina-reranker-v2-base-multilingual (~1,1 GB, multilingue —
# o acervo mistura fontes em ingles com pautas em portugues).
RAG_RERANKER_MODEL = env.get("RAG_RERANKER_MODEL", "")

# ---------------------------------------------------------------------------
# Radar de pautas
# ---------------------------------------------------------------------------
# Buscador gratuito padrao: uma instancia do SearXNG com a saida JSON ligada
# (`search.formats: [html, json]` no settings.yml dele). Cada tenant pode
# apontar outra na tela do radar.
SEARXNG_URL = env.get("SEARXNG_URL", "")

# O teto mensal que um tenant pode escolher, em US$. As contas pagas sao do
# proprio tenant; isto so impede que um valor digitado errado vire fatura.
RADAR_TETO_MAXIMO_USD = env.decimal("RADAR_TETO_MAXIMO_USD", 20.0)

# Nota minima (0 a 100) para um grupo de demanda virar pauta sugerida sozinho.
RADAR_NOTA_MINIMA = env.decimal("RADAR_NOTA_MINIMA", 40.0)

# Distancia de cosseno abaixo da qual dois sinais sao o mesmo tema.
RADAR_DISTANCIA_DO_GRUPO = env.decimal("RADAR_DISTANCIA_DO_GRUPO", 0.10)

# Conta de servico do Google usada no Search Console: o arquivo JSON baixado
# em Google Cloud > IAM > Contas de servico > Chaves. Cada cliente adiciona o
# e-mail dela como usuario (leitura) da propriedade — sem OAuth, sem app a
# verificar. Vazio: a integracao fica desligada.
GSC_CONTA_DE_SERVICO_ARQUIVO = env.get("GSC_CONTA_DE_SERVICO_ARQUIVO", "")

# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.ops.middleware.FiltroDeRequestID"},
    },
    "formatters": {
        "verbose": {
            # O request_id permite ligar o erro de um worker a requisicao que o
            # originou. Sem ele, diagnosticar vira correlacionar horarios na mao.
            "format": "{levelname} {asctime} [{request_id}] {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": env.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        PROJECT_SLUG: {"level": env.get("LOG_LEVEL", "INFO"), "propagate": True},
    },
}

DEFAULT_FROM_EMAIL = env.get("DEFAULT_FROM_EMAIL", f"nao-responda@{ROOT_DOMAIN}")
