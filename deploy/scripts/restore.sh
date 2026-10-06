#!/usr/bin/env bash
#
# Restauracao a partir de um dump.
#
#   ./restore.sh /var/backups/publibot/publibot-20260826-030000.dump

set -euo pipefail

ARQUIVO="${1:?informe o caminho do dump}"
[[ -f "$ARQUIVO" ]] || { echo "Arquivo nao encontrado: $ARQUIVO" >&2; exit 1; }

# shellcheck disable=SC1091
RAIZ="${PUBLIBOT_ROOT:-/home/ubuntu/publi-bot}"
# shellcheck source=deploy/scripts/ler_env.sh
source "$(dirname "${BASH_SOURCE[0]}")/ler_env.sh"
carregar_env "$RAIZ/.env"

echo "Isto vai SOBRESCREVER o banco '$POSTGRES_DB'."
read -r -p "Digite o nome do banco para confirmar: " confirmacao
[[ "$confirmacao" == "$POSTGRES_DB" ]] || { echo "Cancelado."; exit 1; }

echo "==> Parando servicos que escrevem no banco"
sudo systemctl stop celery-publibot.service celery-beat-publibot.service publibot.service

echo "==> Restaurando"
# `--clean --if-exists` remove os objetos antigos antes de recriar. Sem isso a
# restauracao falharia em tudo que ja existe.
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore \
    --host="${POSTGRES_HOST:-127.0.0.1}" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --clean --if-exists --no-owner \
    "$ARQUIVO"

echo "==> Reinstalando as extensoes"
# As extensoes vivem no schema `extensions`, fora do dump da aplicacao.
PGPASSWORD="$POSTGRES_PASSWORD" psql -h "${POSTGRES_HOST:-127.0.0.1}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE SCHEMA IF NOT EXISTS extensions;
     CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
     CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
     GRANT USAGE ON SCHEMA extensions TO $POSTGRES_USER;"

echo "==> Subindo servicos"
sudo systemctl start publibot.service celery-publibot.service celery-beat-publibot.service

echo "Restauracao concluida."
