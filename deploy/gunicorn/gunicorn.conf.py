"""Configuracao do Gunicorn."""

import multiprocessing
import os

# Socket Unix em vez de porta TCP: evita passar pela pilha de rede entre Nginx
# e aplicacao, e as permissoes do arquivo controlam quem pode falar com o app.
bind = os.environ.get("GUNICORN_BIND", "unix:/run/publibot/publibot.sock")

workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
threads = int(os.environ.get("GUNICORN_THREADS", 2))

# Maior que o tempo de leitura do cliente HTTP (30s) com folga: o worker nao
# pode ser morto no meio de uma chamada legitima a um site externo.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
graceful_timeout = 30

# Recicla o processo periodicamente. `jitter` evita que todos reciclem juntos,
# o que causaria uma janela sem capacidade.
max_requests = 1000
max_requests_jitter = 100

# Precisa ser MAIOR que o keepalive do Nginx, senao o Gunicorn fecha a conexao
# enquanto o Nginx ainda a considera aberta e o cliente recebe 502.
keepalive = 75

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Inclui o identificador de requisicao repassado pelo Nginx, para o log do
# servidor poder ser cruzado com o da aplicacao.
access_log_format = '%({x-request-id}i)s %(h)s "%(r)s" %(s)s %(b)s %(D)sus'

# Carrega a aplicacao antes de bifurcar: economiza memoria entre os workers.
preload_app = True

proc_name = "publibot"
