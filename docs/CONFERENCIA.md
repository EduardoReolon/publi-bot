# Roteiro de conferência

O que entrou no PubliBot desde o guia editorial, e como conferir cada parte
na tela. Está em ordem de **quando dá para testar**: primeiro o que funciona
sem nenhuma conta externa, depois o que depende do YouTube, do Search Console
e, por último, da DataForSEO.

Cada item tem **Onde**, **Faça** e **Espere**. Marque o que conferiu; o que
não bater com o "Espere" é bug — anote a tela e a mensagem.

> Conta da DataForSEO ainda bloqueada? Quase tudo das partes 1 a 4 funciona
> sem ela. Na parte 2 há um comando que enche o radar com dados de exemplo,
> para ver as telas que só se preenchem com buscas.

---

## 0. Antes de tudo: atualizar o servidor

```bash
git pull
venv/bin/pip install -r requirements.txt        # entrou o defusedxml
venv/bin/python manage.py migrate_schemas
venv/bin/python manage.py semear_prompts --todos  # prompts novos: opportunity_brief, seed_suggestion
sudo systemctl restart publibot celery-publibot celery-beat-publibot
```

- [ ] **Espere:** as três unidades `active`, e o beat com os agendamentos
      novos. Confira no log do beat (`journalctl -u celery-beat-publibot -n 50`)
      que aparecem `colher-fila-do-radar` (5 min), `descrever-oportunidades`
      (1 h), `atualizar-contexto-dos-sites`, `conferir-fontes-vencidas` e
      `coletar-metricas-dos-sites` (1 dia cada). A migração copia o "nicho"
      do site para o tema do Negócio.
- [ ] **Sem o beat, nada acontece sozinho** — nem a rodada com fila termina.

Variáveis novas no `.env`: nenhuma obrigatória. `MEDIA_ROOT` só se quiser a
mídia fora do projeto (item 1.7).

---

## 1. Sem nenhuma conta externa

### 1.1 Layout e abas
- [ ] **Onde:** qualquer tela. **Espere:** fundo cinza-azulado, seções em
      cartões brancos, menu escuro, avisos coloridos (verde = feito, vermelho =
      erro).
- [ ] **Onde:** Radar. **Espere:** quatro abas no topo — *Demanda e pautas*,
      *Oportunidades*, *Atualizar artigos*, *Configuração e custos*.
- [ ] **Onde:** Radar › Configuração e custos. **Espere:** os campos em
      blocos (Ritmo e temas, Onde, Fontes de demanda, Fontes para os artigos,
      Artigos publicados, Concorrentes, Buscador e custo); caixas de marcar ao
      lado do texto.

### 1.2 Guia editorial
- [ ] **Onde:** menu *Guia editorial*. **Faça:** escolha um modo em "Ponto de
      partida" e clique *Aplicar modo*. **Espere:** voz, termos e regra de ouro
      preenchidos.
- [ ] **Faça:** acrescente um termo proibido (ex.: `cura`), salve, e abra um
      artigo em revisão que contenha a palavra. **Espere:** um quadro vermelho
      "Termos proibidos pelo guia editorial"; *Aprovar* é recusado até marcar
      que revisou e mantém.
- [ ] **Espere também:** marcas de texto de máquina ("vale ressaltar", "em
      suma") aparecem como aviso, sem bloquear.

### 1.3 Acervo: categorias e novas entradas
- [ ] **Onde:** Documentos › Categorias. **Faça:** *Criar as categorias padrão
      que faltam*. **Espere:** colunas Natureza, Ideia central (sustenta / só
      contexto), Citação e Validade.
- [ ] **Onde:** Documentos › Enviar documento › *Ou uma página da web*.
      **Faça:** cole a URL de um artigo. **Espere:** o documento entra na
      curadoria com título, autor e data tirados da página.
- [ ] **Faça:** envie um `.docx`, `.pptx` ou `.xlsx`. **Espere:** convertido
      sem passar pelo worker (seções pelos títulos do documento).
- [ ] **Onde:** Enviar documento › *Escrever nota do especialista*. **Espere:**
      a nota vira fonte atribuída a quem escreveu, sem URL.
- [ ] **Espere, ao gerar um artigo com fonte sem URL** (nota ou categoria
      "atribuição"): a citação aparece como "segundo Fulano", sem link — e não
      derruba mais o artigo.

### 1.4 Busca híbrida
- [ ] **Onde:** Documentos › Qualidade da busca › *Testar uma consulta*.
      **Faça:** busque uma sigla ou termo exato que existe num documento (ex.:
      `SINAPI`, `BDI`). **Espere:** o trecho que contém o termo aparece entre os
      primeiros, mesmo com distância um pouco acima do corte (é a folga do
      texto exato).

### 1.5 Perguntas frequentes (FAQ)
- [ ] **Onde:** Artigos › um artigo em revisão › bloco *Perguntas frequentes*.
      **Espere:** até 6 sugestões; só as marcadas vão ao site, num campo
      separado do corpo. Se o site não declara `faq`, aparece o aviso.

### 1.6 Radar: o que você já viu, corrigido
- [ ] **Onde:** Radar › Configuração e custos. **Espere:** o buscador mostrado
      é **DataForSEO** (sem SearXNG configurado, as buscas vão direto a ela).
- [ ] **Espere, com 23 sementes e intensidade Mínimo:** cada rodada busca 5
      diferentes — em 5 rodadas passam todas (revezamento).
- [ ] **Espere:** rodada que só teve as sementes (sem dado de demanda) não cria
      pauta. O tema fica em observação, e *Virar pauta* funciona nele.
- [ ] **Espere:** erro da DataForSEO aparece na rodada com o código e o motivo
      (ex.: `40201 ... paused access`), com o selo "com erros".

### 1.7 Mídia fora do projeto (se quiser)
- [ ] **Faça:** siga `docs/OPERACAO.md`, seção "Mídia fora do projeto"
      (`.env`, `alias` do Nginx, override do systemd). **Espere:** um PDF
      enviado aparece em `<MEDIA_ROOT>/<schema>/documents/...` e o download
      pela tela funciona (antes desta correção, com `USAR_X_ACCEL=true`, todo
      download dava 404).

### 1.8 Concorrentes pelo sitemap (gratuito)
- [ ] **Onde:** Configuração e custos › Concorrentes. **Faça:** um domínio por
      linha (`concorrente.com.br`) e marque *O que os concorrentes publicam
      (sitemap)*. Rode o radar. **Espere:** temas com a fonte "Publicado por
      concorrente" (o título sai do endereço da página; tag, categoria e
      "contato" ficam de fora). Em *Últimas chamadas externas*, uma chamada
      "Página web" por concorrente.

### 1.9 Caminhos: "Nunca sugerir"
- [ ] **Onde:** Documentos › Caminhos confiáveis. **Faça:** cadastre um
      domínio com o nível *Nunca sugerir* (sem categoria). **Espere:** salvo com
      selo amarelo. Nada daquele endereço será sugerido como fonte (parte 5).

---

## 2. Com dados de exemplo (enquanto a DataForSEO não responde)

As telas de temas, oportunidades, concorrentes e atualizações só se enchem
com buscas. Este comando cria dados **inventados e marcados**:

```bash
venv/bin/python manage.py tenant_command radar_exemplo --schema=<seu_schema>
```

- [ ] **Radar › Demanda e pautas.** **Espere:** temas como "como calcular o
      lifetime value do cliente", com nota colorida (verde ≥ 60, amarelo ≥ 40)
      e as barras "de onde vem a nota". *Virar pauta* cria a pauta; *Descartar*
      tira o tema.
- [ ] **Sementes sugeridas** (mesma tela). **Faça:** *Aceitar* a semente e a
      dor. **Espere:** a semente vai para Palavras-semente (Configuração) e a
      dor para Dores do público (Oportunidades).
- [ ] **Possíveis concorrentes.** **Faça:** escreva um nome e clique *É
      concorrente*. **Espere:** o domínio entra na lista de concorrentes, com
      `| nome`. *Não é* tira da lista para sempre.
- [ ] **Oportunidades.** **Espere:** cartões com nota grande, termos
      (c-TF-IDF), buscas/mês, "ano a ano" (com selo *tendência* quando a subida
      é consistente), custo por clique, um minigráfico de 24 meses e as barras
      das parcelas. "Como reativar clientes inativos" é sazonal: sobe no fim do
      ano, e mesmo assim o ano a ano não o trata como novidade.
- [ ] **Faça:** *Testar com um artigo* numa oportunidade. **Espere:** vira
      pauta sugerida e a oportunidade passa para a aba "Em teste com artigo".
      *Acompanhar (virar semente)* acrescenta o tema às sementes. *Arquivar* esconde.
- [ ] **Atualizar artigos.** **Espere:** uma sugestão "Quase na primeira
      página" com a consulta e as impressões. Sem artigo do PubliBot por trás,
      só há *Atualizei* e *Dispensar* (com artigo, aparecem *Atualizar no
      PubliBot* e *Já atualizei no site*).

**Depois, apague** — os exemplos entram na nota dos temas de verdade:

```bash
venv/bin/python manage.py tenant_command radar_exemplo --schema=<seu_schema> --apagar
```

- [ ] **Espere:** as telas voltam ao que eram; temas reais continuam.

---

## 3. Com o modelo de linguagem no ar (worker-gpu)

- [ ] **Descrição de oportunidade.** **Onde:** Oportunidades › *Pedir descrição
      ao modelo*. **Espere:** em alguns minutos, "O problema", "O que poderia
      atender" e perguntas para validar. Com a placa desligada, nada quebra: a
      tentativa se repete de hora em hora. Para usar o modelo de 30B só nisso,
      escolha-o na versão do prompt `opportunity_brief`.
- [ ] **Sementes pelo site.** Precisa do site cadastrado e respondendo
      `/seo-context/` com `home_content_text`. **Onde:** Radar › *Sugerir a
      partir do site*. **Espere:** na hora, sugestões de semente vindas da
      página (algoritmo, sem modelo); depois, as do modelo — inclusive
      **dores**. O contexto do site é atualizado sozinho uma vez por dia.

---

## 4. Com o YouTube e o Search Console

### 4.1 YouTube (a chave você já cadastrou)
- [ ] **Onde:** Configuração e custos › marque *Comentários do YouTube*;
      intensidade Normal ou Intenso (Mínimo não busca vídeos). Rode o radar.
      **Espere:** mesmo com a DataForSEO falhando, a rodada colhe perguntas dos
      comentários (fonte "Comentário no YouTube") e sugere vídeos em Documentos
      › Fontes sugeridas.
- [ ] **Faça:** aprove um vídeo. **Espere:** a legenda vira documento; se o
      YouTube recusar a legenda, o vídeo fica "aguardando o áudio" com um campo
      para enviar o arquivo (a transcrição depende da rota do worker,
      `docs/WORKER_TRANSCRICAO.md`).
- [ ] **Faça:** em Fontes sugeridas, *Recusar o site inteiro* num vídeo.
      **Espere:** o bloqueio é do **canal**, não do YouTube inteiro.

### 4.2 Search Console
- [ ] Passo a passo em `docs/CONTAS_EXTERNAS.md`, item 4. **Onde:** Radar ›
      Search Console › *Coletar agora*. **Espere:** "Quase lá" e desempenho dos
      artigos. Sem o convite do e-mail da conta de serviço, a mensagem diz qual
      e-mail adicionar.
- [ ] **Espere, depois de uma coleta:** Atualizar artigos mostra páginas
      quase na primeira página e as que perderam posição.

---

## 5. Com a DataForSEO funcionando

Teste nesta ordem, olhando *Configuração e custos › Últimas chamadas
externas* a cada passo: ali aparecem o custo e, se der erro, a mensagem da
própria DataForSEO. **Estes endereços não puderam ser testados contra a API
real** — são os primeiros a conferir.

- [ ] **Conta.** No servidor:
      `curl -s -u 'LOGIN:API_PASSWORD' https://api.dataforseo.com/v3/appendix/user_data | head -c 300`
      → `"status_code": 20000`.
- [ ] **Busca manual (ao vivo).** Radar › Busca manual, com "Trazer volume".
      **Espere:** perguntas relacionadas e volume na hora; custo ~US$ 0,09.
- [ ] **Rodada pela fila.** *Rodar o radar*. **Espere:** a rodada fica
      "Aguardando resultados da fila" e termina sozinha em 5–15 minutos
      (depende do beat). Custo ~um terço do ao vivo. *(conferir: rotas
      `task_post`/`task_get`)*
- [ ] **Histórico de volume.** Depois de uma rodada completa, em
      Oportunidades, o "ano a ano" e o minigráfico com 24 meses. *(conferir: o
      campo `date_from`; se o volume falhar com "Invalid Field", é ele)*
- [ ] **Regiões.** Configuração › Onde › *Baixar lista de locais*, depois
      procure "Curitiba". **Espere:** cidades e estados para escolher; Curitiba
      + Paraná juntos é recusado ("contado duas vezes"). *(conferir: rota
      `locations/br`, e se o volume aceita código de cidade)*
- [ ] **Concorrentes sugeridos.** Depois de algumas rodadas, na tela
      principal, sites que apareceram na primeira página de 2+ buscas.
- [ ] **Buscas dos concorrentes (Labs)** e **Avaliações**: marque em
      Configuração, com concorrentes na lista (avaliações precisam do nome no
      Google depois do `|`). **Espere:** temas com fonte "Busca em que o
      concorrente aparece" (já com volume) e "Avaliação de concorrente" (só
      reclamação e pergunta). *(conferir: `ranked_keywords/live` e
      `business_data/google/reviews`)*
- [ ] **Fontes pelo radar.** Com *Sugerir fontes a partir das buscas do radar*
      ligado, depois de uma rodada: Documentos › Fontes sugeridas com itens
      "Achada nas buscas do radar", o motivo da classificação (ex.: "a página se
      declara blogposting") e no máximo 3/5/8 por rodada.
- [ ] **Bloqueio.** Numa sugestão, *Recusar esta área do site* e *Recusar o site
      inteiro*. **Espere:** o caminho aparece em Caminhos confiáveis como
      "Nunca sugerir", e as outras sugestões dali saem da fila.

---

## 6. Versões de artigo (atualizar um artigo que já está no ar)

Depende do **seu site** implementar a rota nova do contrato:
`PUT /api/v1/publications/{remote_id}/` e declarar `update` em `/health/`
(`docs/contrato/README.md`, "Atualização de artigo publicado", e a lista de
conferência no fim).

- [ ] **Onde:** Artigos › um artigo **publicado**. **Espere:** o aviso "Artigo
      no ar" com *Criar versão nova*.
- [ ] **Faça:** crie a versão. **Espere:** abre a revisão da **versão 2**, com o
      quadro azul explicando que ela substitui a mesma página, e o link para a
      versão anterior. Voltando ao artigo original: "Há uma versão nova deste
      artigo em andamento" (não cria duas).
- [ ] **Faça:** em Radar › Atualizar artigos, numa sugestão de um artigo do
      PubliBot, *Atualizar no PubliBot*. **Espere:** a versão nasce com "O que
      atualizar" preenchido (as perguntas novas ou as consultas).
- [ ] **Faça:** aprove a versão. **Espere:** agendada para **agora** (não entra
      na cadência).
- [ ] **Site sem `update`:** a versão vai para "Falha na publicação" com o
      motivo, e não tenta de novo.
- [ ] **Site com `update`:** a página no site muda, **no mesmo endereço**; a
      versão 1 fica como "Substituído por versão nova".
- [ ] **Vigia.** Artigos publicados há mais de 45 dias (Configuração ›
      Artigos publicados) passam a receber sugestões "Demanda nova sobre o
      mesmo tema" quando o radar acha um tema com volume tão perto do artigo
      quanto o tema que o originou.

---

## 7. Fonte que vence (tabela de preço do mês)

O caso da página de preço: o endereço fica, o dado muda todo mês.

- [ ] **Onde:** Documentos › Categorias. **Faça:** numa categoria de dado seu
      (ex.: "Preços próprios"), ponha a **validade** em 30 dias.
- [ ] **Faça:** envie a planilha de agosto nessa categoria, cure, e gere e
      publique um artigo que a cite.
- [ ] **Faça:** envie a planilha de setembro. Na curadoria dela, no campo
      **Substitui**, escolha a de agosto e conclua. **Espere:**
  - a de agosto passa a vencida (sai da busca);
  - em Radar › Atualizar artigos aparece "Fonte vencida ou substituída", com
    "Preços ago/2026 → Preços set/2026".
- [ ] **Faça:** *Atualizar no PubliBot*. **Espere:**
  - a versão nova abre com a mensagem "1 citação(ões) passaram para a fonte
    nova";
  - em Fontes citadas, a de setembro;
  - "O que atualizar" pede para conferir os números e a data do dado no texto.
- [ ] **Sem planilha nova:** quando a validade passa sozinha, a sugestão
      aparece no dia seguinte (conferência diária), marcada "sem versão nova
      no acervo".
- [ ] **Espere também:** decidida (feita ou dispensada), a mesma sugestão não
      volta; outra fonte vencendo no mesmo artigo volta.

---

## 8. Chamada para a oferta (a landing page)

- [ ] **Onde:** menu **Negócio** (item 10). **Faça:** preencha a **Oferta**
      como o cliente diria. Ex.: "Assinatura mensal: você manda as notas de
      material pelo WhatsApp e um engenheiro planilha os custos e diz se você
      pagou caro".
- [ ] **Faça:** gere um artigo de custo (ex.: "Como saber se o orçamento da
      obra está caro"). **Espere, na revisão, o bloco "Chamada para a
      oferta":**
  - a decisão do planejamento, com a proximidade do tema e da seção (em %);
  - com uma seção sobre conferir preço, "No meio" depois dela, e na prévia
    ("Como vai sair") um quadro tracejado "Chamada para a oferta (bloco do
    site)" no lugar.
- [ ] **Faça:** gere um artigo conceitual longe da oferta (ex.: "O que é BDI").
      **Espere:** "Nenhuma" ou "Só no fim"; com "Nenhuma", o fecho **sem**
      convite.
- [ ] **Faça:** mude na revisão (Onde / depois da seção › *Aplicar*).
      **Espere:** a marca `[[CHAMADA]]` muda de lugar no Texto. Mova a marca à
      mão no Texto e salve: o bloco "Chamada" passa a mostrar a seção nova.
      Apague a marca: vira "Só no fim".
- [ ] **Faça:** em Pautas › Nova pauta, o campo "Chamada para a oferta" força
      o modo. **Espere:** na revisão, "escolhida na pauta".
- [ ] **Sem oferta no Negócio:** aviso amarelo na revisão, e todo artigo fica
      "só no fim".
- [ ] **No site** (precisa do recurso `call_to_action`, ver
      `docs/contrato/README.md`, "Chamada para a oferta do site"): o bloco
      aparece no meio e no fim, ou só no fim, ou em nenhum lugar. Site sem o
      recurso: aviso na revisão.

---

## 9. Leitura e conversões (o "Google Ads" dos artigos)

Depende do site implementar o recurso `insights`: o script de medição e a
rota `GET /api/v1/insights/` (`docs/contrato/README.md`, "Leitura e
conversões"). O nó de referência em Django já traz os dois.

**No site, antes do PubliBot:**

- [ ] **Faça:** abra um artigo, role, fique uns 20 s, troque de aba.
      **Espere:** no DevTools › Rede, um `POST /api/v1/leitura/` com `active`
      perto de 20 e `end` verdadeiro se chegou ao fim.
- [ ] **Faça:** abra e feche outro artigo em 3 s. **Espere:** conta como
      abertura, não como leitura (menos de 10 s).
- [ ] **Faça:** clique no botão do WhatsApp dentro do bloco da chamada.
      **Espere:** `POST /api/v1/conversao/` com `via_cta: true` e a jornada com
      os dois artigos. No DevTools › Aplicação › Armazenamento local,
      `publibot:jornada` volta vazia.
- [ ] **Faça:** abra a landing page com `?gclid=teste` no fim do endereço e
      clique no WhatsApp. **Espere:** a conversão com `first_channel: "paid"`.
      Chegando por uma busca no Google, `"organic"`; digitando o endereço,
      `"direct"`.
- [ ] **Espere:** nenhuma requisição leva IP, cookie de identificação ou
      endereço completo de quem leu.

**No PubliBot (no dia seguinte, ou rode a coleta à mão):**

```bash
venv/bin/python manage.py shell -c "from apps.integrations.tasks import coletar_metricas_dos_sites as t; print(t())"
```

- [ ] **Onde:** Artigos › *Desempenho no site*. **Espere:**
  - por artigo: aberturas, leituras (% das aberturas), tempo ativo médio, até
    o fim, chamada vista / clicada;
  - as conversões em três colunas: último artigo, participou e atribuídas
    (meia para cada, no exemplo acima), ordenado por atribuídas;
  - em cima: quantas vieram pela chamada e quantas sem artigo nenhum.
- [ ] **Espere, com algumas semanas de dado:** no Radar, os temas perto de
      artigos que convertem ganham a barra **conversao**. Sem dado de
      conversão, a nota é exatamente a de antes.

---

---

## 10. Negócio: a referência de tudo que é medido

- [ ] **Onde:** menu **Negócio**. **Espere:** tema do site, público, dores
      do público, oferta e valores, mais a tabela "Como o PubliBot usa cada
      informação".
- [ ] **Espere, depois de atualizar o servidor:** o antigo "nicho" do cadastro
      do site aparece como **tema**, e a oferta que estava no Guia editorial
      aparece aqui. O Guia agora só tem o convite.
- [ ] **Faça:** preencha o **público**. **Espere:** no próximo artigo gerado,
      o planejamento usa esse público (antes usava o nicho, por engano).
- [ ] **Espere:** em Radar › Oportunidades, as dores aparecem só para leitura,
      com o link "Editar em Negócio".
- [ ] **Espere:** as barras da nota no Radar dizem a referência: "perto do
      tema do site", "perto do que já foi escrito", "fonte no acervo" — e não
      mais "aderencia".

---

## 11. PubliBot × anúncios

- [ ] **Onde:** Negócio › Valores. **Faça:** preencha valor de uma conversão,
      investimento mensal no PubliBot e em anúncios, e a cotação do dólar.
- [ ] **Valor do tráfego** (precisa de Search Console e DataForSEO). **Faça:**
      Radar › Search Console › *Coletar agora*. **Espere:** em Configuração e
      custos › Últimas chamadas externas, uma chamada de volume com a
      finalidade "Valor do tráfego" (só para as consultas que ainda não têm
      preço; repete a cada 90 dias).
- [ ] **Onde:** Artigos › Desempenho no site. **Espere:**
  - o quadro "PubliBot × anúncios": conversões pelo PubliBot, por anúncio
    (com a parte que leu artigo), outras;
  - "o que N cliques orgânicos nos artigos custariam em anúncio", em reais;
  - a tabela de custo por conversão, retorno e "os cliques do PubliBot,
    comprados em anúncio, por conversão";
  - por artigo, as colunas "Cliques no Google" e "Valor em anúncio".
- [ ] **Espere, no Radar:** temas cujos sinais têm custo por clique ganham a
      barra **valor comercial**; sem custo por clique, a nota não muda.

---

## 12. Links internos ("Leia também")

- [ ] **Faça:** com dois ou mais artigos publicados do mesmo assunto, publique
      outro. **Espere:** a publicação recebida pelo site traz
      `related_articles` com até 3 artigos vizinhos (no nó de referência, o
      bloco "Leia também" da tag `leia_tambem`).
- [ ] **Espere:** artigo de assunto diferente não entra; a própria página
      (outra versão do mesmo artigo) nunca entra.
- [ ] **No site** (recurso `related_articles`): um bloco "Leia também" com
      links comuns.

---

## Se algo não bater

| Onde olhar | O que mostra |
|---|---|
| Radar › Rodadas | situação de cada rodada, erros com o motivo |
| Configuração e custos › Últimas chamadas externas | cada chamada paga ou não, custo e erro |
| Operação | trabalhos de geração de artigo |
| `journalctl -u celery-publibot -n 100` | erro que não chegou à tela |
| `journalctl -u celery-beat-publibot -n 50` | se os agendamentos estão disparando |

Contas externas (onde criar, como testar cada uma): `docs/CONTAS_EXTERNAS.md`.
