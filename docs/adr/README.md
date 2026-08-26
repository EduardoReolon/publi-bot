# Registros de Decisao de Arquitetura (ADR)

Cada arquivo aqui grava **uma** decisao: o contexto que a forcou, a escolha, e
o que passa a ser verdade por causa dela.

Formato: MADR simplificado — Contexto / Decisao / Consequencias / Status / Data.

Um ADR nunca e editado depois de Aceito. Se a decisao mudar, escreve-se um novo
ADR que declara o anterior como Substituido, e o antigo permanece no repositorio
com esse status. O valor do registro esta justamente em preservar o raciocinio
de quem decidiu com a informacao daquele momento.

| # | Decisao | Status |
|---|---|---|
| [0001](ADR-0001-proposito-e-escopo.md) | Proposito e escopo do produto | Aceito |
| [0002](ADR-0002-nome-e-identidade.md) | Nome canonico do projeto | Aceito |
| [0003](ADR-0003-multi-tenancy.md) | Multi-tenancy por schema | Aceito |
| [0004](ADR-0004-postgres-pgvector.md) | PostgreSQL, pgvector e o indice HNSW | Aceito |
| [0005](ADR-0005-embeddings.md) | Modelo de embedding e onde ele roda | Aceito |
| [0006](ADR-0006-usuarios-e-chaves.md) | Modelo de usuario e chaves primarias | Aceito |
| [0007](ADR-0007-fronteira-da-gpu.md) | Fronteira da GPU e transporte de arquivos | Aceito |
| [0008](ADR-0008-sem-langchain.md) | Orquestracao sem LangChain e sem CrewAI | Aceito |
| [0009](ADR-0009-idiomas.md) | Ingles no codigo, portugues na interface | Aceito |
| [0010](ADR-0010-mapa-de-apps.md) | Mapa canonico de apps e models | Aceito |
| [0011](ADR-0011-django-5-2-lts.md) | Django 5.2 LTS | Aceito |
| [0012](ADR-0012-inferencia-como-endpoint.md) | Inferencia como endpoints com reserva | Aceito |
| [0013](ADR-0013-broker-postgres-em-dev.md) | Broker do Celery no PostgreSQL em desenvolvimento | Aceito |
