# Rota de vetorização no worker-gpu

O que o publi-bot espera do worker para vetorizar documentos (indexar). O
cliente já está pronto (`apps/knowledge/embeddings.py::vetorizar_passagens`) e
testado contra esta especificação (`tests/test_vetorizacao_no_worker.py`).

## Por que

O servidor é um ARM de 1 CPU. Vetorizar um documento é um vetor por parágrafo,
com o `intfloat/multilingual-e5-large` — minutos de CPU que travam o resto. Na
placa, segundos.

**Só a indexação vai ao worker.** A consulta (o que o artigo procura no acervo)
continua no servidor: buscar não pode depender da máquina da placa estar ligada.
Por isso o vetor do worker precisa sair **igual** ao do servidor — é o mesmo
índice.

## Ligar do lado do publi-bot

Em **Inferência**, na conexão do worker, marcar a carga **Vetorização de
documentos** (`embedding`). Sem essa marca, nada muda: o servidor vetoriza como
sempre.

## A rota

```http
POST /v1/embeddings
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: application/json

{
  "model": "intfloat/multilingual-e5-large",
  "input": ["passage: texto do paragrafo 1", "passage: texto do paragrafo 2"]
}
```

Dialeto da OpenAI (`/v1/embeddings`), como as outras rotas.

- `input` vem **já com o prefixo** `passage: ` (exigência do e5). O worker **não
  acrescenta nada** ao texto.
- Um pedido traz os parágrafos de **um documento** (de 1 a algumas centenas).
  Processar em lotes internos do tamanho que a VRAM aguentar.

### Resposta (200)

```json
{
  "object": "list",
  "model": "intfloat/multilingual-e5-large",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, ...]},
    {"object": "embedding", "index": 1, "embedding": [...]}
  ]
}
```

- `embedding` com **1024** números.
- `model` **obrigatório** e igual ao nome pedido: o publi-bot recusa vetor de
  outro modelo (misturar modelos no mesmo índice estraga a busca sem aviso).
- A normalização L2 é feita pelo publi-bot; pode devolver normalizado ou não.

## O vetor precisa ser idêntico ao do servidor

O servidor usa **`fastembed==0.8.0`** (ONNX), modelo
`intfloat/multilingual-e5-large`, chamando `TextEmbedding(...).embed(textos)`
com os textos já prefixados. O caminho mais seguro é o worker usar **a mesma
biblioteca e a mesma versão**, com `onnxruntime-gpu` (pacote `fastembed-gpu`
na versão 0.8.0 e `providers=["CUDAExecutionProvider"]`).

Se usar outra biblioteca (sentence-transformers, por exemplo), conferir que o
pooling é o mesmo — o fastembed 0.8 usa **mean pooling** neste modelo — e
validar com o teste de conformidade abaixo.

### Teste de conformidade (fazer antes de ligar)

Vetorizar no worker e no servidor o texto
`passage: A curadoria garante que so entra no indice o que pode sustentar um artigo.`
Normalizar os dois (L2) e calcular o cosseno: precisa dar **≥ 0,999**.

No servidor:

```bash
python manage.py shell -c "from apps.knowledge.embeddings import get_embedding_client as g; \
print(g().embed_passage(['A curadoria garante que so entra no indice o que pode sustentar um artigo.'])[0][:5])"
```

(o `embed_passage` do servidor acrescenta o `passage: ` sozinho.)

## Erros — o mesmo contrato das outras rotas

| Situação | Resposta | O publi-bot faz |
|---|---|---|
| Modelo ainda baixando/carregando (primeira vez) | `503` + `error.code: "modelo_carregando"` + `Retry-After` | a tarefa volta para a fila e tenta depois do `Retry-After`; **não falha** e não apaga o índice antigo |
| Placa ocupada | `503` + `error.code: "gpu_ocupada"` + `Retry-After` | idem |
| Worker desligado / conexão recusada | — | idem (espera 5 min); a tela oferece **"Vetorizar agora no servidor"** |
| Rota inexistente | `404` | idem, com a mensagem apontando para este arquivo |
| Entrada inválida | `400`/`422` com `error.message` | falha, com a mensagem |

**Modelo na primeira requisição.** Não bloquear a requisição enquanto baixa
~2 GB: disparar o download em segundo plano e responder `503
modelo_carregando` com `Retry-After: 60` enquanto não terminar. Vários
documentos na fila recebem o mesmo 503 e voltam para a fila — nenhum falha, e
ninguém baixa o modelo duas vezes (trava no download).

## Árbitro e memória

- Entra no **mesmo lock** da placa que texto, imagem e conversão.
- O e5-large ocupa ~2,2 GB de VRAM em fp32 (~1,1 GB em fp16). Pode conviver com
  o Ollama se couber; se não couber, descarregar o modelo de texto antes, como a
  rota de imagem faz.
- Descarregar o modelo de vetorização depois de ocioso (como o Docling,
  `CONVERSAO_OCIOSO_SEGUNDOS`).
- Tempo: um documento de 300 parágrafos deve levar segundos. O cliente espera
  até 600 s.

## Sugestão de implementação (FastAPI)

```python
from fastembed import TextEmbedding

_modelo = None          # carregado sob demanda, com trava
_baixando = False

@app.post("/v1/embeddings")
def embeddings(pedido: PedidoDeEmbeddings, _=Depends(autenticar)):
    if pedido.model != "intfloat/multilingual-e5-large":
        raise HTTPException(422, ...)
    modelo = modelo_pronto_ou_503()      # dispara o download e devolve 503 enquanto baixa
    with lock_da_placa(timeout=...):     # 503 gpu_ocupada se nao conseguir
        vetores = list(modelo.embed(pedido.input, batch_size=32))
    return {
        "object": "list",
        "model": pedido.model,
        "data": [
            {"object": "embedding", "index": i, "embedding": v.tolist()}
            for i, v in enumerate(vetores)
        ],
    }
```
