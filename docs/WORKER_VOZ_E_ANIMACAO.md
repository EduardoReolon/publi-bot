# Rotas de voz e de animação no worker-gpu

O que o publi-bot espera do worker para narrar e animar os vídeos (Reels) das
redes com a placa própria, sem pagar API. O cliente já está pronto e testado
contra esta especificação; falta a rota do lado do worker:

- voz: `apps/inference/providers/voz.py::OpenAIVoiceClient`;
- animação: `apps/inference/providers/animacao.py::OpenAIVideoClient`.

As duas rotas falam o **dialeto da OpenAI**, como texto, imagem e transcrição.
Assim a mesma conexão serve à placa própria ou à API paga, trocando só o
endereço: o worker é substituível pela OpenAI (voz `gpt-4o-mini-tts`,
animação `sora-2`), e vice-versa.

## Como o publi-bot liga

```bash
manage.py configurar_api worker_voz          # endereço e segredo: IMAGEM_BASE_URL / IMAGEM_SEGREDO
manage.py configurar_api worker_animacao
```

Sem as rotas no worker, o resultado é um 404. O post ganha o aviso, o vídeo
segue com a imagem parada (zoom lento) e o roteiro continua lá para gravar
fora.

---

## 1. Voz: `POST /v1/audio/speech`

```http
POST /v1/audio/speech
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: application/json

{"model": "kokoro", "voice": "pf_dora", "input": "Você acorda cansado?", "response_format": "mp3"}
```

Resposta `200`: o áudio, `Content-Type: audio/mpeg` (ou `audio/wav`, se o MP3
não estiver disponível; o publi-bot lê os dois pelo ffmpeg).

- **`voice`** é o campo **"voz da narração"** da conta (Identidade visual).
  Nomes de voz da OpenAI (`alloy`, `nova`...) ou vazio usam a voz padrão do
  worker (`VOZ_PADRAO`).
- **`model`** é informativo; manda o `.env` do worker.
- Textos curtos: uma fala por lâmina, até ~400 caracteres.

### Sugestão: Kokoro-82M

- Licença Apache-2.0; tem vozes de português do Brasil: `pf_dora` (feminina),
  `pm_alex` e `pm_santa` (masculinas).
- Fala português pelo `espeak-ng` (`apt install espeak-ng`).
- Roda bem em **CPU**, mais rápido que tempo real. Assim não precisa do lock da
  placa e não disputa VRAM com o Ollama nem com a difusão.

```python
from kokoro import KPipeline
pipeline = KPipeline(lang_code="p")          # p = português do Brasil
partes = [audio for _, _, audio in pipeline(texto, voice="pf_dora")]
# 24 kHz; MP3 com soundfile (libsndfile >= 1.1): sf.write(buf, audio, 24000, format="MP3")
```

Em GPU, entrar no árbitro como `voz` (estimativa de 10 s).

`/health/` deve publicar `rotas.voz: true` e um bloco `voz` com `modelo`,
`dispositivo` e `voz_padrao`.

---

## 2. Animação (imagem para vídeo): `POST /v1/videos`

O dialeto de vídeo da OpenAI (Sora). Ele é **assíncrono**, porque animar leva
minutos:

```http
POST /v1/videos
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: multipart/form-data

prompt=<o movimento, em inglês>
model=ltx-video
seconds=4
size=720x1280
input_reference=<a imagem PNG, 720x1280>
```

**`200`:**

```json
{"id": "video_abc", "object": "video", "status": "queued", "model": "ltx-video",
 "progress": 0, "seconds": "4", "size": "720x1280", "created_at": 1760000000}
```

**`GET /v1/videos/{id}`**: o mesmo objeto, com `status` em `queued`,
`in_progress`, `completed` ou `failed`, mais `progress` (0–100) e, quando
falha, `error: {"code", "message"}`.

**`GET /v1/videos/{id}/content`**: o MP4 (`Content-Type: video/mp4`).

O publi-bot consulta a cada 10 s, por até 30 min.

### A imagem que chega

Ela vem pronta em 720x1280. A lâmina (4:5) fica no meio, inteira, e a cor de
fundo da conta preenche em cima e embaixo. O publi-bot recorta o meio do clipe
de volta para 4:5. Por isso o worker deve:

- respeitar o enquadramento (sem zoom nem corte que mude a composição);
- gerar no tamanho pedido ou num menor de mesma proporção, porque o publi-bot
  reescala.

### Árbitro e 503: o que muda por ser assíncrono

- **No `POST`**, o worker tenta tomar a placa (`ARBITRO.usar("animacao")`):
  - ocupada: `503 gpu_ocupada` com `Retry-After`, como em qualquer rota;
  - livre: o lock passa para uma thread que gera, e o `POST` responde já com
    o `id`.
  - O `threading.Lock` pode ser solto por outra thread, então a thread de
    geração solta o lock no fim, com o mesmo `__exit__`.
- Os trabalhos ficam **em memória** (com o MP4 num diretório temporário,
  apagado 1 h depois de pronto). Um restart perde os trabalhos: o `GET` de um
  `id` desconhecido devolve `404`, e o publi-bot trata como transitório e pede
  de novo (sem gastar tentativa). Isso não contraria o "nada de fila", porque
  cada trabalho só entra com a placa tomada.
- **Orçamento** (`ANIMACAO_TEMPO_MAXIMO`, ex.: 1500 s, abaixo dos 30 min do
  cliente): estourou → `status: failed`, `error.code: "timeout"`.
- Falta de VRAM → `failed` com `error.code: "sem_vram"`. O publi-bot põe o
  aviso na lâmina, e a pessoa escolhe outra forma.

| Situação | Resposta | O publi-bot faz |
|---|---|---|
| Placa ocupada no `POST` | `503` + `Retry-After` | tenta de novo em 10 min, sem gastar tentativa |
| `id` desconhecido (restart) | `404` no `GET` | pede de novo, sem gastar tentativa |
| `failed` | `200` com `error.message` | aviso na lâmina, sem repetir |
| Rota inexistente | `404` no `POST` | aviso: "configure a animação" |

### Sugestão: LTX-Video (imagem para vídeo) pelo diffusers

- Pipeline: `LTXImageToVideoPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=torch.bfloat16)`.
- Com `enable_model_cpu_offload()` e `vae.enable_tiling()`, como a rota de
  imagem faz.
- Parâmetros: ~512x768 (múltiplos de 32), `num_frames = 8k+1` (97 ≈ 4 s a 24 fps)
  e `num_inference_steps` 30–40.
- `export_to_video(frames, caminho, fps=24)`.
- Descarregar o Ollama antes, como na imagem. Liberar a placa depois de ocioso.
- **Não medido numa placa de 8 GB.** Meça como o `medir_imagem.py` antes de
  ligar (`ANIMACAO_ATIVA=nao` de padrão). Se não couber, as alternativas são o
  Wan 2.1 (1,3B, mas é texto para vídeo) ou deixar a animação para a Sora,
  pela API.

`/health/` deve publicar `rotas.animacao` e um bloco `animacao` com `modelo`,
`carregado`, `trabalhos` (em curso e prontos) e `tempo_maximo`.

---

## Contrato

São dois exemplos novos em `contrato/`, e a versão menor sobe (rotas novas não
quebram ninguém):

- `voz-resposta.json`: `_rota`, `_nota`, `content_type: "audio/mpeg"`;
- `video-resposta.json`: o objeto do trabalho, acima.

Depois, copie os dois para `tests/contrato_do_worker/` daqui.
