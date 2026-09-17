#!/usr/bin/env bash
#
# Prepara um servidor NOVO para receber o PubliBot. Roda UMA vez por maquina.
#
#   sudo -u publibot ./deploy/scripts/bootstrap.sh
#
# A divisao entre este script e o `release.sh` e deliberada:
#
#   bootstrap.sh  o que acontece uma vez  — usuario, diretorios, venv, banco,
#                 segredos, units do systemd.
#   release.sh    o que acontece sempre   — codigo, dependencias, migrations,
#                 estaticos, reload.
#
# Juntar os dois pareceria mais simples e sairia caro: o bootstrap gera
# segredos e cria banco, e nada disso deve ser reexecutado a cada implantacao.
#
# E idempotente: rodar de novo confirma o que existe e nao refaz nada. A unica
# excecao deliberada e o arquivo de segredos, que NUNCA e sobrescrito — ver o
# comentario em "Segredos".
#
# O que este script NAO faz, de proposito:
#   - Nginx e TLS: sao da maquina, nao da aplicacao. Ver deploy/nginx/.
#   - Instalar PostgreSQL, Redis e Python: sao pre-requisitos do servidor.
#   - Subir o Ollama: ele roda em outra maquina, alcancada por Tailscale.

set -euo pipefail

RAIZ="${PUBLIBOT_ROOT:-/srv/publibot}"
VENV="$RAIZ/venv"
ETC="/etc/publibot"
ARQUIVO_ENV="$ETC/env"
USUARIO="${PUBLIBOT_USER:-publibot}"

cd "$RAIZ"

echo "==> Conferindo pre-requisitos"
faltando=()
command -v python3 >/dev/null || faltando+=("python3")
command -v psql    >/dev/null || faltando+=("postgresql-client")
command -v redis-cli >/dev/null || faltando+=("redis-tools")

if [[ ${#faltando[@]} -gt 0 ]]; then
    echo "ERRO: faltam pre-requisitos: ${faltando[*]}" >&2
    echo "  sudo apt install python3-venv postgresql redis-server" >&2
    exit 1
fi

# A versao importa: o projeto usa tomllib e sintaxe de 3.12.
versao_py="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$(printf '%s\n3.12\n' "$versao_py" | sort -V | head -1)" != "3.12" ]]; then
    echo "ERRO: Python $versao_py encontrado; o projeto exige 3.12 ou mais novo." >&2
    exit 1
fi
echo "  Python $versao_py"

echo "==> Usuario e diretorios"
if ! id -u "$USUARIO" >/dev/null 2>&1; then
    sudo useradd --system --home-dir "$RAIZ" --shell /usr/sbin/nologin "$USUARIO"
    echo "  usuario $USUARIO criado"
else
    echo "  usuario $USUARIO ja existe"
fi

sudo install -d -o "$USUARIO" -g "$USUARIO" -m 0755 "$RAIZ/media" "$RAIZ/staticfiles"
sudo install -d -o root -g "$USUARIO" -m 0750 "$ETC"
# O socket do Gunicorn vive aqui. `RuntimeDirectory` no unit recria a pasta a
# cada boot; esta linha cobre o intervalo ate o primeiro start.
sudo install -d -o "$USUARIO" -g www-data -m 0750 /run/publibot

echo "==> Ambiente virtual"
if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
    echo "  venv criado"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
echo "  dependencias instaladas"

# ---------------------------------------------------------------------------
# Segredos
# ---------------------------------------------------------------------------
# NUNCA sobrescreve um arquivo existente, e isso nao e excesso de zelo:
# `NODE_KEY_ENCRYPTION_KEY` cifra as credenciais dos sites dos clientes,
# guardadas no banco. Gerar uma chave nova por cima torna TODAS elas
# irrecuperaveis — e o erro so aparece na proxima publicacao, como falha de
# autenticacao contra o site do cliente.
echo "==> Segredos"
if [[ -f "$ARQUIVO_ENV" ]]; then
    echo "  $ARQUIVO_ENV ja existe e foi preservado"
else
    secret_key="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')"
    fernet="$("$VENV/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    senha_banco="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"

    sudo tee "$ARQUIVO_ENV" >/dev/null <<EOF
# Gerado por deploy/scripts/bootstrap.sh em $(date -Iseconds).
#
# ATENCAO: NODE_KEY_ENCRYPTION_KEY cifra as credenciais dos sites guardadas no
# banco. Perde-la torna todas irrecuperaveis. Inclua este arquivo no backup.
#
# Os valores abaixo marcados como AJUSTE sao os unicos que precisam de decisao
# humana. O resto foi gerado ou tem default razoavel.

DJANGO_SETTINGS_MODULE=core.settings.prod
DJANGO_SECRET_KEY=$secret_key
NODE_KEY_ENCRYPTION_KEY=$fernet

# AJUSTE: o dominio de verdade, e os hosts que a aplicacao aceita.
ROOT_DOMAIN=publibot.com.br
DJANGO_ALLOWED_HOSTS=publibot.com.br,.publibot.com.br

POSTGRES_DB=publibot
POSTGRES_USER=publibot
POSTGRES_PASSWORD=$senha_banco
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

BROKER_BACKEND=redis
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
# Prefixo de todas as chaves. O Redis deste servidor atende varios projetos;
# sem isto, dois deles leem a MESMA fila e um descarta as tarefas do outro.
REDIS_NAMESPACE=publibot

# AJUSTE: o endereco do Ollama na rede Tailscale, e o modelo carregado nele.
# Confira o nome exato com \`ollama list\` na maquina que hospeda o modelo.
INFERENCIA_BASE_URL=
INFERENCIA_MODELO=

PUBLISHING_ENABLED=True
# AJUSTE para False quando for publicar de verdade.
PUBLISH_DRY_RUN=True

USAR_X_ACCEL=true
ESQUEMA_PUBLICO=https
EOF

    sudo chown root:"$USUARIO" "$ARQUIVO_ENV"
    sudo chmod 0640 "$ARQUIVO_ENV"
    echo "  $ARQUIVO_ENV criado com segredos novos"
    echo "  AJUSTE ainda necessario: ROOT_DOMAIN, DJANGO_ALLOWED_HOSTS,"
    echo "                           INFERENCIA_BASE_URL, INFERENCIA_MODELO"
fi

# shellcheck disable=SC1090
set -a; source <(sudo cat "$ARQUIVO_ENV" | grep -E '^[A-Z_]+=') ; set +a

echo "==> PostgreSQL: papel, banco e extensoes"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
        CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
    ELSE
        ALTER ROLE ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';
    END IF;
END
\$\$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1; then
    sudo -u postgres createdb -O "${POSTGRES_USER}" "${POSTGRES_DB}"
    echo "  banco ${POSTGRES_DB} criado"
else
    echo "  banco ${POSTGRES_DB} ja existe"
fi

# A extensao vai num schema dedicado, NAO no public: com um schema por tenant,
# uma extensao so no public nao fica alcancavel ao criar o segundo tenant, e a
# migration falha com `type "vector" does not exist`.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${POSTGRES_DB}" <<SQL
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO ${POSTGRES_USER};
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
echo "  extensoes instaladas em 'extensions'"

echo "==> Units do systemd"
"$RAIZ/deploy/scripts/sincronizar-systemd.sh"

sudo systemctl enable publibot.socket publibot.service \
                      celery-publibot.service celery-beat-publibot.service
echo "  servicos habilitados no boot"

echo "==> Rotacao de log"
sudo install -m 0644 "$RAIZ/deploy/logrotate/publibot" /etc/logrotate.d/publibot

echo
echo "Bootstrap concluido."
echo
echo "Ainda falta, e so uma vez:"
echo "  1. Ajustar $ARQUIVO_ENV (os campos marcados AJUSTE)."
echo "  2. Nginx: cp deploy/nginx/publibot.conf /etc/nginx/sites-available/ e"
echo "     emitir o certificado TLS."
echo "  3. Rodar a primeira implantacao:  ./deploy/scripts/release.sh"
echo "  4. Criar o primeiro usuario:      $VENV/bin/python manage.py createsuperuser"
