# Rota de transcrição de áudio no worker-gpu

O que o publi-bot espera do worker para transcrever áudio de vídeo. O cliente
já está pronto (`apps/knowledge/extraction.py::_transcrever_no_worker`) e
testado contra esta especificação; falta a rota do lado do worker.

## Quando é usada

Um vídeo do YouTube aprovado como fonte cuja legenda não veio — o vídeo não
tem legenda, ou o YouTube recusou a leitura a partir do servidor. A pessoa
baixa o áudio e envia pela tela **Documentos › Fontes sugeridas**, ou envia
qualquer arquivo de áudio em **Enviar documento**. O documento entra na fila de
conversão e o passo de conversão chama esta rota.

## A rota

```http
POST /v1/audio/transcriptions
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: multipart/form-data

file=<o arquivo de áudio>
model=whisper
language=pt
response_format=verbose_json
```

É o dialeto da OpenAI (`/v1/audio/transcriptions`), como as rotas de texto e
imagem. `model` é informativo (o worker usa o modelo do `.env` dele);
`language` é o código ISO de duas letras, e pode ser ignorado se o worker
detectar o idioma.

Formatos que o publi-bot envia: `.mp3 .m4a .wav .ogg .oga .opus .webm .mp4
.flac`. O `ffmpeg` que o `faster-whisper` usa lê todos.

### Resposta (200)

```json
{
  "text": "Olá. Hoje vamos falar de BDI...",
  "language": "pt",
  "duration": 812.4,
  "segments": [
    {"start": 0.0, "end": 4.2, "text": "Olá."},
    {"start": 4.2, "end": 9.8, "text": "Hoje vamos falar de BDI."}
  ]
}
```

O publi-bot usa `segments[].start` e `segments[].text` para montar uma seção
por janela de 90 s (`## 03:15`), que é o que a curadoria marca e o que permite
citar o minuto certo. Sem `segments`, usa `text` inteiro numa seção só.
`duration` vai para o registro de tempo da conversão.

### Erros — o mesmo contrato das outras rotas

| Situação | Resposta | O publi-bot faz |
|---|---|---|
| Placa ocupada | `503` + `error.code` (`gpu_ocupada`) + `Retry-After` | adia, sem gastar tentativa |
| Orçamento de tempo estourado | `503` + `error.code: "timeout"`, sem `Retry-After` | falha — o mesmo áudio daria o mesmo resultado |
| Trabalho travado | `500` + `error.code` (`worker_travado`) | falha, sem repetir |
| Conexão recusada | — | adia |
| Conexão cortada no meio | — | falha (o worker morreu no trabalho) |
| Rota inexistente | `404` | falha, com mensagem apontando para este arquivo |
| Arquivo inválido | `400`/`422` com `error.message` | falha, com a mensagem |

### Árbitro e tempo

- Entra no **mesmo lock** da placa que texto, imagem e conversão: Whisper
  compete pela VRAM com o Ollama como o Docling compete.
- Deve descarregar o modelo de texto do Ollama antes de carregar o Whisper,
  como a rota de imagem faz.
- O cliente espera até **30 minutos** (`timeout` de 1800 s). Um áudio de uma
  hora com `faster-whisper` `medium`/`large-v3` em GPU de 8 GB fica bem abaixo
  disso; o prazo duro do worker (equivalente a `IMAGEM_TEMPO_TRAVADO`) deve
  ficar abaixo de 1800 s para o 500 legível chegar antes do timeout do cliente.
- Descarregar o Whisper depois de ocioso, como o Docling
  (`CONVERSAO_OCIOSO_SEGUNDOS`).

### Sugestão de implementação

`faster-whisper` (CTranslate2), modelo `large-v3` em `int8_float16` numa placa
de 8 GB, ou `medium` se faltar VRAM ao lado do que fica residente. VAD ligado
(`vad_filter=True`) para não transcrever silêncio.

### `/health/`

Um bloco `transcricao` como o de `conversao`: `modelo`, `dispositivo`,
`carregado`, `tempo_travado`. E `rotas.transcricao: true|false`.

## Também desejável: `/parse/` aceitar mais formatos

Hoje o `/parse/` configura o Docling só para PDF. O publi-bot lê DOCX, PPTX e
XLSX localmente (esses formatos declaram a estrutura), então isso não bloqueia
nada. Mas o Docling lê esses formatos com mais fidelidade em tabelas e
figuras; se o worker passar a aceitá-los, basta tirar as extensões de
`EXTENSOES_ESTRUTURADAS` em `apps/knowledge/estruturados.py` para o publi-bot
mandá-los ao worker.
