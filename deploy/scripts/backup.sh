#!/usr/bin/env bash
#
# Backup do banco e das midias.
#
# `--format=custom` permite restaurar tabelas isoladas e comprime sozinho. Um
# dump em texto puro so pode ser restaurado por inteiro.

set -euo pipefail

DESTINO="${BACKUP_DIR:-/var/backups/publibot}"
RETENCAO_DIAS="${BACKUP_RETENTION_DAYS:-14}"
CARIMBO="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DESTINO"

# shellcheck disable=SC1091
set -a; source /etc/publibot/env; set +a

echo "==> Banco"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    --host="${POSTGRES_HOST:-127.0.0.1}" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --format=custom \
    --file="$DESTINO/publibot-$CARIMBO.dump"

echo "==> Midias"
tar -czf "$DESTINO/media-$CARIMBO.tar.gz" -C /srv/publibot media

# A chave que cifra as credenciais dos clientes vive no .env, FORA do banco. Um
# backup do banco sem ela nao permite recuperar credencial nenhuma — e por isso
# que ela protege de verdade. Guarde-a separadamente, em cofre.
echo
echo "LEMBRETE: a NODE_KEY_ENCRYPTION_KEY nao esta neste backup, de proposito."
echo "Sem ela as credenciais cifradas sao irrecuperaveis. Guarde-a em cofre."

echo "==> Removendo backups com mais de $RETENCAO_DIAS dias"
find "$DESTINO" -name 'publibot-*.dump' -mtime "+$RETENCAO_DIAS" -delete
find "$DESTINO" -name 'media-*.tar.gz' -mtime "+$RETENCAO_DIAS" -delete

echo "Backup concluido: $DESTINO"
