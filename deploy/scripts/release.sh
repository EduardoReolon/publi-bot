#!/usr/bin/env bash
#
# Implantacao de uma nova versao.
#
# A ordem importa: migrations ANTES de reiniciar a aplicacao. Se o codigo novo
# subisse antes das migrations, ele consultaria colunas que ainda nao existem
# durante a janela entre as duas operacoes.

set -euo pipefail

RAIZ="${PUBLIBOT_ROOT:-/srv/publibot}"
VENV="$RAIZ/venv"
cd "$RAIZ"

echo "==> Buscando codigo"
git fetch --all --prune
git checkout "${1:-main}"
git pull --ff-only

echo "==> Dependencias"
"$VENV/bin/pip" install -q -r requirements.txt

echo "==> Verificacoes"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py check --deploy

# Falha se algum model mudou sem a migration correspondente. Descobrir isso
# aqui e barato; descobrir em producao, com o servico ja reiniciado, nao.
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py makemigrations --check --dry-run

echo "==> Migrations (public e todos os tenants)"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py migrate_schemas

echo "==> Arquivos estaticos"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py collectstatic --noinput

echo "==> Traducoes"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py compilemessages 2>/dev/null || true

echo "==> Reiniciando servicos"
# A aplicacao recarrega sem derrubar o socket: as conexoes em curso terminam.
sudo systemctl reload publibot.service

# O worker precisa de restart, nao reload. `TimeoutStopSec` no unit e maior que
# a tarefa mais longa: com acks_late, matar no meio faz o broker reentregar a
# mensagem, e uma implantacao publicaria o mesmo conteudo duas vezes.
sudo systemctl restart celery-publibot.service
sudo systemctl restart celery-beat-publibot.service

echo "==> Conferindo saude"
sleep 3
for _ in $(seq 1 10); do
    if curl -sf -o /dev/null http://localhost/healthz/ -H "Host: ${ROOT_DOMAIN:-publibot.com.br}"; then
        echo "Aplicacao respondendo."
        exit 0
    fi
    sleep 2
done

echo "ERRO: a aplicacao nao respondeu apos a implantacao." >&2
exit 1
