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
ARQUIVO_ENV="${PUBLIBOT_ENV_FILE:-/etc/publibot/env}"
cd "$RAIZ"

# As units do systemd leem este arquivo por `EnvironmentFile`, mas os
# `manage.py` daqui rodam FORA delas — e sem as variaveis o primeiro comando
# morre em `ImproperlyConfigured: DJANGO_SECRET_KEY`, antes de qualquer
# migration. O `grep` descarta comentarios e linhas soltas: o arquivo e lido
# como dados, nao executado como script.
if [[ -r "$ARQUIVO_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a; source <(grep -E '^[A-Z_][A-Z0-9_]*=' "$ARQUIVO_ENV"); set +a
elif [[ -f "$ARQUIVO_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a; source <(sudo cat "$ARQUIVO_ENV" | grep -E '^[A-Z_][A-Z0-9_]*='); set +a
else
    echo "ERRO: $ARQUIVO_ENV nao existe. Rode antes: deploy/scripts/bootstrap.sh" >&2
    exit 1
fi

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

# As migrations criam as tabelas do schema public, nao a linha em
# `accounts_tenant` que resolve o dominio raiz. Idempotente: numa instalacao ja
# feita apenas confirma o que existe.
echo "==> Tenant public e dominio raiz"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py bootstrap_public

# A conexao de inferencia e uma LINHA no banco, nao um arquivo — trocar de
# modelo nao deve exigir implantacao (ADR-0012). Numa instalacao nova, porem,
# essa linha nao existe, e sem ela a aplicacao sobe inteira e so falha dentro
# do primeiro job.
#
# Sem `--atualizar` de proposito: cria se faltar, preserva se ja houver. Assim
# um ajuste feito na tela sobrevive a proxima implantacao.
echo "==> Conexao de inferencia"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py configurar_inferencia

# Mesma logica da conexao de inferencia, com `--opcional`: uma instalacao sem
# worker de conversao e um estado legitimo (os PDFs sao recusados em producao,
# o resto do sistema funciona), e nao pode derrubar a implantacao.
echo "==> Conexao de conversao (Docling)"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py configurar_conversao --opcional

# Tambem `--opcional`, e aqui o estado sem conexao e ainda mais comum: sem
# gerador de imagem o artigo sai igual, apenas sem capa. O texto e o produto.
echo "==> Conexao de imagem"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py configurar_imagem --opcional

# Os prompts sao LINHAS no schema de cada tenant, e `migrate_schemas` cria
# tabela e nao linha. O provisionamento ja semeia os tenants novos; isto
# alcanca os que existiam antes, e conserta qualquer um que tenha ficado sem —
# um tenant sem prompts sobe inteiro e so falha ao gerar o primeiro artigo.
echo "==> Prompts iniciais"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py semear_prompts --todos

echo "==> Arquivos estaticos"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py collectstatic --noinput

echo "==> Traducoes"
DJANGO_SETTINGS_MODULE=core.settings.prod "$VENV/bin/python" manage.py compilemessages 2>/dev/null || true

# Os units sao versionados junto do codigo, mas o systemd le de
# /etc/systemd/system. Sem sincronizar, editar um `.service` no repositorio nao
# tem efeito nenhum — e nada avisa: a mudanca esta no git, foi revisada, foi
# implantada, e o servico segue rodando a versao antiga.
echo "==> Units do systemd"
"$RAIZ/deploy/scripts/sincronizar-systemd.sh"

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
