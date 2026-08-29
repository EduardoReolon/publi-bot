# Exemplos do contrato `/api/v1`

Requisicoes e respostas reais, na ordem em que acontecem. As regras normativas
estao em [README.md](README.md); os esquemas completos em
[openapi.yaml](openapi.yaml).

Todos os exemplos omitem os cabecalhos de assinatura por brevidade. **Eles nao
sao opcionais** — ver [Autenticacao](README.md#autenticacao).

---

## 1. Cadastro do site: `GET /health/`

O PubliBot chama isto uma vez, ao cadastrar o site, e guarda o que voltar.
`capabilities` e o que permite adicionar recursos ao contrato sem quebrar os
sites ja instalados.

```http
GET /api/v1/health/ HTTP/1.1
Host: exemplo.com.br
X-API-KEY: ...
```

```json
{
  "contract_versions": ["v1"],
  "implementation": "publibot-django 1.0.0",
  "capabilities": ["idempotency", "hmac_signature", "author_photo", "qa", "reconciliation"],
  "server_time": "2026-08-29T14:02:11Z"
}
```

Um site que nao declara `qa` nunca recebe resposta de pergunta. Um que nao
declara `author_photo` recebe os dados textuais do autor e nada mais.

---

## 2. Publicacao de artigo: `POST /publish/`

```http
POST /api/v1/publish/ HTTP/1.1
Content-Type: application/json
Idempotency-Key: 5f3c1a90-4e2b-4b3a-9f1e-7d2c8a6b4e10
```

```json
{
  "type": "article",
  "idempotency_key": "5f3c1a90-4e2b-4b3a-9f1e-7d2c8a6b4e10",
  "title": "Monitoramento da pressao na gestacao",
  "slug": "monitoramento-da-pressao-na-gestacao",
  "html_content": "<p>Quem acompanha uma gestacao encontra orientacoes conflitantes.</p><h2>Por que medir em casa</h2><p>A medida domiciliar reduz o efeito do jaleco branco, segundo <a href=\"https://revista.exemplo.org/estudo\">Silva et al., 2024</a>.</p>",
  "excerpt": "Uma leitura do que a literatura sustenta sobre medir a pressao em casa.",
  "meta_description": "O que os estudos mostram sobre medir a pressao em casa na gestacao.",
  "focus_keyword": "pressao na gestacao",
  "language": "pt-br",
  "author": {
    "name": "Joana Ribeiro",
    "credentials": "Nutricionista, CRN-3 45678",
    "bio": "Atende gestantes ha doze anos.",
    "email": "joana@exemplo.com.br",
    "phone": "+55 11 98888-7777",
    "social_links": [{"label": "Instagram", "url": "https://instagram.com/joana"}],
    "reference": "b1f0c3d2-8a44-4c1e-9f77-2e5a9c0d1b33",
    "has_photo": true
  },
  "reviewed_by": "Joana Ribeiro",
  "reviewed_at": "2026-08-29T13:40:02Z",
  "content_disclosure": "Conteudo produzido com apoio de inteligencia artificial a partir de literatura tecnica e revisado por Joana Ribeiro (Nutricionista, CRN-3 45678). Nao substitui orientacao profissional.",
  "canonical_source": "https://revista.exemplo.org/estudo",
  "status": "published",
  "publish_at": null
}
```

Resposta, quando o site ainda nao tem a foto daquele autor:

```json
{
  "status": "success",
  "remote_id": "9c1d",
  "idempotency_key": "5f3c1a90-4e2b-4b3a-9f1e-7d2c8a6b4e10",
  "url": "https://exemplo.com.br/blog/monitoramento-da-pressao-na-gestacao",
  "slug": "monitoramento-da-pressao-na-gestacao",
  "post_status": "published",
  "published_at": "2026-08-29T14:02:40Z",
  "author_photo_required": true
}
```

**Reenvio com a mesma chave** devolve `200` e a publicacao existente, sem criar
outra:

```json
{
  "status": "already_exists",
  "remote_id": "9c1d",
  "url": "https://exemplo.com.br/blog/monitoramento-da-pressao-na-gestacao",
  "author_photo_required": false
}
```

---

## 3. Foto do autor: `POST /author-photos/`

So acontece porque a resposta anterior trouxe `author_photo_required: true`.

```http
POST /api/v1/author-photos/ HTTP/1.1
Content-Type: multipart/form-data; boundary=----X
```

```
------X
Content-Disposition: form-data; name="author_reference"

b1f0c3d2-8a44-4c1e-9f77-2e5a9c0d1b33
------X
Content-Disposition: form-data; name="sha256"

3ab2f1...c904
------X
Content-Disposition: form-data; name="photo"; filename="b1f0c3d2-....webp"
Content-Type: image/webp

<bytes do WebP>
------X--
```

```json
{ "status": "accepted", "job_id": "d41f" }
```

Se o arquivo ja tinha sido recebido (mesmo `sha256`), responda `200` com
`{"status": "already_exists"}` e nao grave de novo.

Na proxima publicacao daquele autor, `author_photo_required` deve vir `false`.

---

## 4. Resposta de pergunta: `POST /publish/` com `type: "qa"`

Mesma rota, mesma idempotencia, mesma assinatura. O que muda e o corpo.

```json
{
  "type": "qa",
  "idempotency_key": "77c2b418-1d0e-45b8-9a1e-3f6c2d9e5a80",
  "question_id": "pergunta-7",
  "html_content": "<p>A medida em casa e possivel e util no acompanhamento.</p>",
  "language": "pt-br",
  "author": {
    "name": "Joana Ribeiro",
    "credentials": "Nutricionista, CRN-3 45678",
    "reference": "b1f0c3d2-8a44-4c1e-9f77-2e5a9c0d1b33",
    "has_photo": true
  },
  "reviewed_by": "Joana Ribeiro",
  "reviewed_at": "2026-08-29T15:10:00Z",
  "content_disclosure": "Conteudo produzido com apoio de inteligencia artificial ...",
  "status": "published"
}
```

`question_id` e o `id` que **o proprio site** devolveu em
`/pending-questions/`. Sem ele o site nao tem como ligar a resposta a duvida.

Uma resposta pode ter sido **escrita a mao** no PubliBot, quando o acervo nao
sustentava a pergunta. Isso nao muda nada aqui: o corpo e identico e passou
pela mesma revisao humana. O site nao precisa distinguir os dois casos.

---

## 5. Coleta de perguntas: `GET /pending-questions/`

```http
GET /api/v1/pending-questions/?limit=50 HTTP/1.1
```

```json
{
  "pending_questions": [
    {
      "id": "pergunta-7",
      "question_text": "Da para medir a pressao em casa?",
      "submitted_at": "2026-08-28T09:12:00Z",
      "author_name": "",
      "consent_at": null
    }
  ],
  "next_cursor": null
}
```

Depois de importar, o PubliBot confirma:

```json
POST /api/v1/pending-questions/ack/
{ "ids": ["pergunta-7"] }
```

```json
{ "acknowledged": 1 }
```

**Sem a confirmacao, cada ciclo reimportaria as mesmas perguntas.** A
publicacao da resposta so acontece apos revisao humana, potencialmente dias
depois — nao da para usar "respondida" como criterio.

---

## 6. Reconciliacao apos timeout: `GET /publications/`

Chamado antes de repetir um envio que deu timeout. O conteudo pode ter sido
gravado e apenas a resposta ter se perdido.

```http
GET /api/v1/publications/?idempotency_key=5f3c1a90-4e2b-4b3a-9f1e-7d2c8a6b4e10
```

```json
{
  "results": [
    {
      "status": "already_exists",
      "remote_id": "9c1d",
      "url": "https://exemplo.com.br/blog/monitoramento-da-pressao-na-gestacao",
      "slug": "monitoramento-da-pressao-na-gestacao",
      "post_status": "published",
      "published_at": "2026-08-29T14:02:40Z"
    }
  ]
}
```

Lista vazia significa "nunca chegou aqui", e o PubliBot reenvia.

---

## 7. Erros

Todos com o mesmo envelope. O `code` e o que decide se o PubliBot repete —
ver o [catalogo](README.md#catalogo).

```json
{
  "error": {
    "code": "content_rejected",
    "message": "html_content contem <script>",
    "details": {},
    "request_id": "3f2a1b0c-..."
  }
}
```

Credencial invalida devolve **sempre o mesmo corpo e status**, seja a chave
ausente, malformada ou incorreta:

```json
{
  "error": {
    "code": "invalid_api_key",
    "message": "Credencial invalida.",
    "details": {},
    "request_id": "..."
  }
}
```

Limite excedido devolve `429` com `Retry-After` em segundos. O PubliBot
respeita o cabecalho — ele sabe melhor que o SaaS quando o site volta a
aceitar.
