# Carrega o .env como DADOS, do jeito que o systemd (EnvironmentFile) le:
# CHAVE=valor por linha, o valor ate o fim da linha (pode ter espaco), aspas
# em volta do valor inteiro sao tiradas, comentario e linha solta ficam de
# fora. Nunca `source` direto: `NOME=Ekron Consultoria` faria o bash rodar
# "Consultoria" como comando e derrubar a implantacao.
#
#   source deploy/scripts/ler_env.sh && carregar_env /caminho/.env
carregar_env() {
    local linha chave valor
    while IFS= read -r linha || [[ -n "$linha" ]]; do
        linha="${linha%$'\r'}"
        [[ "$linha" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
        chave="${BASH_REMATCH[1]}"
        valor="${BASH_REMATCH[2]}"
        if [[ "$valor" =~ ^\"(.*)\"$ || "$valor" =~ ^\'(.*)\'$ ]]; then
            valor="${BASH_REMATCH[1]}"
        fi
        export "$chave=$valor"
    done < "$1"
}
