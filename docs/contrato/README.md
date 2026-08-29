# Contrato de Integracao `/api/v1`

Especificacao das rotas que um site precisa implementar para receber conteudo.

**O contrato e agnostico de linguagem e plataforma.** Nada aqui pressupoe
Django, WordPress ou qualquer outro sistema: sao rotas HTTP com JSON. A
implementacao de referencia em `reference/django/` existe como exemplo, nao
como requisito.

## Direcao das chamadas

**Todas as requisicoes partem do PubliBot.** O site nunca chama o PubliBot.
Isso simplifica a operacao do lado do cliente: nao ha credencial de saida para
guardar, nem rota de callback para expor.

| Metodo | Rota | Obrigatoria | Recurso |
|---|---|---|---|
| `GET` | `/api/v1/health/` | Sim | — |
| `GET` | `/api/v1/seo-context/` | Sim | — |
| `POST` | `/api/v1/publish/` | Sim | — |
| `POST` | `/api/v1/author-photos/` | Nao | `author_photo` |
| `GET` | `/api/v1/pending-questions/` | Nao | `qa` |
| `POST` | `/api/v1/pending-questions/ack/` | Nao | `qa` |
| `GET` | `/api/v1/publications/` | Nao | `reconciliation` |

## Autenticacao

Toda requisicao carrega quatro cabecalhos:

```
X-API-KEY:   <chave do site>
X-Timestamp: <segundos desde a epoca>
X-Nonce:     <uuid4, unico por requisicao>
X-Signature: v1=<hex>
```

Onde a assinatura e:

```
HMAC-SHA256(segredo, "{timestamp}.{nonce}.{sha256_hex(corpo_bruto)}")
```

### Regras obrigatorias na verificacao

**1. Calcule sobre o corpo BRUTO, antes de interpretar o JSON.** Reserializar
antes de conferir produz digests diferentes para o mesmo conteudo, e a
assinatura falha de forma aparentemente aleatoria.

**2. Compare em tempo constante.** A escrita natural (`if recebida != esperada`)
e curto-circuitada byte a byte: o tempo de resposta revela quantos bytes
iniciais estao corretos, e sem limite de tentativas a extracao byte a byte do
valor esperado e viavel.

| Linguagem | Funcao |
|---|---|
| Python | `hmac.compare_digest()` |
| PHP | `hash_equals()` |
| Go | `hmac.Equal()` |
| Node | `crypto.timingSafeEqual()` |

**3. Recuse instante fora da janela de 300 segundos.** Sem isso, uma requisicao
capturada hoje continua valida indefinidamente.

**4. Guarde os nonces por 600 segundos e recuse repeticao.** E o que impede
reexecutar uma requisicao interceptada dentro da janela.

**5. Responda o MESMO corpo e status para chave ausente, malformada ou
incorreta.** Mensagens diferentes vazam a mesma informacao por outro canal.

**6. Exija TLS.** Recuse HTTP simples com `403`. Sem TLS a chave trafega em
texto claro a cada requisicao.

## Idempotencia

`POST /api/v1/publish/` recebe o cabecalho `Idempotency-Key`.

**Guarde essa chave com indice UNICO.** Ao receber uma chave ja processada,
responda `200` com os dados da publicacao existente — **nao crie outra**.

O motivo e um cenario comum, nao hipotetico: o site grava o artigo e responde
`201`, a resposta se perde na rede, e o PubliBot reenvia. Sem idempotencia, o
mesmo conteudo e publicado duas vezes. Conteudo duplicado e exatamente o
problema que este produto existe para evitar.

## Foto do autor: duas etapas

**O cadastro de autor vive somente no PubliBot.** O site nao mantem cadastro
proprio e nao precisa criar um: nome, credenciais, bio, contato e redes chegam
dentro de cada publicacao, em `author`. Quem valida o conteudo responde por
ele, e nao ha a pergunta impossivel de "qual dos dois lados tem o cadastro mais
recente".

A foto e a unica excecao, e por um motivo tecnico: e um binario. Ela nao vai no
corpo da publicacao.

```
1. POST /publish/          { ..., "author": { "reference": "...", "has_photo": true } }
2. 201                     { ..., "author_photo_required": true }
3. POST /author-photos/    multipart: author_reference + sha256 + photo (WebP)
4. 202                     { "status": "accepted" }
```

**Por que perguntar em vez de sempre enviar.** So o site sabe se ja tem aquele
arquivo. Enviar sempre gastaria um upload por artigo publicado; nunca enviar
deixaria a caixa de autor sem foto para sempre.

**Por que uma rota separada e nao um campo.** Base64 dentro do JSON infla o
corpo em 33%, e o limite padrao do Nginx e 1 MB: o envio falharia com `413`
antes de chegar a aplicacao, o PubliBot leria isso como falha transitoria e
reenviaria megabytes indefinidamente.

**Responda `true` apenas enquanto faltar a foto.** O PubliBot registra a
entrega pelo digest do arquivo e nao reenvia o mesmo arquivo por conta propria
— mas obedece a quem pede. Pedir em toda publicacao reenvia em toda
publicacao.

**Trate a rota como assincrona.** Aceite, responda `202`, processe depois.
Redimensionar dentro da requisicao estoura o tempo limite de leitura e faz o
arquivo ser reenviado.

## Imagens: sempre WebP

**Toda imagem que trafega neste contrato e WebP**, seja por referencia
(`cover_image`) ou por upload (`/author-photos/`). Nao ha negociacao de
formato.

A conversao acontece no PubliBot, no momento em que o arquivo e recebido do
usuario — nao no envio. Converter a cada entrega gastaria CPU em toda
publicacao, deixaria dois formatos no disco e abriria a chance de um caminho
esquecer a conversao, que e como um PNG de 4 MB acaba no site de um cliente.

WebP porque atende os tres lados de uma vez: compressao melhor que JPEG na
mesma qualidade percebida, transparencia como o PNG, e suporte universal em
navegador desde 2020.

Fotos de perfil chegam com no maximo 1600 px no maior lado.

## Envelope de erro

Toda resposta de erro segue o mesmo formato:

```json
{
  "error": {
    "code": "invalid_payload",
    "message": "descricao legivel",
    "details": {},
    "request_id": "uuid"
  }
}
```

### Catalogo

| Status | `code` | O PubliBot repete? |
|---|---|---|
| 400 | `invalid_payload` | Nao |
| 401 | `invalid_api_key`, `signature_expired`, `signature_invalid` | Nao — alerta |
| 403 | `forbidden` | Nao |
| 409 | `duplicate_idempotency_key` | Tratado como sucesso |
| 413 | `payload_too_large` | Nao |
| 422 | `content_rejected` | Nao |
| 429 | `rate_limited` | Sim, respeitando `Retry-After` |
| 5xx | `temporarily_unavailable` | Sim, com espera crescente |

**Normativo:** 5xx, 408 e 429 sao retentaveis. Os demais 4xx sao terminais.

## Sanitizacao do HTML recebido

**Sanitize o `html_content` antes de gravar.** O PubliBot ja sanitiza antes de
enviar, mas a defesa precisa existir dos dois lados: quem grava e o responsavel
final pelo que sai na propria pagina.

Tags aceitas: `p br hr h2 h3 h4 ul ol li strong em b i u s blockquote code pre
a img table thead tbody tr th td figure figcaption span div`

- Atributos `on*` (`onclick`, `onerror`, ...): **sempre removidos**.
- `href` e `src`: apenas `http`, `https` e `mailto`.
- `script`, `iframe`, `object`, `embed`, `style`: recuse com `422`.

Aplique tambem em `title`, `author.name` e no texto das perguntas.

| Linguagem | Ferramenta |
|---|---|
| Python | `nh3` |
| PHP / WordPress | `wp_kses_post()` |
| Node | `DOMPurify` (com jsdom) |
| Go | `bluemonday` |

## Limite de requisicoes

Recomendado: `429` com `Retry-After` acima de 60 requisicoes por minuto por IP
nas rotas `/api/`, e 10 por minuto por chave em `publish`. Bloqueio de 15
minutos apos 10 respostas `401` seguidas.

Sem limite, `GET /api/v1/seo-context/` vira amplificador: devolve a home
inteira e a lista de publicacoes, gerada do zero a cada chamada.

## Rotas

Payloads completos em [`openapi.yaml`](openapi.yaml). Exemplos em
[`exemplos.md`](exemplos.md).

## Versionamento

O prefixo `/api/v1/` e obrigatorio. O campo `capabilities` em `/health/`
declara os recursos opcionais suportados — o PubliBot consulta no cadastro e
**degrada com elegancia** em vez de assumir.

Uma versao permanece suportada por 12 meses apos a seguinte ser publicada.
