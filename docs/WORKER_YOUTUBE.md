# Rotas do YouTube no worker-gpu

O que o publi-bot espera do worker para tirar o texto de vídeos do YouTube.

## Por que no worker

O YouTube costuma recusar a leitura de legendas vinda de IP de servidor em
nuvem (`RequestBlocked`, `IpBlocked`). A máquina da placa está numa conexão
residencial, que normalmente passa. E, quando o vídeo não tem legenda, é lá que
está o Whisper.

## Ligar do lado do publi-bot

Em **Inferência**, na conexão do worker, marcar a carga **Legenda do YouTube**
(`youtube`). Sem ela, o servidor tenta sozinho, como hoje.

---

## 1. Legenda — `POST /v1/youtube/legenda` (implementada no publi-bot)

Cliente: `apps/knowledge/videos.py::_legenda_no_worker`.

```http
POST /v1/youtube/legenda
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: application/json

{"video_id": "dQw4w9WgXcQ", "idiomas": ["pt", "pt-BR", "en"]}
```

`idiomas` em ordem de preferência: devolver a primeira que existir (manual
antes de automática, dentro do mesmo idioma).

### Resposta (200)

```json
{
  "idioma": "pt",
  "gerada_automaticamente": true,
  "segmentos": [
    {"start": 0.0, "duration": 4.2, "text": "Olá."},
    {"start": 4.2, "duration": 5.6, "text": "Hoje vamos falar de BDI."}
  ]
}
```

O publi-bot usa `start` e `text` para montar uma seção por janela de 90 s, que é
o que a curadoria marca e o que permite citar o minuto certo.

### Erros

| Situação | Resposta | O publi-bot faz |
|---|---|---|
| O vídeo não tem legenda em nenhum dos idiomas | `404` + `error.code: "sem_legenda"` | o candidato vai para "aguardando o áudio" |
| O YouTube recusou também daqui | `503` + `error.code: "bloqueado"` | tenta do servidor; se falhar, "aguardando o áudio" |
| Rota inexistente / worker desligado | `404` sem código / conexão recusada | tenta do servidor |

### Sugestão de implementação

A mesma biblioteca que o servidor usa, `youtube-transcript-api` (versão 1.x):

```python
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

@app.post("/v1/youtube/legenda")
def legenda(pedido: PedidoDeLegenda, _=Depends(autenticar)):
    try:
        lista = YouTubeTranscriptApi().list(pedido.video_id)
        try:
            t = lista.find_manually_created_transcript(pedido.idiomas)
        except NoTranscriptFound:
            t = lista.find_generated_transcript(pedido.idiomas)
        dados = t.fetch()
    except (NoTranscriptFound, TranscriptsDisabled):
        return JSONResponse({"error": {"code": "sem_legenda"}}, status_code=404)
    except Exception as exc:  # RequestBlocked, IpBlocked...
        return JSONResponse({"error": {"code": "bloqueado", "message": str(exc)}}, status_code=503)
    return {
        "idioma": t.language_code,
        "gerada_automaticamente": t.is_generated,
        "segmentos": [{"start": s.start, "duration": s.duration, "text": s.text} for s in dados],
    }
```

Não usa a placa: não entra no lock da GPU.

---

## 2. Áudio do vídeo transcrito — `POST /v1/youtube/transcricao` (opcional; o publi-bot ainda não chama)

Para o vídeo sem legenda: o worker baixa **só o áudio** e transcreve com o
Whisper, poupando a pessoa de baixar e enviar o arquivo.

> **Atenção:** os Termos de Serviço do YouTube proíbem baixar conteúdo sem um
> botão ou link de download oferecido pelo próprio YouTube. É o mesmo que a
> pessoa já faz à mão hoje, mas automatizado. Decida se quer essa rota; se
> quiser, o publi-bot passa a chamá-la no lugar de "aguardando o áudio".

```http
POST /v1/youtube/transcricao
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: application/json

{"video_id": "dQw4w9WgXcQ", "language": "pt"}
```

Resposta: **a mesma** de `/v1/audio/transcriptions` com
`response_format=verbose_json` (`text`, `language`, `duration`, `segments[]`) —
veja `docs/WORKER_TRANSCRICAO.md`, inclusive a tabela de erros (503
`gpu_ocupada` com `Retry-After`, 503 `timeout`, 500 `worker_travado`).

Sugestão: `yt-dlp -f bestaudio -x --audio-format m4a` para um diretório
temporário, depois o mesmo caminho do `/v1/audio/transcriptions` (mesmo lock,
mesmo Whisper), e apagar o arquivo no fim. Vídeo acima de 2 h: `422` com
`error.message`.
