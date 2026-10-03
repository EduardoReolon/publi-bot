#!/usr/bin/env bash
#
# Implantacao de uma nova versao. Idempotente: a primeira vez prepara o
# servidor, as seguintes so conferem o que ja existe e seguem.
#
#   bash <pasta-enviada>/deploy/scripts/release.sh
#
# Quem chama e o job `deploy` do CI, depois de copiar o codigo (e o `.env`,
# vindo do secret PRODUCTION_ENV_FILE) para a pasta de envio. Para implantar a
# mao, copie para a mesma pasta e rode este script de la.
#
# A ordem importa, e cada passo esta onde esta por um motivo:
#   1. pacotes e banco           — o que o resto pressupoe;
#   2. dependencias no venv      — ANTES de trocar o codigo: um requirements
#                                  que nao instala para aqui, com o codigo
#                                  antigo ainda no ar e inteiro;
#   3. troca do codigo (rsync)   — com --delete: arquivo apagado no git some
#                                  daqui tambem (uma migration removida que
#                                  ficasse no servidor seria aplicada);
#   4. migrations                — ANTES de reiniciar: senao o codigo novo
#                                  consulta colunas que ainda nao existem;
#   5. units, Nginx e reinicio.

set -euo pipefail

ENVIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAIZ="${PUBLIBOT_ROOT:-/home/ubuntu/publi-bot}"
VENV="$RAIZ/venv"
PYTHON_VERSAO="3.12"

if [[ "$ENVIO" == "$RAIZ" ]]; then
    echo "ERRO: rode a partir da pasta de envio, nao de $RAIZ." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
# Vem junto no envio, gerado do secret. Modo 600: tem a SECRET_KEY, a senha do
# banco e a NODE_KEY_ENCRYPTION_KEY. Sem ele no envio, vale o que ja existe.
mkdir -p "$RAIZ"
if [[ -f "$ENVIO/.env" ]]; then
    install -m 0600 "$ENVIO/.env" "$RAIZ/.env"
    rm -f "$ENVIO/.env"
fi
if [[ ! -f "$RAIZ/.env" ]]; then
    echo "ERRO: $RAIZ/.env nao existe. Cadastre o secret PRODUCTION_ENV_FILE." >&2
    exit 1
fi
# Lido como dados, e nao executado: comentarios e linhas soltas ficam de fora.
# shellcheck disable=SC1090
set -a; source <(grep -E '^[A-Z_][A-Z0-9_]*=' "$RAIZ/.env"); set +a
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-core.settings.prod}"

MIDIA="${MEDIA_ROOT:-$RAIZ/media}"

# ---------------------------------------------------------------------------
# 1. Pacotes do sistema e banco
# ---------------------------------------------------------------------------
echo "==> Pacotes do sistema"
# O projeto exige Python 3.12 (pyproject). No Ubuntu 22.04 ele vem do PPA
# deadsnakes; no 24.04 ja esta no repositorio. Nada de afrouxar o
# requirements para caber num Python mais velho: o servidor rodaria versoes
# que a suite nunca testou.
if ! apt-cache show "python$PYTHON_VERSAO" >/dev/null 2>&1; then
    sudo add-apt-repository -y ppa:deadsnakes/ppa
fi
pacotes=(
    "python$PYTHON_VERSAO" "python$PYTHON_VERSAO-venv" "python$PYTHON_VERSAO-dev"
    build-essential rsync gettext curl postgresql-client
)
faltando=()
for pacote in "${pacotes[@]}"; do
    dpkg -s "$pacote" >/dev/null 2>&1 || faltando+=("$pacote")
done
# Redis e Nginx podem ja existir por outro caminho (outro projeto, container):
# so instala quando nada atende.
timeout 2 bash -c '</dev/tcp/127.0.0.1/6379' 2>/dev/null || faltando+=(redis-server)
command -v nginx >/dev/null || faltando+=(nginx)
if [[ ${#faltando[@]} -gt 0 ]]; then
    echo "  instalando: ${faltando[*]}"
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${faltando[@]}"
fi

echo "==> Banco e extensoes"
export PGPASSWORD="$POSTGRES_PASSWORD"
PSQL=(psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" -v ON_ERROR_STOP=1)

# O `vector` nao vem com o PostgreSQL: e um pacote a parte, casado com a
# versao do servidor. Sem ele a migration morre no meio.
if ! "${PSQL[@]}" -d postgres -tAc \
    "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -q 1; then
    maior="$("${PSQL[@]}" -d postgres -tAc "SELECT current_setting('server_version_num')::int / 10000")"
    echo "  instalando postgresql-$maior-pgvector"
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "postgresql-$maior-pgvector" || {
        echo "ERRO: sem o pacote postgresql-$maior-pgvector neste Ubuntu." >&2
        echo "  Adicione o repositorio oficial do PostgreSQL (apt.postgresql.org) e rode de novo." >&2
        exit 1
    }
fi

if ! "${PSQL[@]}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1; then
    "${PSQL[@]}" -d postgres -c "CREATE DATABASE \"$POSTGRES_DB\""
    echo "  banco $POSTGRES_DB criado"
fi

# A extensao vai num schema dedicado, NAO no public: com um schema por tenant,
# uma extensao so no public nao fica alcancavel ao criar o segundo tenant, e a
# migration falha com `type "vector" does not exist`.
"${PSQL[@]}" -d "$POSTGRES_DB" -q <<SQL
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector   WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
ALTER DATABASE "$POSTGRES_DB" SET search_path TO "\$user", public, extensions;
SQL

# ---------------------------------------------------------------------------
# 2. Dependencias (antes de trocar o codigo)
# ---------------------------------------------------------------------------
echo "==> Dependencias"
if [[ -x "$VENV/bin/python" ]] && ! "$VENV/bin/python" -c \
    "import sys; sys.exit(sys.version_info[:2] != tuple(map(int, '$PYTHON_VERSAO'.split('.'))))"; then
    echo "  venv com outro Python: recriando"
    rm -rf "$VENV"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
    "python$PYTHON_VERSAO" -m venv "$VENV"
    rm -f "$RAIZ/.requirements_hash"
fi
HASH_NOVO="$(sha256sum "$ENVIO/requirements.txt" | awk '{print $1}')"
if [[ "$HASH_NOVO" != "$(cat "$RAIZ/.requirements_hash" 2>/dev/null)" ]]; then
    "$VENV/bin/pip" install -q --upgrade pip setuptools wheel
    "$VENV/bin/pip" install -q -r "$ENVIO/requirements.txt"
    echo "$HASH_NOVO" > "$RAIZ/.requirements_hash"
    echo "  instaladas"
else
    echo "  requirements.txt sem mudanca"
fi
echo "  Python: $("$VENV/bin/python" --version 2>&1)  Django: $("$VENV/bin/python" -c 'import django; print(django.get_version())')"

# ---------------------------------------------------------------------------
# 3. Codigo
# ---------------------------------------------------------------------------
echo "==> Codigo"
rsync -a --delete \
    --exclude '/venv/' --exclude '/.env' --exclude '/.requirements_hash' \
    --exclude '/staticfiles/' --exclude '/media/' --exclude '/.model_cache/' \
    --exclude '/.release' \
    "$ENVIO/" "$RAIZ/"
cd "$RAIZ"
mkdir -p "$MIDIA" "$RAIZ/staticfiles" "$RAIZ/.model_cache"

# ---------------------------------------------------------------------------
# 4. Django
# ---------------------------------------------------------------------------
manage() { "$VENV/bin/python" manage.py "$@"; }

echo "==> Verificacoes"
manage check --deploy
# Model mudado sem migration: descobrir aqui e barato, em producao nao.
manage makemigrations --check --dry-run

echo "==> Migrations (public e todos os tenants)"
manage migrate_schemas --noinput

# As migrations criam tabelas, e nao a linha que resolve o dominio raiz.
echo "==> Tenant public e dominio raiz"
manage bootstrap_public

# Conexoes vivem no banco (ADR-0012): criam se faltar, PRESERVAM o que houver,
# para um ajuste feito na tela sobreviver a proxima implantacao.
echo "==> Conexoes de inferencia, conversao e imagem"
# Sem INFERENCIA_BASE_URL no .env a conexao nao nasce, mas o resto do sistema
# sobe: so a geracao de artigo espera por ela. Aviso, e nao falha.
manage configurar_inferencia || echo "  AVISO: sem conexao de inferencia; confira INFERENCIA_BASE_URL e INFERENCIA_MODELO."
manage configurar_conversao --opcional
manage configurar_imagem --opcional

echo "==> Prompts iniciais"
manage semear_prompts --todos
manage semear_dados

echo "==> Estaticos e traducoes"
manage collectstatic --noinput
manage compilemessages 2>/dev/null || true

# O modelo de embedding (~2 GB) baixa agora, e nao dentro da primeira busca de
# alguem — mas so se ainda nao estiver no disco: abrir o modelo aqui, com o
# servico de vetores ja segurando a copia dele, dobraria a memoria em uso.
# Nunca derruba a implantacao: sem ele a busca falha com mensagem propria.
echo "==> Modelo de embedding"
manage shell -c "
from pathlib import Path
from django.conf import settings
if any(Path(settings.EMBEDDING_CACHE_DIR).glob('**/tokenizer.json')):
    print('  ja esta no disco')
else:
    from apps.knowledge.embeddings import FastEmbedClient
    FastEmbedClient()._carregar()
    print('  baixado')
" || echo "  AVISO: o modelo nao baixou; o servico de vetores tenta de novo ao subir."

# ---------------------------------------------------------------------------
# 5. Servicos
# ---------------------------------------------------------------------------
# Os units sao versionados, mas o systemd le de /etc/systemd/system. Sem
# sincronizar, editar um `.service` no git nao tem efeito nenhum.
echo "==> Units do systemd"
PUBLIBOT_ROOT="$RAIZ" "$RAIZ/deploy/scripts/sincronizar-systemd.sh"
sudo systemctl enable --quiet publibot.socket publibot.service \
    celery-publibot.service celery-beat-publibot.service vetores-publibot.service

echo "==> Nginx"
DOMINIO="${ROOT_DOMAIN:?ROOT_DOMAIN vazio no .env}"
if [[ -f "/etc/letsencrypt/live/$DOMINIO/fullchain.pem" ]]; then
    # O Nginx (www-data) precisa atravessar /home/ubuntu para servir estaticos
    # e midia. So atravessar (x), sem listar.
    sudo chmod o+x "$(dirname "$RAIZ")"
    novo="$(mktemp)"
    sed -e "s|__DOMINIO__|$DOMINIO|g" -e "s|__RAIZ__|$RAIZ|g" -e "s|__MIDIA__|${MIDIA%/}|g" \
        "$RAIZ/deploy/nginx/publibot.conf" > "$novo"
    destino=/etc/nginx/sites-available/publibot
    if ! sudo cmp -s "$novo" "$destino"; then
        anterior="$(mktemp)"
        sudo cp "$destino" "$anterior" 2>/dev/null || true
        sudo install -m 0644 "$novo" "$destino"
        sudo ln -sf "$destino" /etc/nginx/sites-enabled/publibot
        # O Nginx atende outros projetos: config invalida aqui derrubaria todos.
        if sudo nginx -t 2>/dev/null; then
            sudo systemctl reload nginx
            echo "  site atualizado"
        else
            if [[ -s "$anterior" ]]; then
                sudo install -m 0644 "$anterior" "$destino"
            else
                sudo rm -f "$destino" /etc/nginx/sites-enabled/publibot
            fi
            echo "ERRO: a config do Nginx nao passou no nginx -t; a anterior foi mantida." >&2
            exit 1
        fi
    else
        echo "  site ja esta em dia"
    fi
else
    echo "  AVISO: sem certificado em /etc/letsencrypt/live/$DOMINIO/. O site do"
    echo "  Nginx entra na primeira implantacao depois de emiti-lo (docs/OPERACAO.md)."
fi

echo "==> Reiniciando servicos"
# Identifica esta implantacao. O /healthz/ devolve o que o processo carregou;
# e assim que se sabe que o reload pegou o codigo novo, e nao so que o site
# responde (com o antigo).
IMPLANTACAO="$(date +%s)-$$"
echo "$IMPLANTACAO" > "$RAIZ/.release"

# A aplicacao recarrega sem derrubar o socket: as conexoes em curso terminam.
sudo systemctl start publibot.socket
if systemctl is-active --quiet publibot.service; then
    sudo systemctl reload publibot.service
else
    sudo systemctl start publibot.service
fi
# O worker precisa de restart, e nao reload. `TimeoutStopSec` no unit e maior
# que a tarefa mais longa: com acks_late, matar no meio faz o broker
# reentregar, e uma implantacao publicaria o mesmo conteudo duas vezes.
sudo systemctl restart celery-publibot.service
sudo systemctl restart celery-beat-publibot.service

# O servico de vetores leva meio minuto para abrir o modelo, e nesse tempo a
# busca responde "servico carregando". So reinicia quando o codigo dele mudou.
MARCA_VETORES="$RAIZ/.model_cache/.versao-do-servico"
VERSAO_VETORES="$(cat "$RAIZ/apps/knowledge/servico_de_vetores.py" \
    "$RAIZ/apps/knowledge/embeddings.py" | sha256sum | cut -d' ' -f1)"
if ! systemctl is-active --quiet vetores-publibot.service; then
    sudo systemctl start vetores-publibot.service
elif [[ "$(cat "$MARCA_VETORES" 2>/dev/null)" != "$VERSAO_VETORES" ]]; then
    sudo systemctl restart vetores-publibot.service
fi
mkdir -p "$RAIZ/.model_cache" && echo "$VERSAO_VETORES" > "$MARCA_VETORES"

# Direto no socket do Gunicorn, e nao pelo Nginx: responde mesmo antes do
# certificado existir, e nao confunde com o site de outro projeto.
versao_no_ar() {
    curl -sf --unix-socket /run/publibot/publibot.sock \
        -H "Host: $DOMINIO" -H "X-Forwarded-Proto: https" http://localhost/healthz/ || true
}
esperar_versao() {
    for _ in $(seq 1 "$1"); do
        if versao_no_ar | grep -q "$IMPLANTACAO"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

echo "==> Conferindo que a versao nova esta no ar"
if esperar_versao 15; then
    echo "Aplicacao respondendo com a versao nova."
    exit 0
fi
# O reload nao bastou (worker preso, config antiga): restart, uma vez.
echo "  o reload nao trouxe a versao nova; reiniciando o servico"
sudo systemctl restart publibot.service
if esperar_versao 20; then
    echo "Aplicacao respondendo com a versao nova (depois do restart)."
    exit 0
fi

echo "ERRO: a aplicacao nao respondeu com a versao nova. journalctl -u publibot -n 50" >&2
exit 1
