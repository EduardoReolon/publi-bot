# Integrando um cliente ao worker-gpu

Guia para adaptar um sistema que hoje fala com o Ollama direto — ou que não
falava com nada.

O worker é um **árbitro de GPU**, não um provedor. A diferença aparece num
ponto só, e é o ponto inteiro deste documento: **ele recusa quando a placa
está ocupada, e espera que você tente de novo.**

## O que muda no seu cliente

| Antes | Depois |
|---|---|
| `http://gpu:11434/v1/chat/completions` | `http://gpu:8090/v1/chat/completions` |
| sem credencial | `Authorization: Bearer <segredo>` |
| 200 ou erro | 200, ou **503 com `Retry-After`** |
| `stream: true` funcionava | `stream` é recusado |

O corpo e a resposta são os mesmos do dialeto da OpenAI. Se o seu cliente já
usa uma biblioteca compatível, trocar a URL base e a chave costuma bastar —
**menos o 503**, que é o que exige código novo.

## O contrato do 503

Esta é a parte que decide se a integração vai funcionar sob carga.

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 40

{"error": {"code": "gpu_ocupada",
           "message": "a GPU esta em uso por 'imagem'; tente em 40s",
           "ocupante": "imagem"}}
```

Quatro códigos, e eles pedem coisas diferentes:

| `error.code` | Significa | O que fazer |
|---|---|---|
| `gpu_ocupada` | outro trabalho está na placa | reagendar para daqui a `Retry-After` |
| `ollama_indisponivel` | o Ollama caiu ou reinicia | idem; se persistir por horas, alertar |
| `sem_vram` | a placa não tem espaço agora | idem, com paciência maior |
| `timeout` | o trabalho passou do orçamento | **não repita igual** — reduza o pedido |

**Nenhum deles é falha do seu trabalho.** Um 503 não deve consumir tentativa,
não deve abrir disjuntor, e não deve marcar o trabalho como falho. Se o seu
sistema conta tentativas, esse é o detalhe que mais importa: tratando 503 como
erro, alguns minutos de disputa esgotam as tentativas de uma fila inteira.

### Por que 503 e não uma fila no worker

Porque o worker **não tem estado durável**. Uma fila em memória perde trabalho
no primeiro restart, e restart aqui é rotina — atualização, troca de modelo,
reboot da máquina.

Você já tem fila persistente e sabe retomar. Enfileirar no worker seria uma
segunda fila, invisível para você e pior que a que já existe.

### O `Retry-After` é calculado

Ele vem do que está rodando agora, não de uma constante. Se uma imagem começou
há 20 segundos e costuma levar 60, você recebe `Retry-After: 40`. Respeite-o:
voltar antes garante outro 503, e voltar muito depois desperdiça placa ociosa.

## Como implementar do seu lado

Se o seu sistema tem orquestrador com passos, o padrão é este — separar
"adiar" de "falhar":

```python
resposta = requests.post(f"{WORKER}/v1/chat/completions", json=corpo,
                         headers={"Authorization": f"Bearer {SEGREDO}"},
                         timeout=600)

if resposta.status_code == 503:
    dados = resposta.json().get("error", {})
    espera = int(resposta.headers.get("Retry-After", 60))

    if dados.get("code") == "timeout":
        # Repetir igual daria o mesmo resultado. Reduza antes.
        raise TrabalhoPrecisaDeAjuste(dados.get("message", ""))

    # Adiar NAO gasta tentativa: nada deu errado, so nao era a hora.
    raise Adiar(dados.get("message", ""), tentar_em_segundos=espera)

resposta.raise_for_status()
```

Se o seu sistema não tem orquestrador, o mínimo aceitável é reagendar a tarefa
para `agora + Retry-After` e sair — **nunca** um `sleep` segurando o processo:
a placa pode estar ocupada por minutos, e você prende um worker inteiro
esperando.

## Timeouts do seu lado

| Rota | Timeout sugerido |
|---|---|
| `/v1/chat/completions` | 600 s |
| `/v1/images/generations` | 300 s |
| `/parse/` | 600 s |
| `/health/` | 10 s |

São generosos de propósito: o worker pode estar carregando um modelo (dezenas
de segundos) antes de começar. Um timeout curto desiste de um trabalho que
estava indo bem — e, pior, o worker continua trabalhando e segurando a placa,
de modo que a sua retentativa imediata bate num serviço ocupado.

## As rotas

### Texto

```http
POST /v1/chat/completions
Authorization: Bearer <segredo>

{"model": "qwen2.5:7b-instruct",
 "messages": [{"role": "system", "content": "..."},
              {"role": "user", "content": "..."}],
 "temperature": 0.2,
 "stream": false}
```

Resposta: `contrato/texto-resposta.json`.

`stream: true` recebe **422**. O árbitro precisa saber quando o trabalho
termina para soltar a placa, e uma resposta em streaming só termina quando o
cliente termina de ler.

### Imagem

```http
POST /v1/images/generations
Authorization: Bearer <segredo>

{"prompt": "...", "n": 3, "size": "1024x576"}
```

Resposta: `contrato/imagem-resposta.json`. Sempre `b64_json` — um link
temporário expiraria antes de você publicar a imagem.

Cada lado do `size` precisa ser múltiplo de 8. Não é capricho: o modelo
arredonda por dentro e devolveria uma imagem de tamanho diferente do pedido,
sem avisar.

O tamanho quase não muda o tempo (veja o README): o custo é dominado por mover
pesos entre RAM e VRAM. Peça o tamanho que você quer publicar.

### Conversão de PDF

```http
POST /parse/
Authorization: Bearer <segredo>
X-Expected-Sha256: <opcional>

multipart/form-data, campo `file`
```

Resposta: `contrato/conversao-resposta.json`.

Mande o `X-Expected-Sha256`. Sem ele, um arquivo truncado no caminho é
convertido em silêncio, e o Markdown de um documento que ninguém pediu entra
no seu acervo.

### Saúde

`GET /health/`, sem credencial. Use para diagnóstico, não como teste de
disponibilidade antes de cada pedido: entre o `/health/` e o pedido a placa
pode ter sido tomada, e você teria feito duas viagens para chegar no mesmo
503.

## Mantendo o contrato honesto

`contrato/*.json` são os exemplos de resposta. **Copie-os para os testes do
seu cliente** e exercite o seu adaptador contra eles.

Isso pega a divergência dos dois lados: aqui, um teste confere que a resposta
real ainda tem aquela forma; no seu repositório, que o seu código ainda lê
aquela forma.

O que não pega é a sua cópia envelhecer. Por isso todo exemplo tem
`_contrato_versao`, e o worker publica a dele:

```bash
curl -s http://<worker>:8090/health/ | jq -r .contrato_versao
```

Compare de vez em quando — ou num teste, se o seu CI alcança o worker. Versão
maior diferente significa que algo mudou de forma incompatível, e há uma seção
nova neste arquivo explicando o quê.

## Checklist

- [ ] URL base aponta para o worker, não para o Ollama
- [ ] `Authorization: Bearer` em toda chamada (menos `/health/`)
- [ ] 503 **adia**, não falha, e não consome tentativa
- [ ] `Retry-After` é respeitado
- [ ] `error.code == "timeout"` não é repetido igual
- [ ] Sem `stream: true`
- [ ] Timeouts generosos
- [ ] Exemplos de `contrato/` copiados para os seus testes
- [ ] Reagendar, nunca `sleep`
