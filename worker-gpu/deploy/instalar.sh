#!/usr/bin/env bash
#
# Instala os servicos de GPU como units do systemd, nesta maquina.
#
#   ./deploy/instalar.sh                    conversao de PDF (Docling)
#   ./deploy/instalar.sh --imagem           geracao de imagem de capa
#   ./deploy/instalar.sh --tudo             os dois
#   ./deploy/instalar.sh --tudo --sistema   os dois, como unit de sistema
#
# O padrao e unit de USUARIO, e a escolha e deliberada. Numa maquina pessoal
# ela nao pede sudo, sobe junto com a sua sessao e usa o venv que ja esta na
# sua pasta. Unit de sistema e para uma maquina dedicada, que precisa subir o
# servico no boot sem ninguem entrar.
#
# Uma unit de usuario para quando voce sai da sessao. Para mante-la de pe com a
# maquina ligada e ninguem logado:
#
#     sudo loginctl enable-linger "$USER"
#
# Os dois servicos dividem a mesma placa. Isso e tratado em quatro camadas, e o
# cabecalho de `imagem_api.py` as explica — a curta e: cada um roda um pedido
# por vez, a reserva do PubliBot conta vagas por MAQUINA, e o servico de imagem
# devolve a VRAM depois de um tempo ocioso.
#
# Idempotente: rodar de novo reescreve as units e reinicia os servicos.

set -euo pipefail

cd "$(dirname "$0")/.."
RAIZ="$(pwd)"

# Sem argumento de servico, instala so o Docling: e o que ja existia, e uma
# atualizacao do repositorio nao deve comecar a subir um servico novo sozinha.
ESCOPO="usuario"
QUERO_DOCLING=1
QUERO_IMAGEM=0

for argumento in "$@"; do
    case "$argumento" in
        --sistema) ESCOPO="sistema" ;;
        --imagem)  QUERO_DOCLING=0; QUERO_IMAGEM=1 ;;
        --docling) QUERO_DOCLING=1; QUERO_IMAGEM=0 ;;
        --tudo)    QUERO_DOCLING=1; QUERO_IMAGEM=1 ;;
        *)
            echo "ERRO: argumento desconhecido '$argumento'." >&2
            echo "  Use --imagem, --docling, --tudo e/ou --sistema." >&2
            exit 1
            ;;
    esac
done

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

# Sem o segredo o servico sobe e responde 500 a TODA chamada, com uma mensagem
# que fala de configuracao sem dizer qual arquivo preencher.
if ! grep -qE '^WORKER_SHARED_SECRET=.+' "$RAIZ/.env"; then
    echo "ERRO: WORKER_SHARED_SECRET esta vazio em $RAIZ/.env." >&2
    echo "  Gere um com:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"" >&2
    echo "  O MESMO valor vai em CONVERSAO_SEGREDO e IMAGEM_SEGREDO, no .env do PubliBot." >&2
    exit 1
fi

# `BIND_HOST=0.0.0.0` expoe na internet um endpoint que roda modelo na sua
# placa. Recusar aqui e mais barato que descobrir depois.
if grep -qE '^BIND_HOST=(0\.0\.0\.0|::)\s*$' "$RAIZ/.env"; then
    echo "ERRO: BIND_HOST=0.0.0.0 em $RAIZ/.env." >&2
    echo "  Use 127.0.0.1 (so esta maquina) ou o endereco da Tailscale." >&2
    exit 1
fi

if [[ "$QUERO_IMAGEM" == 1 ]] && ! "$RAIZ/venv/bin/python" -c "import diffusers" 2>/dev/null; then
    echo "ERRO: o venv nao tem o diffusers, e o servico de imagem depende dele." >&2
    echo "  ./venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ "$ESCOPO" == "sistema" ]]; then
    PASTA_DAS_UNITS="/etc/systemd/system"
    SYSTEMCTL=(sudo systemctl)
    INSTALAR=(sudo install -m 0644)
    LINHA_DE_USUARIO="User=$(id -un)"
    ALVO="multi-user.target"
else
    PASTA_DAS_UNITS="$HOME/.config/systemd/user"
    SYSTEMCTL=(systemctl --user)
    INSTALAR=(install -m 0644)
    # Unit de usuario ja roda como voce; `User=` ali e erro de carregamento.
    LINHA_DE_USUARIO="# (unit de usuario: roda como quem a iniciou)"
    ALVO="default.target"
    mkdir -p "$PASTA_DAS_UNITS"
fi

instalar_unit() {
    local nome="$1"
    local molde="$RAIZ/deploy/$nome.service"
    local destino="$PASTA_DAS_UNITS/$nome.service"

    echo "==> Gerando a unit $nome ($ESCOPO)"
    local temporario
    temporario="$(mktemp)"

    # `|` como separador: os valores sao caminhos, e com `/` cada um
    # precisaria de escape.
    sed -e "s|RAIZ|$RAIZ|g" \
        -e "s|LINHA_DE_USUARIO|$LINHA_DE_USUARIO|" \
        -e "s|ALVO_DE_INSTALACAO|$ALVO|" \
        "$molde" > "$temporario"

    "${INSTALAR[@]}" "$temporario" "$destino"
    rm -f "$temporario"
    echo "  $destino"
}

conferir_saude() {
    local nome="$1" porta="$2"
    local url="http://${BIND_HOST:-127.0.0.1}:${porta}/health/"

    # Os dois servicos carregam o modelo de forma preguicosa, entao respondem
    # antes de ter peso nenhum na memoria — subir rapido aqui nao diz nada
    # sobre o primeiro pedido, que ainda vai baixar alguns GB.
    for _ in $(seq 1 15); do
        if resposta="$(curl -sf "$url" 2>/dev/null)"; then
            echo "  $nome: $resposta"
            return 0
        fi
        sleep 2
    done

    echo "ERRO: $nome nao respondeu em $url." >&2
    echo "  ${SYSTEMCTL[*]} status $nome.service" >&2
    echo "  journalctl --user -u $nome.service -n 50" >&2
    return 1
}

UNITS=()
[[ "$QUERO_DOCLING" == 1 ]] && UNITS+=("docling-api")
[[ "$QUERO_IMAGEM" == 1 ]] && UNITS+=("imagem-api")

for unit in "${UNITS[@]}"; do
    instalar_unit "$unit"
done

echo "==> Habilitando e subindo"
"${SYSTEMCTL[@]}" daemon-reload
for unit in "${UNITS[@]}"; do
    "${SYSTEMCTL[@]}" enable "$unit.service" >/dev/null
    "${SYSTEMCTL[@]}" restart "$unit.service"
done

echo "==> Conferindo /health/"
set -a; source <(grep -E '^[A-Z_]+=' "$RAIZ/.env"); set +a

FALHOU=0
[[ "$QUERO_DOCLING" == 1 ]] && { conferir_saude docling-api "${BIND_PORT:-8100}" || FALHOU=1; }
[[ "$QUERO_IMAGEM" == 1 ]] && { conferir_saude imagem-api "${IMAGEM_BIND_PORT:-8101}" || FALHOU=1; }
[[ "$FALHOU" == 1 ]] && exit 1

echo
echo "Pronto. No .env do PubliBot:"
if [[ "$QUERO_DOCLING" == 1 ]]; then
    echo "  CONVERSAO_BASE_URL=http://${BIND_HOST:-127.0.0.1}:${BIND_PORT:-8100}"
    echo "  CONVERSAO_SEGREDO=<o mesmo WORKER_SHARED_SECRET daqui>"
fi
if [[ "$QUERO_IMAGEM" == 1 ]]; then
    echo "  IMAGEM_BASE_URL=http://${BIND_HOST:-127.0.0.1}:${IMAGEM_BIND_PORT:-8101}"
    echo "  IMAGEM_SEGREDO=<o mesmo WORKER_SHARED_SECRET daqui>"
    echo "  IMAGEM_MODELO=${IMAGEM_MODELO:-stabilityai/stable-diffusion-xl-base-1.0}"
fi
echo
echo "E depois, uma vez:"
[[ "$QUERO_DOCLING" == 1 ]] && echo "  python manage.py configurar_conversao --testar"
[[ "$QUERO_IMAGEM" == 1 ]] && echo "  python manage.py configurar_imagem --testar"
exit 0
