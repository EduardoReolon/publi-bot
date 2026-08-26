# ADR-0013 — Broker do Celery no PostgreSQL em desenvolvimento

**Status:** Aceito
**Data:** 2026-08-26

## Contexto

A maquina de desenvolvimento e Windows. **Nao existe build oficial de Redis
para Windows** — as alternativas sao Memurai (comercial), WSL, ou um container.
Nenhuma delas e gratuita em esforco, e todas adicionam um servico a manter numa
maquina que ja tem PostgreSQL instalado e em uso por varios projetos.

Uma afirmacao anterior desta conversa, de que instalar Redis no Windows seria
trivial ("um `redis-server.exe`"), estava **factualmente errada** e levou a
descartar cedo demais uma restricao legitima.

Producao roda Linux, onde Redis e um `apt install`, e continua sendo a escolha
certa la: e o broker que o Celery melhor suporta, e o mesmo Redis serve de
cache e de contador para o disjuntor por site do ADR-0012.

## Decisao

**A variavel `BROKER_BACKEND` escolhe o transporte:**

| Valor | Efeito |
|---|---|
| `redis` (padrao) | `CELERY_BROKER_URL`, ou `redis://127.0.0.1:6379/0` |
| `postgres` | Transporte `sqla` do Kombu sobre o **mesmo banco da aplicacao**, com resultados em `django-db` |

Em modo `postgres` a URL e **montada a partir das credenciais que ja existem**
(`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_DB`), em vez de pedir uma variavel a mais. Isso elimina o erro mais
chato deste arranjo: o broker apontando para um banco diferente do da
aplicacao, cujo unico sintoma sao tarefas que somem.

Dependencias: `SQLAlchemy` fica em `requirements-dev.txt` (producao nao
instala); `django-celery-results` fica em `requirements.txt`, porque o app
entra em `INSTALLED_APPS` independentemente do broker.

## Duas armadilhas encontradas ao implementar

Ambas foram descobertas rodando o worker, nao lendo documentacao — e ambas
falhariam em silencio ou com mensagem enganosa.

**1. O nome da variavel nao pode comecar com `CELERY_`.**
`config_from_object(namespace="CELERY")` retira esse prefixo e repassa a chave
ao Celery. Uma variavel `CELERY_BROKER` vira `broker`, que o Celery interpreta
como URL: o worker subia tentando falar AMQP com um host chamado `postgres`
(`amqp://guest:**@postgres:5672//`). Dai o nome `BROKER_BACKEND`.

**2. O Celery le `os.environ` ANTES do settings do Django.**
De `celery/app/utils.py`:

```python
@property
def broker_url(self):
    return (os.environ.get('CELERY_BROKER_URL') or
            self.first('broker_url', 'broker_host'))
```

Como o `python-dotenv` injeta o `.env` em `os.environ`, um `CELERY_BROKER_URL`
deixado la vence tudo o que o settings configure. O settings dizia Postgres, o
`app.conf.get('broker_url')` retornava Postgres, e `app.conf.broker_url`
retornava Redis. Por isso o modo `postgres` **reescreve** as duas variaveis em
`os.environ`, para que concordem com a decisao em vez de disputa-la.

**3. `visibility_timeout` e exclusivo do Redis.** O transporte `sqla` repassa
`transport_options` direto ao `create_engine()` do SQLAlchemy, que rejeita
argumentos desconhecidos com `TypeError` na inicializacao. A opcao passou a ser
aplicada condicionalmente ao transporte.

## O que este modo NAO reproduz

**Este transporte nao vai para producao.** As diferencas nao sao de desempenho,
sao de semantica:

- **Sem `visibility_timeout`.** Toda a analise sobre reentrega de mensagem
  reservada — o risco de gerar o mesmo artigo duas vezes e gastar GPU em dobro
  — simplesmente nao se exercita aqui. E um comportamento especifico do Redis
  que so aparece testando contra Redis.
- **Latencia por polling.** Medido: ida-e-volta em torno de 2100 ms contra 3 ms
  no Redis. Irrelevante para este produto, cujas tarefas reais levam dezenas de
  segundos (inferencia, conversao de PDF), mas e uma diferenca real.
- **O transporte `sqla` do Kombu tem bem menos uso em producao** que o de Redis,
  e recebe menos atencao.

A garantia que **nao** depende do transporte e a retomada de trabalho, porque
ela vive no `GenerationJob` no banco (ADR-0012). E por isso que trocar o broker
em desenvolvimento nao compromete o que mais importa.

**Verificado:** worker consumindo do PostgreSQL, propagacao de tenant correta
nos tres schemas, e o teste repetido **com o Redis desligado**, para provar que
nenhuma dependencia oculta permanecia.
