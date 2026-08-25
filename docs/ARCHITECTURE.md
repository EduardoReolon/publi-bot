# Documentação de Arquitetura: Motor de Geração de Conteúdo IA (SaaS)

## 1. Visão Geral do Sistema

O sistema é um orquestrador centralizado (SaaS) projetado para gerenciar, gerar iterativamente, aprovar e publicar conteúdo focado em SEO para múltiplos sites (Nós Finais) utilizando Modelos de Linguagem (LLMs). A arquitetura suporta *multi-tenancy* (múltiplos projetos/clientes isolados) e desacopla o motor de inferência (hardware local) da lógica de negócios e aprovação (nuvem).

O fluxo prioriza a qualidade técnica, aderência a regras de SEO, controle de duplicidade, embasamento estruturado em fontes reais (Summary Indexing RAG) e revisão humana obrigatória (YMYL). O sistema atua em duas frentes de publicação: **(A) Geração Proativa de Artigos Pilar** e **(B) Resolução Reativa de Q&A (Perguntas de Usuários)**. A distribuição do conteúdo de ambas as frentes é regida por uma Cron Engine, garantindo cadência consistente.

---

## 2. Componentes da Arquitetura



### A. SaaS Central (O Cérebro na Nuvem)



Hospedado em um servidor na nuvem, atuando como o núcleo do sistema.



* **Tecnologia:** Django (Painel Administrativo, ORM), Celery + Redis/RabbitMQ (Fila de Tarefas Assíncronas), PostgreSQL + `pgvector` (Banco de Dados e Vetores).

* **Responsabilidades:**

* Gerenciar usuários, projetos (Nós Finais) e bases de conhecimento (RAG).

* Consultar Nós Finais para obter contexto de SEO (evitar canibalização).

* Orquestrar o fluxo de geração de agentes via **LangChain** / **CrewAI**.

* Prover a interface humana para aprovação, edição e agendamento de postagens.

* Disparar o "Push" final do conteúdo aprovado.







#### B. O Worker Local (O Motor de Inferência e Extração)

Computador com hardware dedicado (GPU) localizado em uma rede segura (Tailscale). Não possui banco de dados próprio, atuando puramente como uma API de processamento.

* **Tecnologia:** Django (Painel Administrativo, ORM), **biblioteca padrão de multi-tenancy do Django (esquemas baseados em banco)**, Celery + Redis/RabbitMQ (Fila de Tarefas Assíncronas), PostgreSQL + `pgvector` (Banco de Dados e Vetores). A infraestrutura de produção utiliza **Gunicorn** como servidor WSGI e **Nginx** como proxy reverso.
* **Comportamento Assíncrono e Fila de Trabalho:** Como o Worker Local é uma máquina pessoal (sujeita a estar desligada ou com recursos limitados), o sistema **não** envia múltiplas requisições simultâneas aguardando resposta em tempo real. O SaaS Central empilha as tarefas (geração de texto ou conversão de PDF) em uma fila no Celery (RabbitMQ/Redis).
* **Consumo Controlado:** O Worker Local "puxa" as tarefas uma a uma ou o Celery gerencia a concorrência para nunca sobrecarregar a GPU. Se o Worker estiver desligado, as tarefas aguardam na fila em segurança até que ele seja ligado, sem gerar erros de *timeout* no SaaS.
* **Versionamento Dinâmico de Prompts:** Os *System Prompts* (ex: "Filtro de Consenso", "Redação SEO") não são hardcoded no código. Eles são armazenados no banco de dados (Tabela `PromptTemplate`), permitindo ajustes rápidos e testes de comportamento (A/B testing) da LLM diretamente pelo painel administrativo, sem necessidade de novos *deploys*.




### C. Nós Finais (Os Sites/Front-ends)



Os sites onde o conteúdo será publicado (ex: Consultoria B2B, Enfermagem Obstétrica).



* **Tecnologia:** Qualquer framework web (Django, WordPress, etc.) que exponha a API padrão.

* **Responsabilidades:** Fornecer contexto quando solicitado e receber/salvar novos artigos em seu próprio banco de dados.

---

## 3. Módulo de Base de Conhecimento (RAG & Citações para SEO)

Este módulo garante a veracidade do conteúdo e injeta credibilidade técnica (E-E-A-T), abandonando o estilo de citação acadêmica em favor de links de saída (outbound links) otimizados para buscadores.

### A. Ingestão Assíncrona e Processamento de Visão (Document Layout Analysis)

Para extrair dados confiantemente, o sistema utiliza um pipeline de Visão Computacional.

* **O Fluxo de Upload:** O usuário faz o upload de um PDF no SaaS Central e seleciona a categoria do documento (ex: *"Artigo Científico"*, *"Termo de Referência"*, *"Relatório"*).
* **Fila de Processamento:** O PDF é enviado para a fila assíncrona do Celery.
* **Conversão Local:** Quando o Worker Local estiver online e disponível, ele processa o PDF usando **Docling/MinerU** (ou similar) e devolve para o SaaS o documento inteiramente convertido em **Markdown estruturado**.

### B. Curadoria Humana e Indexação Estrutural (Summary Indexing)

O sistema não joga o Markdown diretamente no banco de dados vetorial. O fluxo exige confirmação humana para garantir a pureza dos dados.

* **Extração Automática de Metadados:** As bibliotecas tentam pré-preencher Autor(es), Ano e Título a partir do cabeçalho do Markdown. O sistema formata automaticamente os autores (ex: converte listas longas para *"Sobrenome et al."*).
* **A Interface de Confirmação:** O usuário acessa a área de documentos pendentes no SaaS. Ele visualiza o Markdown gerado e pode:
* Corrigir ou confirmar os campos de Autor, Título e Ano.
* Inserir o URL de origem do documento (necessário para a estratégia de SEO).
* **Selecionar o "Super Chunk":** O usuário destaca qual parte do Markdown representa a essência do documento (geralmente o Abstract e a Conclusão).


* **Armazenamento Híbrido:**
1. O *Super Chunk* selecionado pelo usuário é vetorizado e salvo no banco `pgvector` junto com seus metadados.
2. O documento **Markdown completo** é compactado (ex: GZIP) e salvo em uma coluna de banco de dados relacional padrão (PostgreSQL). Isso preserva o documento estruturado original para futuras mudanças de estratégia no RAG, sem necessidade de reprocessamento do PDF.



### C. Injeção de Contexto e SEO (Estratégia de "Artigo Pilar")

* O sistema localiza 1 a 3 "Super Chunks" relevantes para o tema no `pgvector`.
* **A Regra de Citação (SEO):** O agente abandona o formato "[Autor, Ano]" no fim de cada frase. Em vez disso, o sistema seleciona o estudo com maior autoridade (com base na URL original fornecida na etapa de confirmação) e instrui a LLM a inserir **apenas 1 ou 2 links de saída (dofollow)** em formato de texto-âncora natural (Ex: *"De acordo com uma pesquisa de [Sobrenome et al., Ano] (Link)..."*), imitando curadoria humana de alto nível.

---

## 4. Fluxo de Execução (O Ciclo de Vida do Conteúdo)

### Fluxo A: Geração de Artigos Pilar (Proativo)

1. **Ingestão de Contexto de SEO:** O Celery acessa a rota `/api/seo-context/` do Nó Final para evitar canibalização de palavras-chave.
2. **Geração de Pautas:** O SaaS pede à LLM sugestões de 5 novos títulos.
3. **Aprovação de Pauta (Humana):** O usuário aprova os títulos desejados.
4. **Produção Iterativa (Com Filtro Anti-Frankenstein):**
* **Retrieval:** O SaaS busca "Super Chunks" no `pgvector`.
* **Filtro de Consenso:** A LLM lê os resumos encontrados e gera uma "Tese" interna.
* **Drafting:** A LLM escreve o artigo inteiro baseado nesta Tese, inserindo o link da fonte primária.


5. **Revisão Final (Humana):** O usuário aprova o texto e a imagem gerada (Stable Diffusion).
6. **Agendamento e Push:** O artigo entra na Fila de Reserva e é enviado para `/api/publish/` via POST no momento agendado.

### Fluxo B: Resolução de Q&A (Reativo)

1. **Coleta de Perguntas:** O Celery acessa a rota `/api/pending-questions/` do Nó Final e importa perguntas deixadas por usuários no site.
2. **Resolução via RAG:**
* O SaaS busca "Super Chunks" no `pgvector` que respondam à dúvida.
* A LLM (Worker Local) redige uma resposta direta e objetiva, citando a fonte.
* Se a similaridade no banco vetorial for baixa, a pergunta é marcada como *"Requer Ingestão de Novos Documentos"*.


3. **Revisão Humana (Q&A):** A resposta fica com status "Pendente". Um humano revisa, garantindo a precisão técnica da resposta.
4. **Devolução (Push):** Após aprovação, o SaaS envia a resposta para `/api/publish/` (com a flag `type: qa`), o que atualiza o site de origem e notifica o usuário, contando como uma nova publicação de SEO para o site.


#### Resiliência em Cadeias Longas (LLM Chains) e Interrupções

Como a produção do texto envolve múltiplas rodadas sequenciais de chamadas à LLM local (ex: 1. Sumarizar -> 2. Criar Tese -> 3. Escrever -> 4. Revisar) e o Worker Local pode ser desconectado a qualquer momento, o fluxo exige persistência de estado.
* **Stateful Celery:** Cada passo do raciocínio do Agente (CrewAI/LangChain) salva seu output temporário no banco de dados ou no *cache* do Redis atrelado ao *Task ID*.
* **Graceful Resume:** Se o Worker desligar no passo 3, o SaaS não marca a tarefa principal como "Falha". A tarefa entra em *retry* suspensa. Quando o Worker retornar, o Celery retoma o processo a partir do passo 3 (injetando o contexto salvo dos passos 1 e 2), evitando o desperdício de GPU e tokens nas etapas que já haviam sido concluídas.

---

## 5. Segurança na Comunicação (SaaS ↔ Nó Final)



A comunicação entre o SaaS na Nuvem e os Nós Finais (que estão na internet pública) utiliza **Autenticação Baseada em Token Fixo (API Key)**.



* Cada Nó Final possui uma `SECRET_API_KEY` única, armazenada em suas variáveis de ambiente (`.env`).

* O SaaS Central possui o registro dessas chaves para cada Nó cadastrado.

* Todas as requisições disparadas pelo SaaS devem incluir a chave no cabeçalho HTTP:

`X-API-KEY: a1b2c3d4-e5f6-7890-abcd-ef1234567890`

* *Defesa em Profundidade:* O Nó Final deve rejeitar imediatamente qualquer requisição para as rotas `/api/` que não contenha a chave correta (Erro 401 ou 403).

---

Sua arquitetura agora está blindada! Você resolveu o problema de formatação (com os Vision Parsers), o problema das alucinações (usando Summary Indexing) e a regra de ouro do SEO (Substituindo o "paper acadêmico" por curadoria de links âncora).

## 6. Contrato de Interface (APIs dos Nós Finais)

Qualquer site que for conectado ao SaaS (seja Django, WP, etc.) deve implementar exatamente as duas rotas abaixo:

### Rota 1: Fornecimento de Contexto

* **Endpoint:** `GET /api/seo-context/`
* **Descrição:** Retorna os dados necessários para o SaaS entender o estado atual do site e gerar novos títulos sem repetir assuntos.
* **Headers Exigidos:** `X-API-KEY`
* **Resposta Esperada (200 OK):**
```json
{
  "site_title": "Nome do Site Consultoria",
  "home_content_text": "Texto limpo e sem tags extraído da home page...",
  "published_h1s": [
    "Artigo sobre estratégia de dados 1",
    "Artigo sobre automação 2"
  ]
}

```



### Rota 2: Recebimento de Publicação (Push)

* **Endpoint:** `POST /api/publish/`
* **Descrição:** Rota unificada para salvar um novo artigo ou publicar uma resposta de Q&A.
* **Payload de Envio (Diferenciado pelo campo `type`):**

```json
{
  "type": "article", // Pode ser "article" ou "qa"
  "title": "Aplicações avançadas...", // Vazio se for Q&A
  "question_id": 145, // ID da pergunta original (apenas para tipo "qa")
  "html_content": "<p>Texto da resposta ou artigo...</p>",
  "cover_image_base64": "...", // Vazio se for Q&A
  "status": "published" 
}

```


* **Resposta Esperada (201 Created):**
```json
{
  "status": "success",
  "message": "Artigo recebido e publicado com sucesso.",
  "url": "https://dominio-do-site.com.br/blog/aplicacoes-avancadas-e-praticas-mercado-b2b"
}

```

### Rota 3: Coleta de Perguntas Pendentes (Q&A)

* **Endpoint:** `GET /api/pending-questions/`
* **Descrição:** Retorna uma lista de perguntas enviadas pelos visitantes do Nó Final que ainda não foram respondidas. O SaaS consome essa rota para alimentar a fila do agente de Q&A.
* **Headers Exigidos:** `X-API-KEY`
* **Resposta Esperada (200 OK):**

```json
{
  "pending_questions": [
    {
      "id": 145,
      "author_name": "João Silva",
      "question_text": "O uso contínuo da substância X prejudica a função renal?",
      "submitted_at": "2026-08-24T14:30:00Z"
    },
    {
      "id": 146,
      "author_name": "Maria",
      "question_text": "Qual a diferença entre a abordagem A e B na hipertrofia?",
      "submitted_at": "2026-08-25T09:15:00Z"
    }
  ]
}

```

### 7. Motor de Cadência e Agendamento (Cron Engine)

Para manter o engajamento contínuo e a frequência de rastreamento (crawl budget) dos motores de busca otimizada, o SaaS opera com uma fila de distribuição temporal.

#### A. Configuração de Cadência por Nó Final

* No Painel Administrativo do SaaS, cada projeto/Nó Final recebe uma configuração de cadência de publicação (ex: *1 artigo a cada terça e quinta às 10h*, ou *1 artigo a cada 15 dias*).
* A configuração é flexível e armazena o *timezone* (fuso horário) específico do cliente para garantir que a publicação ocorra no horário comercial adequado.

#### B. A Fila de Reserva (Buffer de Publicação)

* O objetivo do sistema é sempre manter uma fila de reserva (buffer) de artigos no status **"Aprovado e Agendado"**.
* O Celery Monitora o tamanho dessa fila. Se o número de artigos aprovados para um Nó Final cair abaixo de um limite crítico (ex: faltam menos de 2 artigos para as próximas publicações), o SaaS dispara automaticamente o "Passo 2" do Fluxo de Execução (Geração de Pautas), alertando o usuário para iniciar a produção de um novo lote.

#### C. O Gatilho de Publicação (Cron Job)

* O Celery Beat roda rotinas minuto a minuto verificando o banco de dados.
* Quando a data e hora atuais coincidem com a data programada de um artigo com status "Aprovado e Agendado", o Celery dispara uma *Task* assíncrona.
* A *Task* empacota o JSON (HTML do texto, imagem em base64, metadados de SEO) e faz o "Push" (POST) para a rota `/api/publish/` do Nó Final.
* Em caso de resposta `201 Created` do Nó Final, o SaaS altera o status do artigo internamente para **"Publicado"** e grava a URL final recebida.
* Se houver falha de rede (ex: Nó Final fora do ar, Erro 500), a *Task* entra em *Exponential Backoff* (tentará novamente em 5 min, depois 10 min, 30 min, etc.) até garantir a entrega.

## 8. Estrutura de Deploy e Infraestrutura

A aplicação adota práticas padrão para ambiente de produção em servidores Linux (ex: Ubuntu/Debian).

* **Diretório de Deploy:** Na raiz do projeto, a pasta `deploy/` concentra todos os arquivos de configuração de infraestrutura, mantendo o versionamento do ambiente.
* **Componentes contidos em `deploy/`:**
* **NGINX (`.conf`):** Configurações do *Reverse Proxy*, roteamento para o Gunicorn, terminação SSL (HTTPS) e entrega estática.
* **Systemd (`.service` / `.socket`):** Arquivos de daemon para inicializar e gerenciar o ciclo de vida dos serviços em *background*:
* `nome_aplicacao.service` (Servidor Web da aplicação rodando via Gunicorn, permitindo reload).
* `nome_aplicacao.socket` (Socket Unix para comunicação segura de alta performance entre Nginx e aplicação).
* `celery-nome_aplicacao.service` (Consumidor das filas padrão de processamento assíncrono).
* `celery-beat-nome_aplicacao.service` (Motor do Cron/Agendador de tarefas e publicações).