#!/usr/bin/env bash
#
# Prepara PostgreSQL e Redis nativos para desenvolvimento.
#
# Este e o caminho padrao do projeto. Nada aqui exige container: no Ubuntu, o
# pgvector esta no repositorio oficial da distribuicao.
#
#   ./scripts/setup-db.sh
#
# Le POSTGRES_* do .env. Rode depois de copiar .env.example para .env.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERRO: .env nao encontrado. Rode antes:  cp .env.example .env" >&2
    exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

DB_NAME="${POSTGRES_DB:-publibot}"
DB_USER="${POSTGRES_USER:-publibot}"
DB_PASS="${POSTGRES_PASSWORD:?defina POSTGRES_PASSWORD no .env}"

# Descobre a versao do PostgreSQL instalada em vez de fixar um numero: o
# Ubuntu 24.04 traz a 16, versoes mais novas trazem outra.
PG_VERSION="$(pg_config --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)"
if [[ -z "$PG_VERSION" ]]; then
    echo "ERRO: PostgreSQL nao encontrado. Instale antes:" >&2
    echo "  sudo apt install postgresql postgresql-contrib redis-server" >&2
    exit 1
fi

echo "PostgreSQL ${PG_VERSION} detectado."

# O pgvector e um pacote por versao do servidor.
if ! ls "/usr/lib/postgresql/${PG_VERSION}/lib/vector.so" >/dev/null 2>&1; then
    echo "ERRO: pgvector ausente para o PostgreSQL ${PG_VERSION}. Instale com:" >&2
    echo "  sudo apt install postgresql-${PG_VERSION}-pgvector" >&2
    exit 1
fi
echo "pgvector presente."

echo "Criando papel e banco (idempotente)..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}' CREATEDB;
    ELSE
        ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';
    END IF;
END
\$\$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
    echo "Banco ${DB_NAME} criado."
else
    echo "Banco ${DB_NAME} ja existe."
fi

# A extensao vai num schema dedicado e NAO no public.
#
# Com um schema por tenant, uma extensao instalada apenas no public nao fica
# alcancavel da forma que as migrations esperam ao criar o segundo tenant. O
# search_path de toda conexao inclui `extensions` via PG_EXTRA_SEARCH_PATHS.
#
# Sem isto o primeiro tenant funciona e o segundo falha com:
#   type "vector" does not exist
echo "Instalando extensoes no schema 'extensions'..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO ${DB_USER};
CREATE EXTENSION IF NOT EXISTS vector   WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
DO \$\$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET search_path TO "\$user", public, extensions',
        current_database()
    );
END
\$\$;
SQL

# A mesma preparacao vai para o template1.
#
# Motivo: `vector` nao e uma extensao "trusted", entao cria-la exige
# superusuario — e o usuario da aplicacao NAO deve ser superusuario. Como o
# banco de teste e criado em tempo de execucao pelo usuario da aplicacao, ele
# nunca conseguiria instalar a extensao sozinho.
#
# Todo banco novo herda do template1, entao instalar ali resolve de uma vez o
# banco de teste do pytest e qualquer outro banco criado depois.
echo "Preparando template1 (para os bancos de teste herdarem)..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -d template1 <<SQL
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO ${DB_USER};
CREATE EXTENSION IF NOT EXISTS vector   WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
SQL

echo
sudo -u postgres psql -d "${DB_NAME}" -c \
  "SELECT extname, extversion, n.nspname AS schema
     FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE extname IN ('vector','unaccent');"

echo "Verificando Redis..."
if redis-cli ping >/dev/null 2>&1; then
    echo "Redis respondendo."
else
    echo "AVISO: Redis nao respondeu em 127.0.0.1:6379." >&2
    echo "  sudo apt install redis-server && sudo systemctl enable --now redis-server" >&2
fi

echo
echo "Pronto. Proximo passo:"
echo "  python manage.py migrate_schemas --shared"
echo "  python manage.py bootstrap_public"
