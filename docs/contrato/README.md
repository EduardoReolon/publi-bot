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
| `PUT` | `/api/v1/publications/{remote_id}/` | Nao | `update` |
| `GET` | `/api/v1/insights/` | Nao | `insights` |

Dois recursos nao tem rota do PubliBot, e sim trabalho no seu template:
`call_to_action` (onde mostrar a chamada para a sua oferta) e, para alimentar
`insights`, a medicao de leitura no navegador — que fala com o SEU servidor,
nunca com o PubliBot.

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

### Vetor de teste

Confira sua implementacao contra estes valores antes de subir qualquer coisa.
Se o resultado nao bater, o problema esta na assinatura — e nao vale a pena
depurar mais nada antes disso.

| Entrada | Valor |
|---|---|
| segredo | `segredo-de-exemplo` |
| `X-Timestamp` | `1756483200` |
| `X-Nonce` | `6f1c9e2a-3d4b-4a5c-8e7f-0a1b2c3d4e5f` |
| corpo bruto | `{"type":"article","title":"Exemplo"}` |

Passo a passo:

```
sha256(corpo)  = 23af6d1a900ecc8147c078c2cf41592ea50fff00f53b1c110b787bc8aa674fa7
base           = 1756483200.6f1c9e2a-3d4b-4a5c-8e7f-0a1b2c3d4e5f.23af6d1a900ecc8147c078c2cf41592ea50fff00f53b1c110b787bc8aa674fa7
X-Signature    = v1=31a9127f0bb293e82c27717ee8c35b56a845d9bee31257b72cd9afdc4227e6ee
```

O corpo acima tem **36 bytes, sem espaco e sem quebra de linha**. Se voce
reserializar o JSON antes de conferir, o digest muda e a assinatura falha — e
esse e, de longe, o erro mais comum.

Em `multipart/form-data` (a rota de fotos) nada muda: o digest e do corpo
bruto, inteiro, com os delimitadores e tudo. Leia os bytes ANTES de deixar o
framework interpretar o multipart.

## Idempotencia

`POST /api/v1/publish/` recebe o cabecalho `Idempotency-Key`.

**Guarde essa chave com indice UNICO.** Ao receber uma chave ja processada,
responda `200` com os dados da publicacao existente — **nao crie outra**.

O motivo e um cenario comum, nao hipotetico: o site grava o artigo e responde
`201`, a resposta se perde na rede, e o PubliBot reenvia. Sem idempotencia, o
mesmo conteudo e publicado duas vezes. Conteudo duplicado e exatamente o
problema que este produto existe para evitar.

## Atualizacao de artigo publicado

Recurso `update`. Um artigo que ja esta no ar ganha uma versao nova no
PubliBot — mais completa, corrigida, respondendo o que o publico passou a
perguntar — e ela substitui o conteudo da MESMA pagina:

```http
PUT /api/v1/publications/{remote_id}/
Idempotency-Key: <uuid novo, um por atualizacao>
```

O corpo e o mesmo de `POST /publish/`. Tres regras:

1. **Substituicao inteira.** Nao e patch: o corpo traz o artigo completo, e o
   que nao vier (um FAQ removido, por exemplo) deixa de existir.
2. **O endereco nao muda.** Ignore o `slug` recebido. A pagina ja tem links e
   posicao no Google naquele endereco; trocar a URL numa atualizacao joga isso
   fora. Se o seu site precisar mudar o endereco, devolva a URL nova e
   redirecione a antiga com `301`.
3. **Idempotente pela chave.** Reenviar a chave da ULTIMA atualizacao aplicada
   devolve `200` com `status: "already_applied"` e o estado atual, sem aplicar
   de novo. E o que torna seguro repetir apos um timeout. Guarde ao menos a
   ultima chave por publicacao.

`remote_id` desconhecido devolve `404` com `code: "not_found"` — terminal: o
PubliBot nao repete, e avisa quem publicou.

Sem declarar `update` em `/health/`, o PubliBot nao tenta: a versao nova fica
aprovada, e a tela diz que o site nao aceita atualizacao.

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

## Imagem de capa

`cover_image` chega **por referencia**, com `url`, `sha256` e `mime_type`.
Baixe o arquivo, confira o digest, e sirva a imagem do seu proprio dominio —
nao faca `hotlink` da URL recebida.

A URL aponta para o PubliBot e e publica de proposito: quem a busca e o seu
servidor, sem credencial. Ela e um UUID nao enumeravel, e o PubliBot so serve
por ela a imagem **escolhida por uma pessoa** de um artigo **ja aprovado**. As
outras opcoes de capa e o restante da midia (os PDFs do acervo) nao sao
alcancaveis por URL.

**O campo pode nao vir.** Nenhuma capa e escolhida automaticamente — se
ninguem escolheu, o artigo chega sem `cover_image`, e nao com um objeto vazio.
Trate a ausencia como normal.

## Perguntas frequentes

Recurso `faq`. O artigo pode trazer `faq`: uma lista de
`{question, answer_html}`, na ordem em que as perguntas devem aparecer.

```json
"faq": [
  {"question": "Medir a pressao em casa substitui a consulta?",
   "answer_html": "<p>Nao. A medida em casa complementa o acompanhamento.</p>"}
]
```

**Chegam separadas do `html_content` de proposito.** Onde e como exibir e
decisao sua, porque depende do seu layout: um bloco no fim do artigo, uma
sanfona, uma barra lateral. O PubliBot nao escolhe por voce, e o titulo do
bloco ("Perguntas frequentes", "FAQ") tambem e seu.

Tres orientacoes, nenhuma delas obrigatoria:

- **Exiba no mesmo endereco do artigo.** As perguntas foram escritas para
  complementar aquele texto. Uma pagina propria para cada uma competiria com o
  artigo na busca.
- **Se usar sanfona, deixe o texto no HTML.** Resposta recolhida e indexada
  normalmente, desde que esteja na pagina; carregada por script so ao clicar,
  ela some para buscadores e leitores automaticos.
- **JSON-LD `FAQPage` e opcional.** O Google deixou de exibir o resultado
  enriquecido de FAQ em 2026, e diz que nao exige marcacao especial para os
  recursos de IA. Nao atrapalha, mas nao espere efeito na busca.

Regras:

- **O campo pode nao vir.** Sem nenhuma pergunta selecionada, o artigo chega
  sem `faq`, e nao com lista vazia.
- **`question` e texto puro**: sanitize como o `title`.
- **`answer_html` e HTML curto, sem links**: sanitize com a mesma lista do
  `html_content` (ver [Sanitizacao](#sanitizacao-do-html-recebido)).
- **Declare `faq` em `capabilities` so se exibir as perguntas.** O PubliBot
  envia o campo de qualquer forma — um site que nao o conhece simplesmente o
  ignora —, mas usa a declaracao para avisar a quem revisa quando as perguntas
  nao vao aparecer.

## Chamada para a oferta do site

Recurso `call_to_action`. Todo artigo traz o campo `call_to_action`, que diz
se cabe uma chamada para a oferta do site (a landing page, o WhatsApp, o
plano) e onde:

| Valor | O que o site mostra |
|---|---|
| `none` | Nenhum bloco de chamada. O tema esta longe da oferta; forcar um anuncio ali faz o leitor desconfiar do resto. |
| `end` | O bloco no fim do artigo. |
| `inline` | O bloco no fim **e** no meio, no lugar do elemento `<aside data-publibot="chamada"></aside>` que vem dentro do `html_content`. |

```json
"call_to_action": "inline",
"html_content": "<h2>Como conferir a nota</h2><p>...</p><aside data-publibot=\"chamada\"></aside><h2>...</h2>"
```

**O bloco e seu, nao do PubliBot.** Texto, botao, cor, link do WhatsApp e
rastreio ficam num componente do seu site. O artigo so diz se cabe e onde.
Trocar a oferta muda um lugar, e nao duzentos artigos. Quem decide o modo e o
PubliBot, pela proximidade entre o tema e a oferta descrita no Guia editorial;
a pessoa que revisa pode mudar.

Regras:

- **Aceite o `aside` na sanitizacao so com `data-publibot="chamada"`, sem
  conteudo.** E o unico `aside` do contrato. Com `nh3`:
  `tag_attribute_values={"aside": {"data-publibot": {"chamada"}}}` e `aside`
  na lista de tags.
- **No maximo um por artigo.** Se vier mais de um, mostre so o primeiro.
- **Fora de `inline`, o elemento nao vem.** Se vier assim mesmo, ignore.
- **Marque o bloco** para a medicao saber que o clique foi na chamada de um
  artigo: `data-publibot-bloco` no elemento do componente (ver
  [Leitura e conversoes](#leitura-e-conversoes)).
- **Nada de pop-up cobrindo o texto.** O Google penaliza intersticial
  intrusivo; o bloco e parte da pagina.

Declare `call_to_action` em `capabilities` quando o template mostrar o bloco.
Sem a declaracao, o PubliBot envia do mesmo jeito (um `aside` vazio nao aparece
na pagina), mas avisa na revisao que a chamada nao vai aparecer.

## Leitura e conversoes

Recurso `insights`. E o que permite ao PubliBot saber quais artigos sao lidos
de verdade e quais trazem cliente — e passar a sugerir mais temas parecidos
com os que convertem, como o Google Ads faz com palavras-chave.

**O PubliBot nunca recebe dado de pessoa.** O site mede no navegador, agrega
por dia e entrega numeros. A jornada ate a conversao fica guardada **no
navegador de quem le** (armazenamento local, 30 dias) e so e enviada ao SEU
servidor no momento em que a pessoa converte, sem identificador nenhum.

### O que medir, por artigo e por dia

| Campo | Definicao |
|---|---|
| `views` | Aberturas da pagina do artigo. |
| `engaged_views` | Aberturas com pelo menos **10 s de tempo ativo**. Quem so abriu e fechou fica de fora. |
| `engaged_seconds` | Soma do tempo ativo, em segundos. Ativo = aba visivel **e** alguma interacao (rolagem, toque, tecla, mouse) nos ultimos 30 s. Teto de 30 min por abertura. |
| `read_to_end` | Aberturas em que o fim do corpo do artigo entrou na tela. |
| `cta_views` | Aberturas em que o bloco da chamada entrou na tela. |
| `cta_clicks` | Aberturas com clique no bloco da chamada. |

Tempo ativo, e nao tempo com a aba aberta: uma aba esquecida aberta a noite
inteira nao e leitura. E o criterio do "tempo de engajamento" do Google
Analytics 4, com o corte de 10 s da "sessao engajada".

### O que e conversao

O que o seu negocio chamar de conversao: clique no WhatsApp, envio de
formulario, assinatura. Marque o elemento com `data-publibot-conversao="<tipo>"`
(`whatsapp`, `assinatura`...). A conversao leva:

- `kind` — o tipo;
- `via_cta` — se o clique foi dentro do bloco da chamada de um artigo;
- `journey` — os artigos lidos nos 30 dias anteriores, em ordem, com o tempo
  ativo de cada um: `[{"remote_id": "...", "engaged_seconds": 95}, ...]`.
  Todos, nao so o ultimo: e isso que permite ver que um artigo apresentou o
  problema e outro fechou a venda. Depois de converter, a jornada recomeca.

O PubliBot descarta da jornada o que foi lido por menos de 10 s e mostra tres
atribuicoes: **ultimo artigo**, **participou** e **atribuida** (cada conversao
dividida em partes iguais entre os artigos lidos).

### A rota

```http
GET /api/v1/insights/?since=2026-09-01&cursor=
```

```json
{
  "reading": [
    {"remote_id": "a1b2", "date": "2026-09-20", "views": 120, "engaged_views": 71,
     "engaged_seconds": 6390, "read_to_end": 33, "cta_views": 40, "cta_clicks": 5}
  ],
  "conversions": [
    {"id": "5f0c...", "date": "2026-09-20", "kind": "whatsapp", "via_cta": true,
     "journey": [{"remote_id": "c3d4", "engaged_seconds": 40},
                 {"remote_id": "a1b2", "engaged_seconds": 95}]}
  ],
  "next_cursor": null
}
```

- **Uma linha por publicacao e dia**, com o total do dia. O PubliBot pede os
  ultimos dias de novo a cada coleta e grava por cima: nao some, substitua.
- **`id` estavel por conversao.** Coletar de novo nao duplica.
- **`since`** e a data mais antiga pedida (no maximo 90 dias para tras).
  Pagine por `next_cursor` se a resposta ficar grande.
- `remote_id` e o `id` que o seu site devolveu na publicacao. Versoes novas do
  artigo (recurso `update`) mantem o mesmo, e a serie continua.

### Medir no navegador

A implementacao de referencia traz um script pronto, sem dependencia, em
`reference/django/publibot_node/static/publibot_node/leitura.js`. Ele le o id
do artigo de `data-publibot-id`, mede o tempo ativo, o fim do texto e a
chamada, e envia um resumo por abertura (`navigator.sendBeacon`) para uma rota
do SEU site, que agrega. Serve de modelo para qualquer plataforma.

Cuidados:

- **Privacidade.** Nada de identificador de visitante, IP ou endereco completo
  de quem le. O armazenamento local guarda so ids de artigo, segundos e data.
  Mesmo assim, conte na politica de privacidade que o site mede leitura; se o
  seu banner de consentimento bloqueia medicao, o script respeita
  `window.publibotMedir = false`.
- **Robos.** Descarte user-agents de robo e limite por IP a rota que recebe as
  medicoes: ela e publica, e qualquer um pode enviar numero falso. Os numeros
  sao indicativos, nao auditaveis.
- **Nao dependa de script de terceiro.** Bloqueador de anuncio derruba script
  de analytics conhecido; o seu, servido do seu dominio, passa.

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
| 404 | `not_found` | Nao |
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
a img table thead tbody tr th td figure figcaption span div`, e `aside` so
com `data-publibot="chamada"` (ver [Chamada](#chamada-para-a-oferta-do-site)).

- Atributos `on*` (`onclick`, `onerror`, ...): **sempre removidos**.
- `href` e `src`: apenas `http`, `https` e `mailto`.
- `script`, `iframe`, `object`, `embed`, `style`: recuse com `422`.

Aplique tambem em `title`, `author.name`, no texto das perguntas dos
visitantes e em cada item de `faq` (`question` como texto puro, `answer_html`
com a mesma lista acima).

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

## Lista de conferencia

Para quem esta implementando. Nesta ordem — cada item depende do anterior
funcionar.

**Antes de qualquer rota**

- [ ] A assinatura bate com o [vetor de teste](#vetor-de-teste).
- [ ] O digest e calculado sobre o corpo BRUTO, antes de interpretar o JSON.
- [ ] A comparacao usa funcao de tempo constante.
- [ ] Instante fora da janela de 300s e recusado.
- [ ] Nonce e guardado por 600s e repetido e recusado.
- [ ] Chave ausente, malformada e incorreta devolvem o MESMO corpo e status.
- [ ] HTTP simples e recusado com `403`.

**Obrigatorias**

- [ ] `GET /health/` devolve `contract_versions` e `capabilities` — e as
      `capabilities` dizem a verdade sobre o que voce implementou.
- [ ] `GET /seo-context/` pagina por cursor e trunca `home_content_text` em
      8000 caracteres.
- [ ] `POST /publish/` guarda `Idempotency-Key` com indice **UNICO no banco**,
      nao com uma checagem em Python.
- [ ] Chave repetida devolve `200` com a publicacao existente, sem criar outra.
- [ ] Duas requisicoes simultaneas com a mesma chave: a restricao do banco
      decide, e a perdedora devolve o registro vencedor.
- [ ] `html_content` passa por sanitizacao com lista de PERMISSAO antes de ser
      gravado.
- [ ] `title` e `author.name` tambem sao sanitizados.
- [ ] `content_disclosure` e renderizado visivelmente junto ao conteudo.
- [ ] Todo erro segue o envelope, com `code` do catalogo.

**Se voce declarar `author_photo`**

- [ ] `author_photo_required: true` so quando `has_photo` e verdadeiro **e**
      voce ainda nao tem aquele arquivo.
- [ ] `POST /author-photos/` aceita multipart e responde `202` sem processar a
      imagem dentro da requisicao.
- [ ] A foto e guardada por `author_reference`, nao pelo nome do autor.
- [ ] O `sha256` e conferido; divergente devolve `422`.
- [ ] O limite de corpo do seu framework aceita o arquivo (o padrao do Django
      sao 2,5 MB).

**Se voce declarar `qa`**

- [ ] `GET /pending-questions/` pagina por cursor.
- [ ] `POST /pending-questions/ack/` marca as perguntas como recebidas — sem
      isso, cada ciclo reimporta as mesmas.
- [ ] `author_name` so e enviado com consentimento registrado.
- [ ] A publicacao com `type: "qa"` usa o `question_id` que voce mesmo
      devolveu.

**Se voce declarar `reconciliation`**

- [ ] `GET /publications/?idempotency_key=` devolve a publicacao existente, ou
      lista vazia.

**Se voce declarar `update`**

- [ ] `PUT /publications/{remote_id}/` substitui o conteudo inteiro, com a
      mesma sanitizacao de `/publish/`.
- [ ] O endereco da pagina nao muda (ou muda com `301` do antigo).
- [ ] A chave da ultima atualizacao, reenviada, devolve `already_applied` sem
      reaplicar.
- [ ] `remote_id` desconhecido devolve `404` com `code: "not_found"`.

**Se voce declarar `faq`**

- [ ] `faq` pode nao vir. Ausencia e normal, nao erro.
- [ ] `question` e sanitizado como texto puro, e `answer_html` com a lista de
      permissao do `html_content`.
- [ ] As perguntas sao exibidas no mesmo endereco do artigo, com o texto das
      respostas presente no HTML (mesmo que recolhido).

**Se voce declarar `call_to_action`**

- [ ] O bloco aparece no fim para `end` e `inline`, e em nenhum lugar para
      `none`.
- [ ] O `aside data-publibot="chamada"` e trocado pelo bloco; a sanitizacao o
      aceita so com esse atributo e sem conteudo.
- [ ] O bloco tem `data-publibot-bloco`, e o botao, `data-publibot-conversao`.

**Se voce declarar `insights`**

- [ ] A pagina do artigo tem `data-publibot-id` com o `remote_id`.
- [ ] Leitura = pelo menos 10 s de tempo ATIVO (aba visivel e interacao nos
      ultimos 30 s), nao tempo com a aba aberta.
- [ ] `GET /insights/` devolve uma linha por publicacao e dia, com o total.
- [ ] Cada conversao tem `id` estavel e a jornada inteira dos 30 dias.
- [ ] Nada que identifique a pessoa sai do navegador; a rota que recebe as
      medicoes tem limite por IP e descarta robos.

**Imagem de capa**

- [ ] `cover_image` pode nao vir. Ausencia e normal, nao erro.
- [ ] A imagem e baixada da `url`, o `sha256` e conferido, e o arquivo passa a
      ser servido do SEU dominio (sem hotlink).

**Operacao**

- [ ] Limite de requisicoes com `429` e `Retry-After`.
- [ ] Bloqueio apos respostas `401` seguidas.
- [ ] `5xx`, `408` e `429` sao os unicos que fazem o PubliBot repetir.
