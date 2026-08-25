# ADR-0012 — Inferencia como endpoints com reserva de concorrencia

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O hardware disponivel e uma NVIDIA RTX 3050 com 8 GB de VRAM, que nao comporta
um modelo de texto e um modelo de imagem carregados ao mesmo tempo. E ha um
modo de falha especialmente traicoeiro: **quando a VRAM nao basta, o Ollama cai
silenciosamente para CPU** — a geracao passa de dezenas de tokens por segundo
para poucos, sem erro nenhum. Serializacao estrita nao e otimizacao aqui, e
requisito de corretude.

Ao mesmo tempo, o produto precisa poder usar APIs hospedadas, e no futuro
permitir que um cliente traga a chave dele, com limite proprio.

## Decisao

**Toda inferencia e um endpoint HTTP registrado como `InferenceConnection`.**

```python
class InferenceConnection:
    tenant           # nulo = compartilhada pelo sistema
    name
    kind             # ollama | openai_compatible | anthropic | docling | comfyui
    base_url
    api_key_ciphertext
    workloads        # text | vision_parse | image | embedding
    max_concurrency  # a GPU local: 1
    is_active, health_status, consecutive_failures
```

Antes de qualquer chamada sair, a task **adquire uma reserva** naquela conexao.
Sem vaga, o job volta ao banco com `next_attempt_at` — nunca fica bloqueado
segurando um processo.

O **despachante le os jobs do banco**, nao da fila do Celery, e escolhe o
proximo respeitando o modelo ja carregado na GPU: drena todo o trabalho de
texto antes de trocar para imagem, evitando os 10 a 60 segundos de troca de
modelo na VRAM.

## Consequencias

- Um unico mecanismo serve a GPU local e as APIs hospedadas. O argumento
  decisivo: **APIs hospedadas sao endpoints e nao podem virar worker de fila.**
  Se a GPU local fosse um worker Celery remoto e as APIs fossem endpoints,
  existiriam dois caminhos de codigo para a mesma coisa, duas formas de contar
  concorrencia e dois lugares para o mesmo bug.
- A conexao de um cliente tem limite proprio e nao disputa com a do sistema.
- O Celery vira transporte; o banco e a fonte da verdade. E isso que torna a
  retomada apos interrupcao implementavel e visivel no painel.
- Um disjuntor por conexao: cinco falhas abrem o circuito por 15 minutos, com
  sondagem de meia-abertura no endpoint de saude.
- Configuracao recomendada na maquina com GPU: `OLLAMA_MAX_LOADED_MODELS=1`,
  `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=30m`.
- **Numeros ainda nao medidos**, e todo dimensionamento de tempo limite depende
  deles: quanto o Docling leva num artigo de 20 paginas, e quanto leva uma
  geracao de 2000 palavras nesta placa.

## Escolha do modelo por chamada

Ordem de resolucao, do mais especifico ao menos:

1. `Site.model_overrides` — um JSON de chave-do-prompt para nome-do-modelo.
2. `PromptVersion.model_name` — o modelo com que aquela versao foi calibrada.
3. O padrao do `settings`.

Isso permite que um site em italiano use um modelo melhor em italiano apenas
para a redacao, sem duplicar prompt nenhum.
