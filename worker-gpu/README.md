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
| `imagem-api` | 8101 | Imagem de capa (difusao) |

Os tres dividem a mesma placa. Como, e o assunto de
[Dividir a placa](#dividir-a-placa) mais abaixo — e nao e um detalhe: uma placa
de 8 GB nao comporta dois desses modelos ao mesmo tempo.

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
./deploy/instalar.sh   # unit de usuario; --sistema para unit de sistema
```

O `instalar.sh` preenche os caminhos desta maquina no molde da unit, habilita,
sobe e confere o `/health/`. Antes disso ele recusa o que so daria erro depois:

| Recusa | Por que |
|---|---|
| venv ausente | nada a executar |
| `WORKER_SHARED_SECRET` vazio | o servico sobe e responde 500 a toda chamada |
| `BIND_HOST=0.0.0.0` | publica a placa na internet |
| `BIND_HOST` em que nao da para escutar | o uvicorn morre no boot e o systemd reinicia a cada 10s |
| `BIND_PORT` / `IMAGEM_BIND_PORT` ausente | o systemd passa string vazia, e o uvicorn recusa |

A conferencia de endereco pega tres coisas de uma vez: um valor de exemplo
nunca substituido, o endereco da Tailscale com o `tailscaled` parado, e um IP
que mudou de lugar. Se o `/health/` mesmo assim nao responder, o instalador
imprime o fim do journal da unit — a causa na tela, e nao um comando para
rodar depois.

Unit de **usuario** e o padrao, e e o que faz sentido num computador pessoal:
nao pede sudo e sobe junto com a sua sessao. Para mante-la de pe com a maquina
ligada e ninguem logado:

```bash
sudo loginctl enable-linger "$USER"
```

Use `--sistema` numa maquina dedicada, que precisa subir o servico no boot.

### Servico de imagem de capa

Mesmo venv e mesmo `.env` do Docling, porta 8101:

```bash
./venv/bin/pip install -r requirements.txt   # traz diffusers e accelerate
./deploy/instalar.sh --imagem                # ou --tudo, para os dois
```

O que o `.env` controla:

```bash
IMAGEM_MODELO=stabilityai/stable-diffusion-xl-base-1.0
IMAGEM_DEVICE=auto          # cpu | cuda | auto
IMAGEM_PASSOS=25            # 1 a 4 nos modelos "turbo", com GUIDANCE=0
IMAGEM_OCIOSO_SEGUNDOS=300  # tempo sem pedido ate devolver a placa
```

Ele fala o dialeto de imagem da OpenAI (`POST /v1/images/generations`,
resposta em `b64_json`), que e o unico que o PubliBot conhece. A consequencia
util: trocar este servico por um provedor pago e mudar a URL e a chave da
conexao, sem tocar em codigo nenhum.

O `instalar.sh --imagem` recusa subir se o venv nao tiver o `diffusers` — o
caso de quem instalou o worker antes deste servico existir, e cujo sintoma
seria um `ModuleNotFoundError` no journal.

### Python 3.14

Duas dependencias do Docling tem marcador `python_version < "3.14"`, e nesse
Python elas simplesmente nao entram:

| Pacote | Consequencia |
|---|---|
| `rapidocr` | sem OCR. O servico recusa subir com `DOCLING_OCR=true`. |
| `opencv` (vinha junto do rapidocr) | quebraria a analise de TABELA |

O segundo e o pior, porque `docling-ibm-models` declara o opencv apenas como
extra opcional: sem o rapidocr, ninguem o instala, e a falha aparece so na
primeira conversao, como `ModuleNotFoundError: No module named 'cv2'`. Por isso
o `requirements.txt` daqui fixa `opencv-python-headless` explicitamente.

Se voce precisa de OCR, use Python 3.12 ou 3.13 no venv do worker.

#### Sem placa, por enquanto

**O Docling nao exige GPU.** A analise de layout — que e o que o distingue do
extrator local — roda em CPU; a placa muda o tempo, nao o resultado. Da para
subir este servico numa maquina comum, inclusive na mesma da aplicacao, e
trocar depois.

```bash
DOCLING_DEVICE=cpu     # cpu | cuda | auto
DOCLING_THREADS=4      # so em CPU; 0 deixa o Docling decidir
DOCLING_OCR=false      # OCR e a parte mais cara; artigo com texto nao precisa
```

Quando a placa existir:

```bash
DOCLING_DEVICE=cuda
sudo systemctl restart docling-api
curl -s http://127.0.0.1:8100/health/    # {"device": "cuda", "ocr": false, ...}
```

**Nada muda no PubliBot**: ele fala HTTP e nao sabe onde o modelo roda. A fila do
Celery, o adiamento quando o worker esta ocupado e a conferencia de `sha256`
continuam iguais. E por isso que vale montar o caminho cedo, mesmo lento.

O `/health/` devolve `device` e `ocr` de proposito: sem isso, um `.env` mal
editado deixa o servico na CPU sem ninguem perceber, e o sintoma seria apenas
"esta demorando muito".

A primeira conversao baixa os modelos de layout do HuggingFace (algumas centenas
de MB). Numa rede que bloqueie `huggingface.co` isso falha com `ProxyError` no
meio da conversao — nao no boot.

## Cadastro no PubliBot

Pelo terminal da nuvem, com `CONVERSAO_BASE_URL` e `CONVERSAO_SEGREDO` no
`.env` de la (esse segredo e o MESMO `WORKER_SHARED_SECRET` daqui):

```bash
python manage.py configurar_conversao --testar
```

O `--testar` chama `/health/` e imprime o dispositivo em uso. Vale o segundo
que custa: o erro mais comum aqui nao e de configuracao e sim de rede — o
servico escutando num endereco que a nuvem nao alcanca, ou o Tailscale fora do
ar. Sem essa confirmacao isso so apareceria dentro de um job.

Para medir antes de decidir entre CPU e placa, nesta maquina:

```bash
python medir.py um-artigo.pdf --cpu --threads 1   # pior caso, um nucleo
python medir.py um-artigo.pdf --cuda
```

Ou, pelo painel, em Conexoes de inferencia:

| Campo | Ollama | Docling | Imagem |
|---|---|---|---|
| Tipo | Compativel com OpenAI | Docling | Geracao de imagem |
| URL base | `http://100.x.y.z:11434` | `http://100.x.y.z:8100` | `http://100.x.y.z:8101` |
| Cargas | `["text"]` | `["vision_parse"]` | `["image"]` |
| Concorrencia maxima | **1** | **1** | **1** |

Concorrencia 1 nos tres, e a mesma maquina: sao a mesma placa. Deixar 2 em
qualquer um deles reintroduz exatamente o problema de VRAM descrito acima.

Ou pelo terminal, que e o caminho sem formulario:

```bash
python manage.py configurar_imagem --testar
```

## Dividir a placa

Numa RTX 3050 de 8 GB cabe **um** modelo grande de cada vez: um de texto de
7-8B quantizado, **ou** um de imagem. Nunca os dois inteiros.

Nao ha arranjo perfeito para isso, e o sistema nao finge que ha. Sao cinco
camadas, cada uma cobrindo o que a anterior deixa passar:

1. **A reserva do PubliBot conta vagas por MAQUINA**, e nao por conexao
   (`apps/inference/leases.py`). As tres conexoes apontam para o mesmo host,
   entao gerar texto e gerar imagem se revezam. Cobre o caminho normal.
2. **Um pedido por vez dentro de cada servico** (503 no segundo). Cobre quem
   chamar por fora do PubliBot.
3. **Os pesos do modelo de imagem ficam na RAM**, nao na VRAM
   (`enable_model_cpu_offload`): o pico cai de ~7 GB para perto de 3,5 GB, e e
   isso que permite o Ollama seguir carregado ao lado.
4. **A placa e devolvida depois de `IMAGEM_OCIOSO_SEGUNDOS` sem pedido.**
5. **Faltando VRAM, a imagem e refeita em CPU** — com WARNING no log e
   `ultimo_dispositivo=cpu` no `/health/`. Leva minutos em vez de segundos,
   mas nao falha.

O que nenhuma cobre: o Ollama decide sozinho quando carregar modelo e nao
participa de reserva nenhuma. Quando ele carrega um modelo grande no meio de
uma geracao de imagem, quem entra e a camada 5 — e e exatamente por isso que
ela existe, em vez de o pedido simplesmente falhar.

A camada 5 e a unica que produz um resultado pior sem falhar, entao ela grita:
`configurar_imagem --testar` relatando `ultimo=cpu` significa que a placa esta
apertada. Reduza `IMAGEM_OCIOSO_SEGUNDOS`, gere em 512x512, ou use um modelo
menor.

**Meça antes de confiar:** quanto o Docling leva num artigo de 20 paginas, e
quanto leva gerar 2000 palavras. Todo limite de tempo depende desses dois
numeros, e eles variam com a placa.
