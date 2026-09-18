#!/usr/bin/env bash
#
# Instala o servico de conversao como unit do systemd, nesta maquina.
#
#   ./deploy/instalar.sh              unit de USUARIO  (systemctl --user)
#   ./deploy/instalar.sh --sistema    unit de SISTEMA  (precisa de sudo)
#
# O padrao e unit de usuario, e a escolha e deliberada. Numa maquina pessoal
# ela nao pede sudo, sobe junto com a sua sessao e usa o venv que ja esta na
# sua pasta. Unit de sistema e para uma maquina dedicada, que precisa subir o
# servico no boot sem ninguem entrar.
#
# Uma unit de usuario para quando voce sai da sessao. Para mante-la de pe com a
# maquina ligada e ninguem logado:
#
#     sudo loginctl enable-linger "$USER"
#
# Idempotente: rodar de novo reescreve a unit e reinicia o servico.

set -euo pipefail

cd "$(dirname "$0")/.."
RAIZ="$(pwd)"
MOLDE="$RAIZ/deploy/docling-api.service"

ESCOPO="usuario"
if [[ "${1:-}" == "--sistema" ]]; then
    ESCOPO="sistema"
elif [[ -n "${1:-}" ]]; then
    echo "ERRO: argumento desconhecido ${1!r}. Use --sistema ou nenhum." >&2
    exit 1
fi

echo "==> Conferindo o que precisa existir"

if [[ ! -x "$RAIZ/venv/bin/uvicorn" ]]; then
    echo "ERRO: $RAIZ/venv/bin/uvicorn nao existe." >&2
    echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -f "$RAIZ/.env" ]]; then
    echo "ERRO: $RAIZ/.env nao existe." >&2
    echo "  cp .env.example .env    e defina WORKER_SHARED_SECRET" >&2
    exit 1
fi

# Sem o segredo o servico sobe e responde 500 a TODA conversao, com uma
# mensagem que fala de configuracao sem dizer qual arquivo preencher.
if ! grep -qE '^WORKER_SHARED_SECRET=.+' "$RAIZ/.env"; then
    echo "ERRO: WORKER_SHARED_SECRET esta vazio em $RAIZ/.env." >&2
    echo "  Gere um com:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"" >&2
    echo "  O MESMO valor vai em CONVERSAO_SEGREDO, no .env do PubliBot." >&2
    exit 1
fi

# `BIND_HOST=0.0.0.0` expoe na internet um endpoint que aceita PDF e roda
# modelo. Recusar aqui e mais barato que descobrir depois.
if grep -qE '^BIND_HOST=(0\.0\.0\.0|::)\s*$' "$RAIZ/.env"; then
    echo "ERRO: BIND_HOST=0.0.0.0 em $RAIZ/.env." >&2
    echo "  Use 127.0.0.1 (so esta maquina) ou o endereco da Tailscale." >&2
    exit 1
fi

if [[ "$ESCOPO" == "sistema" ]]; then
    DESTINO="/etc/systemd/system/docling-api.service"
    SYSTEMCTL=(sudo systemctl)
    INSTALAR=(sudo install -m 0644)
    LINHA_DE_USUARIO="User=$(id -un)"
    ALVO="multi-user.target"
else
    DESTINO="$HOME/.config/systemd/user/docling-api.service"
    SYSTEMCTL=(systemctl --user)
    INSTALAR=(install -m 0644)
    # Unit de usuario ja roda como voce; `User=` ali e erro de carregamento.
    LINHA_DE_USUARIO="# (unit de usuario: roda como quem a iniciou)"
    ALVO="default.target"
    mkdir -p "$(dirname "$DESTINO")"
fi

echo "==> Gerando a unit ($ESCOPO)"
TEMPORARIO="$(mktemp)"
trap 'rm -f "$TEMPORARIO"' EXIT

# `|` como separador: os valores sao caminhos, e com `/` cada um precisaria de
# escape.
sed -e "s|RAIZ|$RAIZ|g" \
    -e "s|LINHA_DE_USUARIO|$LINHA_DE_USUARIO|" \
    -e "s|ALVO_DE_INSTALACAO|$ALVO|" \
    "$MOLDE" > "$TEMPORARIO"

"${INSTALAR[@]}" "$TEMPORARIO" "$DESTINO"
echo "  $DESTINO"

echo "==> Habilitando e subindo"
"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" enable docling-api.service >/dev/null
"${SYSTEMCTL[@]}" restart docling-api.service

# O servico carrega o Docling de forma preguicosa, entao ele responde antes de
# ter modelo nenhum na memoria — subir rapido aqui nao diz nada sobre a
# primeira conversao, que ainda vai baixar centenas de MB.
echo "==> Conferindo /health/"
set -a; source <(grep -E '^[A-Z_]+=' "$RAIZ/.env"); set +a
URL="http://${BIND_HOST:-127.0.0.1}:${BIND_PORT:-8100}/health/"

for _ in $(seq 1 15); do
    if resposta="$(curl -sf "$URL" 2>/dev/null)"; then
        echo "  $resposta"
        echo
        echo "Pronto. No PubliBot:"
        echo "  CONVERSAO_BASE_URL=http://${BIND_HOST:-127.0.0.1}:${BIND_PORT:-8100}"
        echo "  CONVERSAO_SEGREDO=<o mesmo WORKER_SHARED_SECRET daqui>"
        echo "  python manage.py configurar_conversao --testar"
        exit 0
    fi
    sleep 2
done

echo "ERRO: o servico nao respondeu em $URL." >&2
echo "  ${SYSTEMCTL[*]} status docling-api.service" >&2
echo "  ${SYSTEMCTL[*]} -u docling-api.service -n 50   (journalctl)" >&2
exit 1
