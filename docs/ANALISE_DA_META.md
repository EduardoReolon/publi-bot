# Análise do app na Meta (App Review) — o que preencher

Textos prontos para colar em *Analisar > Análise do app*. Os campos de texto
vão **em inglês** (os analistas leem em inglês; resposta em português costuma
voltar com pedido de esclarecimento).

## Antes de enviar (a ordem que evita recusa)

1. **Teste em modo de desenvolvimento.** Com o app "Não publicado", quem tem
   função no app (administrador, desenvolvedor, testador) já usa todas as
   permissões, sem análise. Conecte o seu Instagram em *Redes › Configurar*,
   publique um post de teste, deixe um comentário nele e veja o
   *Diagnóstico*. Isso prova que tudo funciona e vira o vídeo.
2. **Grave um vídeo por permissão** (ou um só cobrindo todas, com cortes): a
   tela do PubliBot, o login da Meta pedindo a permissão, e a função usando
   o dado. Em inglês ou com legenda em inglês; mostre o idioma da interface.
3. **Crie um acesso para os analistas**: um cliente de teste no PubliBot
   (`provision_tenant`) com um usuário e senha só para eles, e uma conta de
   Instagram de teste ligada a uma página. Os dados vão em *Instruções da
   análise*.
4. **Verificação da empresa** (Configurações do app > Básico) precisa estar
   concluída para o acesso avançado.

Enquanto a análise não sai, um cliente pode ser adicionado como **Testador**
(Funções do app) e conectar normalmente.

## 1. Uso permitido — o que dizer de cada permissão

Para cada uma, a Meta pede "como o app usa" e a confirmação de que o uso
beneficia o usuário. Cole o texto correspondente.

**instagram_basic**
> PubliBot is a content tool for small businesses. After the business owner
> connects their Instagram professional account, we read the account's
> profile (username, follower count) and its media list so the owner can see
> their posts and results inside PubliBot and so we can measure the posts we
> publish on their behalf.

**instagram_content_publish**
> The business owner writes or approves each post inside PubliBot (caption and
> images, usually a carousel generated from an article they published on their
> own website). At the time the owner scheduled, PubliBot publishes that
> approved post to the owner's own Instagram professional account. Nothing is
> published without the owner's approval.

**instagram_manage_comments**
> We read comments on the posts PubliBot published so the business owner can
> see questions from their audience. When the owner approves an answer inside
> PubliBot, we post it as a reply to that comment. We store only the comment
> text, date and the commenter's first name, for 90 days.

**instagram_manage_insights**
> We read reach, likes, comments, saves and shares of the owner's posts and the
> account's follower count to show the owner which posts performed better and
> to recommend what to post next. Insights are shown only to the owner of the
> account.

**pages_show_list**
> Instagram professional accounts are linked to a Facebook Page. We list the
> Pages the owner manages only to find the Instagram account linked to the
> Page they choose to connect.

**pages_read_engagement**
> Required together with pages_show_list to read the Instagram business
> account linked to the owner's Page (instagram_business_account field). We do
> not read or store Page posts.

**business_management**
> Many small businesses keep their Page and Instagram account inside a Meta
> Business portfolio. This permission lets the owner connect an Instagram
> account whose Page belongs to their business portfolio. We do not create or
> change anything in the portfolio.

**ads_read** (só se pediu; é a configuração `SOCIAL_META_CONFIG_ID_ANUNCIOS`)
> Optional, enabled by the owner with a separate "Connect ads" button. We read
> spend, reach, impressions and link clicks per ad, and which Instagram post
> each ad promoted, from the owner's own ad account, to show the owner how much
> they spent per post and which boosts were worth it. Read-only; we never
> create, edit or pay for ads.

## 2. Tratamento de dados

Respostas para as perguntas do formulário (ajuste o que for diferente no seu
caso, como o provedor de hospedagem):

- **Responsável pelos dados:** o mesmo `OPERADOR_NOME` / `OPERADOR_DOCUMENTO`
  do `.env` (aparece em `/privacidade/`).
- **Há operadores (processadores) com acesso aos dados da Meta?** Sim:
  - o provedor de hospedagem do servidor e do banco (diga qual);
  - se você usa um modelo de linguagem pago (não a sua placa local): o
    provedor dele, que recebe o texto dos comentários para redigir a
    resposta que o dono aprova. Com o modelo só na sua placa, responda que
    não há.
- **País onde os dados ficam:** o do servidor (ex.: Brasil, ou o país da
  região da hospedagem).
- **Pedidos de autoridades públicas nos últimos 12 meses:** não.
- **Políticas para pedidos de autoridades:** "We only disclose data when
  legally required, after reviewing the legality of the request, and disclose
  the minimum necessary. Requests are documented."
- **Exclusão de dados:** callback já configurado
  (`https://<raiz>/exclusao-de-dados/meta/`).

## 3. Instruções da análise

Cole e preencha os `<...>`:

> **How to test**
>
> 1. Go to `https://<test-tenant>.<raiz>/` and log in with
>    user `<email>` / password `<senha>`.
> 2. Open **Redes › Configurar**. On the "Instagram" account, click
>    **Conectar a API** and log in with the test Instagram account
>    `<@conta>` (Facebook user `<email>` / `<senha>`). Choose the Page
>    `<pagina>`. This uses pages_show_list, pages_read_engagement,
>    business_management and instagram_basic.
> 3. Open **Redes › Diagnóstico**: the account's past posts, reach and
>    comments are imported (instagram_basic, instagram_manage_insights,
>    instagram_manage_comments).
> 4. Open **Redes › Para revisar**, edit any draft post and click
>    **Aprovar**, then **Publicar agora** in **Agenda**. The post appears on
>    the Instagram account (instagram_content_publish).
> 5. Comment a question on that post from another account. In **Redes ›
>    Comentários** the question appears; after the answer is approved in
>    **Perguntas**, PubliBot replies to the comment (instagram_manage_comments).
> 6. (ads_read) In **Redes › Diagnóstico › Gasto com anúncios**, click
>    **Conectar anúncios**; spend per ad is listed.
>
> The interface is in Portuguese; the video shows each step with English
> captions.

Depois de enviar, a Meta responde em alguns dias. Se ela pedir mais
informação, a resposta chega na *Caixa de Entrada de alertas* do app.
