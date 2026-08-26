#!/usr/bin/env bash
#
# Roda as tres suites do projeto.
#
# Sao tres e nao uma porque exigem configuracoes diferentes:
#
#  1. Suite principal    — multi-tenant, cliente de embedding falso.
#  2. Contrato           — settings SEM multi-tenancy: o site que recebe
#                          conteudo e um site comum, e supor tenancy nele
#                          testaria algo que o contrato nao exige.
#  3. Integracao         — carrega o modelo de embedding real (~2 GB).

set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-./venv/bin/python}"
falhou=0

echo "=== 1/3 suite principal ==="
"$PY" -m pytest tests/ -q || falhou=1

echo
echo "=== 2/3 contrato ponta a ponta ==="
"$PY" -m pytest tests_contrato/ -q --ds=core.settings.test_contract || falhou=1

echo
echo "=== 3/3 integracao (modelo real) ==="
if [[ -d .model_cache ]] && [[ -n "$(ls -A .model_cache 2>/dev/null)" ]]; then
    EMBEDDING_LOCAL_FILES_ONLY=True "$PY" -m pytest tests/ -q -m integration || falhou=1
else
    echo "pulado: modelo nao esta em .model_cache"
fi

echo
if [[ $falhou -eq 0 ]]; then
    echo "tudo passou."
else
    echo "houve falhas." >&2
fi
exit $falhou
