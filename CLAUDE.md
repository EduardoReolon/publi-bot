# Regras de trabalho neste repositorio

- Responder em portugues.
- Desenvolver no branch `claude-cli` e dar push nele.
- **Merge em `main` sempre**, ao terminar cada mudanca (suite verde), sem perguntar —
  ate o dono dizer o contrario. Ninguem mais usa o sistema por enquanto:

      git checkout main && git pull origin main \
        && git merge --no-ff claude-cli -m "Merge branch 'claude-cli' into main" \
        && git push -u origin main; git checkout claude-cli

  O push em `main` dispara o deploy (`release.sh` roda migrate e `semear_prompts`).
- Nao abrir pull request.
- Preferir algoritmo a LLM; reaproveitar funcoes, nao duplicar codigo.
- O PubliBot e generico (clientes esperados: clinicas medicas): prompts sem vies do
  negocio do dono. Campos novos de formulario do radar sao opcionais.
- Nunca repetir segredos de `.env` ou de logs.
