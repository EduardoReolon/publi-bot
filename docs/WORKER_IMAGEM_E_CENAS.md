# Imagem com referência, foto na cena e duração exata no worker-gpu

O que o publi-bot espera do worker para fazer o **vídeo por cenas** das redes
(`apps/social/cenas.py`). Cada cena é uma animação curta (2 a 4 s) a partir de
uma imagem inicial; a voz e a legenda entram na montagem, do lado daqui.

O cliente já está pronto e testado contra esta especificação:

- imagem com referência e máscara: `OpenAICompatibleImageClient.editar`
  (`apps/inference/providers/openai_compatible.py`);
- seed na animação: `OpenAIVideoClient.criar` (`apps/inference/providers/animacao.py`).

Sem as mudanças no worker, o publi-bot continua funcionando: a cena sai só pela
descrição (sem o personagem de referência e sem a foto do lugar), com um aviso
na cena, e a duração curta é compensada na montagem.

> **Implementado no worker (contrato 2.12).** Diferenças em relação ao que está
> abaixo, já tratadas aqui:
> - o `/v1/images/edits` pode devolver `avisos` (lista de textos) quando ignorou
>   algo pedido (hoje: referências com o modelo de referência desligado). Eles
>   aparecem na cena;
> - `image` sem `mask` volta `422` (o publi-bot sempre manda os dois juntos), e a
>   máscara precisa de canal alfa e do tamanho da base (é RGBA, 720x1280);
> - o trabalho do `/v1/videos` devolve `seed` (a usada, inclusive a sorteada), e o
>   publi-bot guarda essa;
> - os trechos do FramePack são de ~1,2 s, com `round(seconds / 1,2)` trechos
>   (mínimo 1): o publi-bot pede `round(trechos × 1,2)` segundos;
> - com e sem referência são **modelos de imagem diferentes**, e só um fica
>   carregado por vez: o publi-bot gera todas as imagens iniciais antes de
>   animar, as com personagem juntas, para não recarregar.
>
> **Contrato 2.13:** o `/v1/videos` lê o `model` (catálogo em
> `animacao.modelos` do `/health/`: `framepack`, bf16, e `framepack-fp8`). O
> publi-bot anima no `ANIMACAO_MODELO_MENOR` (padrão `framepack-fp8`) e guarda,
> em cada cena, o `model` e a `seed` que voltaram; "Refazer em qualidade
> máxima" pede de novo no `ANIMACAO_MODELO_MAIOR` (padrão `framepack`) com a
> MESMA seed. Os dois nomes ficam no `.env`. Como toda cena automática usa o
> menor, a fila já sai agrupada por modelo.
>
> **Referência:** vai só UMA por cena (a do 1º personagem citado no texto, a 1ª
> figura da folha): o IP-Adapter mistura as referências numa aparência só.

---

## 1. Imagem com referência e foto na cena: `POST /v1/images/edits`

O dialeto de edição de imagem da OpenAI, com dois campos a mais.

```http
POST /v1/images/edits
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: multipart/form-data

model=<o modelo de imagem>
prompt=<o primeiro quadro, em inglês>
size=720x1280
n=1
response_format=b64_json
negative_prompt=<opcional>
image=<PNG base, opcional>
mask=<PNG máscara, opcional, do tamanho da base>
reference_image=<PNG>          (repetível, até 3: os personagens)
reference_strength=0.60        (0 a 1, só com reference_image)
```

**`200`:** igual ao `/v1/images/generations`: `{"data": [{"b64_json": "..."}]}`.

Os três jeitos de chamar:

| Campos | O que o worker faz | Para quê |
|---|---|---|
| `reference_image` (sem `image`) | gera do zero pelo `prompt`, puxando a aparência das referências | o Carlos e a Bia saírem parecidos em toda cena |
| `image` + `mask` (+ referências) | **pinta só a parte transparente da máscara** e mantém o resto exatamente como está | a foto real na janela (ou no alto do fundo) |
| `image` sem `mask` | não usado pelo publi-bot hoje | — |

**A máscara:** transparente (alfa 0) = pintar; opaco (alfa 255) = manter, como
na OpenAI. O publi-bot manda:

- **foto na janela:** a base é a cor de fundo da conta com a foto num retângulo
  no alto (à esquerda ou à direita). A máscara deixa opaco só esse retângulo. O
  `prompt` descreve a cena com "a window" ali, e o modelo pinta o resto da cena
  em volta da foto, que sai intacta;
- **lugar de fundo com personagem:** a base é a foto inteira. A máscara mantém
  a metade de cima (o céu, o prédio) e pinta a de baixo com o personagem.

### Sugestão de implementação (SDXL, placa de 8 GB)

- referência: **IP-Adapter** para SDXL (`h94/IP-Adapter`, `ip-adapter-plus_sdxl_vit-h`),
  com `set_ip_adapter_scale(reference_strength)`. Várias referências: uma lista
  de imagens (o diffusers aceita) ou a média dos embeddings;
- máscara: o mesmo SDXL num pipeline de pintura
  (`AutoPipelineForInpainting.from_pipe(...)`, sem carregar outro modelo), com a
  máscara invertida para a convenção do diffusers (branco = pintar);
- memória: `enable_model_cpu_offload()`, como já é feito no `/generations`.

### Erros

Iguais aos do `/generations` (503 ocupada, 500 com `error.code` quando quebra).
**404 ou 405 na rota** = worker sem a rota: o publi-bot cai para o
`/generations` só com o texto e avisa na cena.

`/health/` deve publicar `rotas.imagem_edicao: true`.

---

## 2. Animação: seed, tamanho e o trabalho que sobrevive

Mudanças pequenas no `POST /v1/videos` (seção 2 de `WORKER_VOZ_E_ANIMACAO.md`):

1. **`seed`** (inteiro, opcional, no multipart). A mesma imagem, o mesmo
   `prompt` e a mesma `seed` devolvem o mesmo clipe. O publi-bot guarda a seed
   de cada cena: "Refazer a animação" manda outra; um trabalho perdido (o
   worker reiniciou) é pedido de novo com a mesma. Sem `seed`, o worker sorteia
   (como hoje). O publi-bot só manda `seed` quando a conexão não é a Sora (que
   recusa campo desconhecido).

2. **`size` respeitado.** O publi-bot pede `448x800` (o "balde" 9:16 do
   FramePack, `ANIMACAO_TAMANHO` no `.env` daqui) e amplia para 1080x1920 na
   montagem. O tempo cresce com os pixels: o worker deve gerar no tamanho
   pedido (ou no balde mais próximo), sem subir para um maior.

3. **Duração.** O FramePack gera em trechos de ~1,2 s; o worker faz
   `round(seconds / 1,2)` trechos (contrato 2.12). O publi-bot pede o **menor**
   clipe que, desacelerado até 25% e com o último quadro parado até meio
   segundo, cobre a fala da cena, em segundos que caem exatamente nos trechos
   (2 trechos = 2 s, 3 = 4 s, 4 = 5 s): uma cena de 3 s custa 2 trechos, e não 3.

**O trabalho que sobrevive:** o publi-bot pede (`POST`), guarda o `id` na cena
e confere (`GET /v1/videos/{id}`) de minuto em minuto, sem prender ninguém
esperando. Worker desligado: a cena espera e as prontas ficam. Worker de volta
sem lembrar do trabalho (`404`): a cena é pedida de novo. Até 6 h sem resposta:
a cena desiste, com aviso.

---

## Contrato

Versão menor sobe (rotas e campos novos não quebram ninguém):

- `imagem-edicao-resposta.json`: `_rota`, `_nota` e o `data` com `b64_json`;
- `video-resposta.json`: a `_nota` cita `seed` e o arredondamento de `seconds`.

Depois, copie os dois para `tests/contrato_do_worker/` daqui.

---

## 3. Voz: como falar e as falas vizinhas (`POST /v1/audio/speech`)

Cada cena do vídeo é um pedido de voz. Lidas uma a uma, as falas soavam como
frases soltas de quem lê. O publi-bot manda, **só para o worker**, três campos a
mais no corpo:

```json
{
  "model": "kokoro",
  "voice": "qwen:carla",
  "input": "Trabalhando até tarde de novo pra fechar o mês?",
  "response_format": "mp3",
  "instructions": "Fale em portugues do Brasil como quem conversa com uma pessoa, ...",
  "previous_text": "a fala da cena anterior (ate 400 caracteres)",
  "next_text": "a fala da cena seguinte (ate 400 caracteres)"
}
```

> **Contrato 2.15:** o worker aceita os três campos, mas **ignora** os três nos
> dois motores, e diz isso em `/health/` → `voz.campos` (todos `false`). O
> clone do Qwen3-TTS (modelo Base) não tem controle por instrução, e as falas
> vizinhas exigiriam gerar três falas e recortar sem marcas de tempo. No worker,
> **o tom de cada voz vem da gravação de referência**: para variar o estilo, use
> vozes diferentes (ex.: `qwen:eduardo-vendas`, `qwen:eduardo-calmo`; veja o
> `GRAVAR_VOZ.md` do worker) e escolha a voz na Identidade visual da conta.
>
> O publi-bot continua mandando os campos: com outro motor eles podem passar a
> valer, e `voz.campos` vai dizer. Onde eles valem hoje: a ElevenLabs usa as
> falas vizinhas (`previous_text`/`next_text`), e o `gpt-4o-mini-tts` da OpenAI
> usa a instrução.

---

## 4. Conversão de voz (`POST /v1/audio/convert`)

> **Implementado no worker (contrato 2.16)**, com o conversor do Chatterbox
> (MIT), que aplica uma marca d'água inaudível. Detalhes no `INTEGRACAO.md` do
> worker, seção "Conversão de voz".

Em cada cena, a pessoa pode gravar (no navegador, ou enviar um arquivo) a fala
com a entonação que quer. A gravação é **só um guia**: ela nunca entra no
vídeo. Na montagem, o publi-bot pede ao serviço da voz do vídeo que a repita
no timbre dela: mesmas palavras, ritmo, pausas e ênfase, outra voz. A voz pode
ser a da conta (ex.: `qwen:carla`) ou **um modelo da voz da própria pessoa**
(ex.: `qwen:eduardo`, criado no worker com uma gravação boa): assim ela grava a
entonação em qualquer lugar, com barulho, e sai com a voz limpa dela.

A ElevenLabs também converte (speech-to-speech, com remoção de ruído); o
publi-bot usa quando a voz do vídeo é dela.

```http
POST /v1/audio/convert
Authorization: Bearer <WORKER_SHARED_SECRET>
Content-Type: multipart/form-data

audio=<a gravacao, mp3 (o publi-bot ja corta o silencio das pontas e iguala o volume)>
voice=qwen:carla        (so voz do Qwen: a referencia dela e o timbre de saida)
```

| Resposta | O que o publi-bot faz |
|---|---|
| `200`, MP3 (`audio/mpeg`) | usa a fala convertida e a guarda por voz + gravação |
| `422 voz_desconhecida` (a voz não existe, ou é do Kokoro, que não tem referência) | tenta a próxima conexão de voz; nenhuma converte: voz gerada, com aviso na cena |
| `422 pedido_invalido` (áudio vazio, ilegível ou acima de 120 s) | idem |
| `413 arquivo_grande` (acima de 25 MB) | idem (o publi-bot já recusa gravação acima de 20 MB) |
| `503 gpu_ocupada` com `Retry-After` | reagenda a montagem nesse tempo, como a voz do Qwen |
| `404 conversao_desligada` (rota desligada) | voz gerada, com aviso na cena |

- **Espera:** cada conversão carrega o modelo; o publi-bot espera até 300 s.
- **`/health/` → `rotas.conversao_de_voz`** diz se a rota está ligada. O
  publi-bot não consulta antes: tenta, e o `404` cai na voz gerada com aviso.

## 5. Acabamento de locução (contrato 2.17)

Toda fala que sai do worker (`/v1/audio/speech` e `/v1/audio/convert`) vem com
acabamento de locução pelo ffmpeg e o volume normalizado em -16 LUFS (MP3,
44,1 kHz). `/health/` → `voz.acabamento` diz se está ligado (`ligado`,
`ffmpeg`); sem ffmpeg no worker, a fala sai sem acabamento.

Do lado daqui nada muda: o publi-bot só normaliza a **gravação de entonação**
antes de mandá-la para a conversão (`LIMPAR_A_GRAVACAO`, também -16 LUFS), e a
montagem usa a voz como o worker devolveu.

