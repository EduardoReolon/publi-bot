# Worker de GPU

Esta maquina **nao roda Django, nem Celery, nem banco**. Ela expoe servicos
HTTP e nada mais (ADR-0007).

O motivo e que APIs hospedadas — Together, OpenAI, Anthropic — sao endpoints e
nao podem virar worker de fila. Se a GPU local fosse um worker e as APIs fossem
endpoints, existiriam dois caminhos de codigo para a mesma coisa, duas formas
de contar concorrencia e dois lugares para o mesmo defeito.

| Servico | Porta | O que faz |
|---|---|---|
| `ollama serve` | 11434 | Geracao de texto |
| `docling-api` | 8100 | PDF para Markdown |

## Regra que nao pode ser violada

**Escute apenas no endereco da rede privada (Tailscale), nunca em `0.0.0.0`.**

Um endpoint que aceita PDF e roda modelo, aberto na internet, e um problema
serio: qualquer pessoa poderia consumir a GPU, e o Ollama nao tem autenticacao
propria.

## Instalacao

### Ollama

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf <<'CONF'
[Service]
# Substitua pelo IP da Tailscale desta maquina.
Environment="OLLAMA_HOST=100.x.y.z:11434"

# Um modelo por vez. Numa placa de 8 GB, dois modelos carregados estouram a
# VRAM e a inferencia cai SILENCIOSAMENTE para CPU: de dezenas de tokens por
# segundo para poucos, sem erro nenhum.
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"

# Mantem o modelo carregado entre chamadas. Recarregar custa de 10 a 60
# segundos, e a fila e agrupada por modelo justamente para nao pagar isso a
# cada tarefa.
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF

sudo systemctl daemon-reload && sudo systemctl restart ollama
```

### Serviço do Docling

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # defina WORKER_SHARED_SECRET e BIND_HOST
sudo cp docling-api.service /etc/systemd/system/
sudo systemctl enable --now docling-api
```

## Cadastro no PubliBot

No painel, em Conexoes de inferencia:

| Campo | Ollama | Docling |
|---|---|---|
| Tipo | Compativel com OpenAI | Docling |
| URL base | `http://100.x.y.z:11434` | `http://100.x.y.z:8100` |
| Cargas | `["text"]` | `["vision_parse"]` |
| Concorrencia maxima | **1** | **1** |

Concorrencia 1 nos dois, e a mesma maquina: sao a mesma placa. Deixar 2 em
qualquer um deles reintroduz exatamente o problema de VRAM descrito acima.

## Dimensionamento

Numa RTX 3050 de 8 GB, cabe **um** de cada vez:

- um modelo de texto de 7-8B quantizado (q4), com contexto moderado; **ou**
- um modelo de imagem.

Nunca os dois. E por isso que o PubliBot serializa por conexao e agrupa a fila
por modelo carregado.

**Meça antes de confiar:** quanto o Docling leva num artigo de 20 paginas, e
quanto leva gerar 2000 palavras. Todo limite de tempo depende desses dois
numeros, e eles variam com a placa.
