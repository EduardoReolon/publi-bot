#!/usr/bin/env bash
#
# Copia os units do repositorio para /etc/systemd/system e recarrega o systemd
# APENAS se algum deles mudou.
#
# Por que isto existe: os units sao versionados junto do codigo, mas o systemd
# le de /etc/systemd/system. Sem sincronizar, editar um `.service` no
# repositorio nao tem efeito nenhum no servidor — e nada avisa. O sintoma e
# perverso: a mudanca esta no git, foi revisada, foi implantada, e o servico
# continua rodando a versao antiga.
#
# So recarrega quando ha diferenca porque `daemon-reload` a cada implantacao e
# ruido: ele reavalia todas as units da maquina, inclusive as de outros
# projetos.
#
# Usado pelo bootstrap.sh (primeira instalacao) e pelo release.sh (toda
# implantacao). Idempotente: sem mudanca, nao faz nada.

set -euo pipefail

RAIZ="${PUBLIBOT_ROOT:-/srv/publibot}"
ORIGEM="$RAIZ/deploy/systemd"

# Sobrescrevivel para permitir exercitar a logica de comparacao fora de um
# servidor de verdade — ver tests/test_deploy_scripts.py. Em producao, o
# default e o unico valor correto.
DESTINO="${PUBLIBOT_SYSTEMD_DIR:-/etc/systemd/system}"

# Idem: fora de um servidor nao ha systemd nem sudo.
SUDO="${PUBLIBOT_SUDO-sudo}"

if [[ ! -d "$ORIGEM" ]]; then
    echo "ERRO: $ORIGEM nao existe." >&2
    exit 1
fi

mudou=0

for arquivo in "$ORIGEM"/*; do
    nome="$(basename "$arquivo")"
    if ! $SUDO cmp -s "$arquivo" "$DESTINO/$nome"; then
        echo "  unit alterada: $nome"
        $SUDO install -m 0644 "$arquivo" "$DESTINO/$nome"
        mudou=1
    fi
done

if [[ $mudou -eq 1 ]]; then
    echo "  recarregando o systemd"
    $SUDO systemctl daemon-reload
else
    echo "  units ja estao em dia"
fi
