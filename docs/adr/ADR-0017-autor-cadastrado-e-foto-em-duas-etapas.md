# ADR-0017 — Autor cadastrado no PubliBot, foto em duas etapas, imagem sempre WebP

**Status:** Aceito
**Data:** 2026-08-29

## Contexto

Ate aqui a assinatura de um artigo eram duas strings digitadas na tela de
revisao: `author_name` e `author_credentials`. Funciona para publicar, e falha
em tudo o mais.

Digitar o autor a cada artigo produz grafias diferentes da mesma pessoa, e nao
ha onde pendurar foto, biografia, contato ou redes sociais — coisas que o site
de destino precisa para montar uma caixa de autor decente. Num nicho sensivel a
caixa de autor nao e enfeite: e o que permite ao leitor avaliar quem escreveu.

A pergunta seguinte e onde esse cadastro mora. Duplica-lo no site de destino
criaria a pergunta impossivel de "qual dos dois lados tem a versao mais
recente", e obrigaria o site a implementar um CRUD de autor antes de receber a
primeira publicacao.

## Decisao

### 1. O cadastro vive somente no PubliBot

`content.Author`, dentro do schema do tenant. Nome obrigatorio; foto,
credenciais, bio, e-mail, telefone e redes opcionais.

O no final continua **passivo**: ele nao conhece o PubliBot antes de receber a
primeira publicacao e nao mantem cadastro proprio. Os dados do autor viajam
dentro de cada publicacao, em `author`. A responsabilidade fica onde deve estar
— com quem validou o conteudo — e nao ha estado para sincronizar.

O artigo ganha `author` (FK) **e mantem** `author_name` / `author_credentials`.
Isso nao e redundancia: os dois campos de texto sao o **retrato do que foi
publicado**. Renomear alguem no cadastro nao pode reescrever a assinatura de um
artigo que ja esta no ar.

A escolha do autor e opcional para **salvar** e obrigatoria para **aprovar**. A
trava fica em `aprovar_e_agendar`; exigi-la no formulario impediria o revisor de
guardar uma correcao de texto num ambiente que ainda nao cadastrou ninguem.

### 2. A foto viaja em duas etapas, e so quando pedida

```
1. POST /publish/          author: { reference, has_photo: true, ... }
2. 201                     author_photo_required: true
3. POST /author-photos/    multipart: author_reference + sha256 + photo
4. 202                     { "status": "accepted" }
```

Duas razoes, ambas concretas:

**So o no sabe se ja tem o arquivo.** Enviar sempre gastaria um upload por
artigo publicado; nunca enviar deixaria a caixa de autor sem foto para sempre.

**O corpo da publicacao e JSON.** Uma imagem embutida ali viraria base64, 33%
maior, num corpo que o Nginx corta em 1 MB por padrao: o envio falharia com
`413` antes de chegar a aplicacao, e o SaaS leria isso como falha transitoria e
reenviaria indefinidamente. E o mesmo motivo pelo qual `cover_image` viaja por
referencia.

A entrega e assincrona dos dois lados. Do lado do no porque a rota aceita e
processa depois. Do lado daqui porque o artigo **ja foi publicado com sucesso**
quando a foto sai: uma falha no upload nao pode desfazer a publicacao nem
prender o worker que a fez. Dai a task propria, `deliver_author_photo`.

`integrations.AuthorPhotoDelivery` registra o que ja foi entregue, com chave
`(site, autor, sha256 do arquivo)`. A chave e o **digest**, e nao o autor:
trocar a foto precisa reenviar, manter a mesma foto nao. Guardar so por autor
esconderia a troca.

### 3. Toda imagem e WebP, convertida na entrada

A conversao acontece ao **receber** o arquivo do usuario, nunca ao envia-lo.
Converter a cada entrega gastaria CPU em toda publicacao, deixaria dois formatos
no disco e abriria a chance de um caminho esquecer a conversao — que e como um
PNG de 4 MB acaba no site de um cliente.

WebP porque atende os tres lados de uma vez: compressao melhor que JPEG na mesma
qualidade percebida, transparencia como o PNG, e suporte universal em navegador
desde 2020. Escolher por tipo de imagem exigiria decidir caso a caso, e a decisao
erraria.

Fotos sao reduzidas para no maximo 1600 px no maior lado — acima disso a imagem
e maior do que qualquer lugar onde ela aparece.

## Consequencias

* O site de destino nao implementa cadastro de autor. Implementa, no maximo, uma
  rota de arquivos, e so se quiser exibir a foto.
* `author_photo` e recurso **opcional**, anunciado em `/health/`. Um site que nao
  o declara recebe os dados textuais do autor e nada mais.
* Um no que responda `author_photo_required: true` em toda publicacao recebe o
  arquivo em toda publicacao. O contrato diz isso explicitamente; o PubliBot
  obedece a quem pede.
* Artigos anteriores ao cadastro continuam publicaveis pelo retrato em
  `author_name`, e o padrao do site (`default_author`) segue valendo como ultimo
  recurso.

## Alternativas recusadas

**Cadastro espelhado no no final.** Exigiria sincronizacao, versionamento e uma
resposta para "quem ganha quando os dois mudam". Nada disso agrega ao produto.

**Foto por URL assinada, como a imagem de capa.** Obrigaria o PubliBot a expor
armazenamento publico com URL temporaria so para um arquivo pequeno que muda
raramente. O upload direto e mais simples e nao abre superficie nova.

**Base64 no corpo da publicacao.** Descrito acima: falha com `413` no limite
padrao do Nginx, e a falha se parece com uma transitoria.
