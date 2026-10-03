# Redes sociais: Google Meu Negócio, LinkedIn e Instagram

Plano aprovado, **ainda não implementado**. Fazer junto da implantação dos
adaptadores de dados públicos (`docs/DADOS_PUBLICOS.md`), quando o dono pedir.

## Objetivo

Levar os artigos (sobretudo os do fluxo B) para onde o público está, sem
prejudicar o site e medindo o que traz cliente, não só curtida. O PubliBot:

1. escolhe **qual artigo** vai para **qual rede**, e quando;
2. escreve **uma versão própria para cada rede** (nunca o artigo inteiro);
3. publica pela API, ou deixa pronto para copiar;
4. mede o resultado pelo canal de entrada nas conversões do site.

## Princípios

- **Nunca republicar o artigo inteiro.** Cópia integral em outra plataforma
  vira conteúdo duplicado e pode passar o site para trás no Google. Cada rede
  recebe um texto curto, no formato dela, que leva ao artigo.
- **Link com a origem marcada:** `?utm_source=<rede>&utm_medium=social&utm_campaign=<slug>`.
  O PubliBot já recebe as conversões do site por canal de entrada
  (`insights`), então sabe qual rede traz visita e contato.
- **Aprovação configurável por rede**, e o padrão é **todo post passa por
  aprovação**. Quem quiser liga o automático rede a rede.
- **Teto por rede** (padrão: 3 posts por semana) para não virar spam.
- **Algoritmo antes de LLM:** a escolha do artigo e da rede é por
  regras e proximidade (embedding); a LLM só escreve o texto do post.
- Genérico: nada no texto ou nas regras é específico do negócio do dono.

## As três redes

### Google Meu Negócio (Perfil da Empresa no Google)

- **Para quê:** busca local e mapa. O post aparece no perfil da empresa.
- **Formato:** até 1.500 caracteres, local ("em Curitiba…"), a capa do
  artigo como imagem, botão **"Saiba mais"** com o link.
- **API:** Business Profile API, `accounts.locations.localPosts`. O acesso
  precisa ser **pedido ao Google** (formulário de acesso à API; a cota começa
  em zero até aprovarem — pode levar semanas). **O dono deve pedir cedo.**
- **Login:** OAuth da conta Google dona do perfil; o PubliBot guarda o
  refresh token cifrado por site (como as outras chaves por site).
- **Até a API ser aprovada:** modo "copiar para postar".

### LinkedIn — perfil pessoal e página da empresa (os dois)

- **Para quê:** público profissional (consultoria, B2B, gestores, médicos).
- **Formato:** abertura forte na primeira linha (é o que aparece antes do
  "ver mais"), 3 a 5 parágrafos curtos, uma pergunta no fim, 3 hashtags no
  máximo. Imagem: a capa. É amplamente relatado (não confirmado pelo
  LinkedIn) que post com link no texto alcança menos: o link vai no
  **primeiro comentário**, publicado logo depois do post (a API permite).
- **API:**
  - **Perfil pessoal:** produto "Share on LinkedIn" (escopo
    `w_member_social`), liberação simples no app do LinkedIn Developers.
  - **Página da empresa:** "Community Management API" (escopo
    `w_organization_social`), que exige **revisão do LinkedIn** e o usuário
    precisa ser administrador da página. Mesma integração, outro "autor"
    (`urn:li:person:…` ou `urn:li:organization:…`).
- **Login:** OAuth. O token **vence em ~60 dias**; o PubliBot avisa no
  painel uma semana antes e pede para reconectar (sem refresh token para
  apps comuns).
- Uma "rede" por autor: o mesmo site pode ter o perfil pessoal e a página
  como dois destinos, cada um com a sua aprovação e o seu teto.

### Instagram

- **Para quê:** público amplo e visual (clínicas, obras: antes/depois).
- **Formato:** **carrossel** de 5 a 7 imagens com as ideias principais do
  artigo, legenda curta com chamada para o "link na bio" (Instagram não tem
  link clicável na legenda).
- **Imagens — geração própria, por modelo de layout, não por IA de imagem:**
  modelo de imagem escreve texto mal (letras trocadas). O carrossel é
  montado por algoritmo (Pillow): cada lâmina é um modelo com as cores e a
  fonte do site e um texto curto. A primeira lâmina usa a **capa** já gerada
  do artigo (com o título por cima); as seguintes, uma ideia por lâmina
  (frase extraída/encurtada das seções pela LLM, até ~20 palavras); a última,
  a chamada ("leia o artigo completo — link na bio"). 1080×1350 (4:5).
- **API:** Instagram Graph API (Content Publishing), conta **profissional**
  ligada a uma página do Facebook, via app da Meta. As imagens precisam
  estar em **URL pública**: o PubliBot serve as lâminas da própria mídia
  (endereço com chave aleatória) durante a publicação.
- **Login:** OAuth da Meta, token de longa duração (~60 dias), renovável;
  mesmo aviso de reconexão.

## Escolher o artigo e a rede

Por regras, sem LLM, com dados que o PubliBot já tem:

1. **Artigo novo publicado** → entra na fila das redes que combinam com ele.
2. **Qual rede combina** (pontuação por rede, a maior ganha; empate, todas):
   - **Google Meu Negócio:** as buscas do artigo (Search Console) ou o tema
     têm cidade, bairro ou "perto de mim" das regiões do Radar; ou o artigo é
     sobre um serviço que o negócio presta (proximidade com a oferta, a mesma
     de `chamada.py`).
   - **LinkedIn:** proximidade entre o artigo e a descrição do público
     profissional da rede (configurável por destino: "gestores de clínica",
     "donos de pequenas empresas"…).
   - **Instagram:** artigo com seções que viram ideias curtas (listas,
     passo a passo, "x sinais de…") — detectado pela estrutura do plano.
3. **Repostar o que já está no ar** (com outro ângulo, nunca o mesmo texto):
   - **subindo no Search Console** (impressões crescendo entre dois retratos,
     posição 8 a 20) → um empurrão;
   - **converte no site** (insights) → reciclar a cada N meses (padrão 3).
4. **Aprender com o resultado:** por rede, visitas e conversões pela
   `utm_source`; a rede que traz resultado ganha prioridade no teto semanal.
5. **Teto e espaçamento:** no máximo N por semana por destino, nunca dois
   posts do mesmo artigo na mesma rede em menos de 30 dias.

## Fluxo

```
artigo no ar ──▶ escolha (regras) ──▶ PostSocial "sugerido"
                                        │  LLM escreve o texto (prompt por rede)
                                        │  Instagram: monta as lâminas
                                        ▼
                         aprovação (padrão: sempre) ──▶ "agendado" ──▶ API publica
                                        │                                │
                                        └── sem API: "copiar para postar" ┘
                                                                         ▼
                                                     url do post, utm, resultado
```

## Modelo de dados (tenant)

- `DestinoSocial`: rede (`gmn`, `linkedin`, `instagram`), autor (para o
  LinkedIn: pessoal ou página), conta/IDs, credenciais cifradas e validade,
  `aprovacao` (`sempre` | `automatico`, padrão `sempre`), teto semanal,
  descrição do público, ligado/desligado.
- `PostSocial`: artigo, destino, motivo da escolha (novo, subindo,
  converte), texto, imagens (lâminas), link com utm, situação (`sugerido`,
  `aprovado`, `publicado`, `falhou`, `descartado`), agendado para, URL e id
  remotos, erro.
- Prompts novos (semeados): `post_gmn`, `post_linkedin`,
  `post_instagram` (legenda + frases das lâminas), com as regras: sem
  promessa de cura, sem urgência falsa, só o que o artigo diz.

## Telas

- **Menu "Redes"**: os posts sugeridos e agendados por destino, com prévia
  (como aparece na rede), editar, aprovar, descartar e **"Copiar para
  postar"** (texto + link com utm; lâminas para baixar).
- **Configuração**: conectar cada destino (OAuth), aprovação, teto, público.
- **Artigo**: aba com os posts dele e o resultado por rede.
- **Painel**: token perto de vencer; post que falhou.

## Fases (quando o dono pedir)

1. **Copiar para postar** nas três redes: escolha, texto por rede, lâminas do
   Instagram, utm e medição. Não depende de nenhuma API.
2. **LinkedIn pela API** (pessoal primeiro; página quando a revisão sair).
3. **Google Meu Negócio pela API** (quando o Google liberar o acesso).
4. **Instagram pela API** (app da Meta).

O que o dono pode adiantar: pedir o acesso à Business Profile API do Google;
criar o app no LinkedIn Developers e pedir a Community Management API; ter a
conta do Instagram como profissional, ligada a uma página do Facebook.
