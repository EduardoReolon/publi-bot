# Contexto do modelo de texto no worker-gpu

O que o publi-bot espera do worker para que um pedido grande (planejar um
artigo, por exemplo) nunca estoure o contexto do modelo. Vale para quem
implementar ou reinstalar o worker — inclusive depois de formatar a máquina.

## O problema

O Ollama carrega o modelo com **4096 tokens de contexto por padrão**. Planejar
um artigo manda ~5 mil tokens (fontes + tese + instruções), e o pedido falha:

```
HTTP 400: request (4835 tokens) exceeds the available context size (4096 tokens)
```

Subir o contexto à mão resolve até a próxima reinstalação. Este contrato faz o
worker se ajustar sozinho, a cada pedido.

## O cabeçalho

Toda chamada de texto do publi-bot (`POST /v1/chat/completions`) leva:

```http
X-PubliBot-Contexto: 16384
```

O valor é **quanto contexto este pedido precisa**: o maior entre o mínimo do
publi-bot (`INFERENCIA_CONTEXTO_MINIMO`, padrão 16384) e o tamanho estimado do
pedido mais a resposta, arredondado para cima em múltiplos de 4096.

## O que o worker faz com ele

1. Ler o cabeçalho (ausente ou inválido: usar o padrão do worker).
2. Repassar ao Ollama como **`options.num_ctx`**. A rota OpenAI do Ollama
   (`/v1/chat/completions`) **ignora** `options`; por isso o worker deve chamar
   a API nativa, `POST /api/chat`, com:

   ```json
   {
     "model": "<modelo>",
     "messages": [...],
     "stream": false,
     "options": {"num_ctx": 16384, "temperature": 0.2, "num_predict": <max_tokens>},
     "format": <json_schema, se veio response_format>
   }
   ```

   e converter a resposta de volta para o formato OpenAI que o publi-bot lê
   (`choices[0].message.content`, `usage.prompt_tokens`,
   `usage.completion_tokens`, `model`).
3. **Não baixar o contexto** de um pedido para o outro sem necessidade: cada
   troca de `num_ctx` faz o Ollama recarregar o modelo (segundos). Guardar o
   maior valor já usado e mandar sempre `max(pedido, maior_ja_usado)` mantém
   uma carga só.
4. **Limite da placa.** Se o contexto pedido não couber na VRAM, usar o maior
   que couber (configuração do worker, por exemplo `CONTEXTO_MAXIMO`) e, se
   mesmo assim o pedido não couber, responder **400** com o mesmo
   `exceed_context_size_error` que o Ollama/llama.cpp devolveria
   (`n_prompt_tokens`, `n_ctx`): o publi-bot mostra uma mensagem clara.
5. Enquanto o modelo recarrega com o contexto novo, se o worker preferir não
   segurar a conexão, pode responder **503 com `Retry-After`**: o publi-bot
   adia e tenta de novo, sem gastar tentativa.

Alternativa mínima (sem trocar de rota): iniciar o Ollama com
`OLLAMA_CONTEXT_LENGTH=16384` (ou mais). Funciona, mas não acompanha pedidos
maiores que esse valor — por isso o cabeçalho é o caminho preferido.

## O que o `/health/` declara

No bloco `ollama` do `/health/`, acrescentar:

```json
"ollama": {
  "de_pe": true,
  "carregados": ["qwen2.5:14b"],
  "contexto": {
    "padrao": 16384,
    "atual": 16384,
    "maximo": 32768,
    "segue_cabecalho": true
  }
}
```

- `padrao`: o contexto usado quando o pedido não traz o cabeçalho.
- `atual`: o `num_ctx` com que o modelo está carregado agora (0 se nenhum).
- `maximo`: o maior que a placa aguenta (o teto do item 4).
- `segue_cabecalho`: `true` quando o item 2 está implementado.

O publi-bot confere isso em `manage.py conferir_worker`: sem o bloco, ou com
`segue_cabecalho: false` e `padrao` abaixo do mínimo, a conferência falha com a
instrução.

## Como testar

```bash
# Um pedido com ~6 mil tokens precisa passar:
python - <<'PY'
import httpx, os
texto = "palavra " * 18000
r = httpx.post(
    os.environ["WORKER"] + "/v1/chat/completions",
    headers={"Authorization": "Bearer " + os.environ["SEGREDO"], "X-PubliBot-Contexto": "16384"},
    json={"model": os.environ["MODELO"], "messages": [{"role": "user", "content": texto + "\nResponda: ok"}], "max_tokens": 8},
    timeout=600,
)
print(r.status_code, r.text[:300])
PY
```

Esperado: `200`. Sem o contrato implementado, o mesmo pedido devolve o 400 de
contexto excedido.
