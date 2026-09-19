# worker-gpu

Um processo, uma placa, um árbitro.

Esta máquina tem uma GPU e vários clientes. A placa é **indivisível**: um
modelo de texto de 30B já ocupa quase toda a VRAM de 8 GB, e a difusão precisa
de ~5,4 GB. Não cabem juntos — e o que acontece quando se tenta não é um erro,
é o processo caindo para CPU em silêncio, dezenas de vezes mais lento, sem
nada no log dizendo por quê.

Por isso **tudo** entra por aqui, inclusive o texto:

| Rota | O que faz |
|---|---|
| `POST /v1/chat/completions` | texto — repassa ao Ollama |
| `POST /v1/images/generations` | imagem — difusão |
| `POST /parse/` | PDF para Markdown com análise de layout |
| `GET /v1/models` | catálogo |
| `GET /health/` | estado, sem credencial |

As três primeiras disputam **um lock só**. Quem não pega recebe `503` com
`Retry-After` calculado.

> Integrando um cliente? **[`INTEGRACAO.md`](INTEGRACAO.md)** tem o guia
> completo. O resto deste arquivo é sobre operar a máquina.

## Por que um processo, e não três serviços

Foi três, e não funcionava. Cada serviço tinha um lock e protegia a si mesmo;
nenhum sabia dos outros. O Ollama, então, não participava de nada — ele decide
sozinho quando carregar e descarregar modelo.

O resultado era o esperado em retrospecto: a difusão encontrava a placa cheia,
caía para CPU, e um único lote consumia horas de processador e 17 GB de RAM.

Um lock em memória só é correto porque **todo** pedido de GPU entra no mesmo
processo. É também o que torna seguro mandar o Ollama soltar a VRAM: detendo o
lock, ninguém está gerando texto.

## Instalação

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env          # defina WORKER_SHARED_SECRET e BIND_HOST
./venv/bin/python baixar_modelo.py    # os ~7 GB do modelo de imagem, uma vez
./deploy/instalar.sh
```

O `baixar_modelo.py` não é opcional na prática. O serviço carrega o modelo de
forma preguiçosa, e o primeiro pedido de todos não carrega: **baixa**. Sem
isso, o primeiro cliente a pedir uma imagem espera minutos e leva um tempo
esgotado. O `/health/` informa `baixado`.

### O Ollama

Ele passa a ser **interno**: só o worker fala com ele. Tire-o da rede.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf <<'CONF'
[Service]
# Loopback. Quem publica na rede privada e o worker, que arbitra a placa.
# Deixar o Ollama exposto e deixar uma porta dos fundos sem arbitragem.
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
CONF

sudo systemctl daemon-reload && sudo systemctl restart ollama
```

`OLLAMA_KEEP_ALIVE` deixa de ser crítico: o worker descarrega o modelo quando
precisa da placa para imagem. Mantê-lo alto passa a ser vantagem — o texto não
recarrega à toa.

### O endereço de escuta

| Valor | Quem alcança |
|---|---|
| `127.0.0.1` | só esta máquina |
| `$(tailscale ip -4)` | os outros clientes, pela rede privada |
| `0.0.0.0` | a internet inteira. O instalador recusa |

Trocar exige **reinstalar a unit** (`./deploy/instalar.sh`): o endereço entra
na linha de comando do uvicorn.

### Sobe sozinho no boot?

| Instalação | Sobe quando |
|---|---|
| `./deploy/instalar.sh` (padrão, unit de **usuário**) | você faz login |
| o mesmo, **mais** `sudo loginctl enable-linger $USER` | no boot, sem login |
| `./deploy/instalar.sh --sistema` | no boot, sempre |

Numa máquina pessoal que também atende outros, `enable-linger` é o que você
quer.

## Medir antes de decidir

```bash
./venv/bin/python medir_imagem.py --tamanhos 512x288,768x432,1024x576
./venv/bin/python medir.py um-artigo.pdf --cpu --threads 1
```

Os dois medem a **segunda** execução de cada combinação: a primeira inclui a
montagem do pipeline, que se paga uma vez, e misturar as duas produz um número
que não serve para decidir nada.

### O que a medição já mostrou nesta placa

| tamanho | área relativa | tempo | pico VRAM |
|---|---|---|---|
| 512×288 | 1× | 14,5 s | 5,3 GB |
| 768×432 | 2,25× | 13,3 s | 5,4 GB |
| 1024×576 | 4× | 17,8 s | 5,4 GB |

Quatro vezes mais pixels por 23% mais tempo — e o menor foi *mais lento* que o
do meio. O custo dominante é mover pesos entre RAM e VRAM, que é fixo por
geração; a difusão em si é o troco.

**Consequência prática:** reduzir o tamanho para "economizar" não economiza. O
que muda o tempo é o modelo. E o pico de VRAM não cai com o tamanho — é por
isso que o árbitro, e não uma imagem menor, é a resposta para dividir a placa.

Em CPU, o mesmo 512×288 levou 160 s: 11 vezes mais, com qualidade pior.

## Diagnóstico

```bash
curl -s http://<endereco>:8090/health/ | jq
journalctl --user -u worker-gpu -f
```

| Sintoma | Causa provável |
|---|---|
| `503 gpu_ocupada` | funcionando como projetado; o cliente deve voltar depois |
| `503 sem_vram` | o Ollama não soltou a placa. Veja `ollama.carregados` no `/health/` |
| `503 ollama_indisponivel` | o Ollama caiu, ou `OLLAMA_URL` está errado |
| `/health/` dá `timed out` | um handler bloqueante no event loop — nenhum deveria ser `async def` |
| `baixado: false` | rode `baixar_modelo.py` antes do primeiro uso |
| `ultimo_dispositivo: cpu` | caiu para CPU. Com `IMAGEM_PERMITIR_CPU=nao` isso não deveria acontecer |
| uvicorn morre no boot | `BIND_HOST` inexistente, ou `BIND_PORT` vazio |

## Testes

```bash
./venv/bin/python -m pytest -q
```

Os modelos pesados não entram: `gerar_imagens`, `obter_conversor` e o Ollama
são substituídos. O que se exercita é o contrato HTTP, a arbitragem e as
recusas — e é lá que os erros deste repositório doem, porque do outro lado há
clientes que só veem JSON.

`contrato/` guarda os exemplos de resposta que os clientes copiam. Os testes
conferem que as respostas reais ainda têm aquela forma.
