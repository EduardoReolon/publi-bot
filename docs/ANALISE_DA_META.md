# Análise do app na Meta (App Review)

**Não precisa agora.** Com o app "Não publicado", quem tem função no app
(você, como administrador) usa todas as permissões nas próprias contas, sem
análise. A análise só libera contas de **clientes**. Use o sistema, veja
funcionando, e só então siga este roteiro. Até lá, um cliente pode ser
adicionado como **Testador** (Funções do app) e conectar normalmente.

## Permissões: o que manter e o que tirar

| Permissão | Manter? | Por quê |
|---|---|---|
| instagram_basic | **sim** (confira que está na lista) | ler perfil e posts da conta |
| instagram_content_publish | sim | publicar |
| instagram_manage_comments | sim | ler e responder comentários |
| instagram_manage_insights | sim | alcance, salvos, seguidores |
| pages_show_list | sim | achar o Instagram ligado à página |
| pages_read_engagement | sim | idem (ler o campo do Instagram na página) |
| business_management | sim | página dentro de um portfólio empresarial |
| public_profile | sim | padrão de todo login; nada a fazer |
| ads_read | sim | gasto com anúncios (só leitura) |
| Marketing API Access Tier | sim | sem ele, a API de anúncios só lê contas de anúncio suas; para as dos clientes precisa do acesso padrão |
| **ads_management** | fica no app, mas **não pedir na análise** enquanto o PubliBot não impulsionar sozinho | serve para criar e editar anúncios; hoje o PubliBot só lê. O caso de uso da Marketing API não deixa removê-la, e tudo bem: na análise cada permissão é pedida separadamente, e esta fica sem pedir. Se um dia o PubliBot passar a impulsionar, ela entra no mesmo pedido, com um passo a mais no vídeo |

## Um vídeo só, para todas as permissões

Grave a tela uma vez, seguindo os passos abaixo em ordem. Na hora de enviar,
use **o mesmo vídeo** em todas as permissões. Narração não é obrigatória:
basta uma legenda (pode ser escrita na tela, ou no editor de vídeo) com o
texto entre aspas de cada passo.

Antes de gravar: tenha um post de artigo **em "Para revisar"** na conta do
Instagram e saia da sua conta do Facebook no navegador (para o login
aparecer no vídeo).

1. Abra `https://<seu cliente>.<raiz>/redes/` já logado no PubliBot.
   Legenda: *"PubliBot, a content tool for small businesses."*
2. Vá em **Configurar**, na conta Instagram clique **Conectar a API**.
   Faça login no Facebook, escolha a página e a conta do Instagram, e
   aceite as permissões (mostre a tela de permissões por 2 ou 3 segundos).
   Legenda: *"The owner connects their own Instagram professional account
   (pages_show_list, pages_read_engagement, business_management,
   public_profile)."*
3. Você volta ao PubliBot, na aba **Diagnóstico**: aparecem os posts antigos,
   o alcance e os comentários. Role a página devagar.
   Legenda: *"Past posts, reach and comments are imported to show the owner
   what worked (instagram_basic, instagram_manage_insights,
   instagram_manage_comments)."*
4. Vá em **Para revisar**, abra o post, clique **Aprovar**; na **Agenda**,
   clique **Publicar agora**. Depois abra o Instagram (outra aba ou o
   celular) e mostre o post publicado.
   Legenda: *"Nothing is published without the owner's approval. The
   approved post is published to the owner's account
   (instagram_content_publish)."*
5. Comente uma pergunta nesse post com outra conta (pode ser pelo celular).
   No PubliBot, aba **Comentários**, mostre a pergunta; em **Perguntas**,
   aprove a resposta; mostre a resposta no Instagram.
   Legenda: *"Questions in comments are answered after the owner approves
   the reply (instagram_manage_comments)."*
6. Aba **Diagnóstico** > **Conectar anúncios** > login aceitando a leitura
   de anúncios. Mostre a lista de gasto por anúncio.
   Legenda: *"Read-only: spend per ad and which post each ad promoted
   (ads_read, Marketing API standard access)."*

Se o passo 5 demorar (a resposta passa pela fila de Perguntas), pode cortar
a espera no editor.

## Textos de cada permissão (colar em "Uso permitido")

**instagram_basic**
> After the business owner connects their Instagram professional account, we
> read its profile (username, follower count) and media list so the owner sees
> their posts and results inside PubliBot, and to measure the posts we publish
> on their behalf.

**instagram_content_publish**
> The owner writes or approves each post inside PubliBot (caption and images,
> usually a carousel made from an article published on their own website). At
> the time the owner scheduled, PubliBot publishes that approved post to the
> owner's own account. Nothing is published without approval.

**instagram_manage_comments**
> We read comments on the owner's posts so they see their audience's
> questions. When the owner approves an answer inside PubliBot, we post it as
> a reply. We keep only the comment text, date and the commenter's first name.

**instagram_manage_insights**
> We read reach, likes, comments, saves, shares and follower count to show the
> owner which posts performed better and recommend what to post next. Shown
> only to the account owner.

**pages_show_list**
> Instagram professional accounts are linked to a Facebook Page. We list the
> owner's Pages only to find the Instagram account linked to the Page they
> choose.

**pages_read_engagement**
> Used with pages_show_list to read the Instagram business account linked to
> the owner's Page. We do not read or store Page posts.

**business_management**
> Many small businesses keep their Page and Instagram inside a Meta Business
> portfolio. This lets the owner connect an account whose Page belongs to that
> portfolio. We do not create or change anything in the portfolio.

**public_profile**
> Standard login data (name and app-scoped id) used to identify who connected
> the account and to honor data deletion requests.

**ads_read**
> Optional, enabled by the owner with a separate "Connect ads" button. We read
> spend, reach, impressions and link clicks per ad, and which Instagram post
> each ad promoted, from the owner's ad account, to show how much was spent
> per post. Read-only: we never create, edit or pay for ads.

**Marketing API Access Tier (acesso padrão)**
> Our clients are small businesses whose ad accounts are not owned by the app
> developer. Standard access lets PubliBot read (read-only, ads_read) the
> spend report of each client's own ad account once a day, after the client
> connects it.

## Tratamento de dados (já contando com modelo externo)

Declarar agora os provedores externos não cria burocracia extra: é uma lista
de quem processa os dados, e evita refazer a declaração quando trocar a placa
local por uma API paga. A política de privacidade (`/privacidade/`) já diz o
mesmo.

- **Responsável:** o `OPERADOR_NOME` (e `OPERADOR_DOCUMENTO`) do `.env`.
- **Operadores (processadores):** sim.
  - Hospedagem do servidor e do banco: `<seu provedor>` — `<país>`.
  - Provedores de modelo de linguagem (ex.: Anthropic, OpenAI, Google) —
    Estados Unidos. Recebem o texto dos comentários e das legendas para
    redigir respostas e posts que o dono aprova; não recebem tokens de
    acesso.
- **Pedidos de autoridades nos últimos 12 meses:** não.
- **Política para pedidos de autoridades:**
  > We only disclose data when legally required, after reviewing the legality
  > of each request, and disclose the minimum necessary. Requests are
  > documented.
- **Exclusão de dados:** `https://<raiz>/exclusao-de-dados/meta/` (já
  configurado).

## Instruções da análise (colar e preencher os `<...>`)

> The interface is in Portuguese; the video shows every step with English
> captions.
>
> 1. Go to `https://<cliente-de-teste>.<raiz>/redes/` and log in with
>    `<email>` / `<senha>`.
> 2. **Configurar** › Instagram › **Conectar a API**: log in with the test
>    Facebook user `<email>` / `<senha>`, choose the Page `<pagina>`.
> 3. **Diagnóstico**: imported posts, reach and comments.
> 4. **Para revisar** › **Aprovar**, then **Agenda** › **Publicar agora**:
>    the post appears on `<@conta>`.
> 5. Comment a question on that post; it appears in **Comentários**; the
>    approved answer is posted as a reply.
> 6. **Diagnóstico** › **Conectar anúncios**: spend per ad.

Para isso, crie antes um cliente de teste no PubliBot com um usuário só para
os analistas (`provision_tenant`) e deixe uma conta de Instagram de teste
ligada a uma página.
