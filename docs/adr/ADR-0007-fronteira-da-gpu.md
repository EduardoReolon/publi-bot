# ADR-0007 — Fronteira da GPU e transporte de arquivos

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

A especificacao dizia que *"o PDF e enviado para a fila assincrona do Celery"*.
Implementado ao pe da letra, cada mensagem carregaria de 2 a 50 MB inflados em
base64 **dentro do Redis, que e memoria pura**: o broker viraria armazenamento
de arquivo, cada nova tentativa duplicaria o payload, e com o worker dias
offline a fila acumularia gigabytes de RAM. Nao existia sequer `MEDIA_ROOT`
definido.

A especificacao tambem se contradizia sobre o Worker Local: uma linha afirmava
que ele *"nao possui banco de dados proprio"*, e a linha seguinte, sobre o
mesmo componente, listava Django, PostgreSQL, Gunicorn e Nginx.

## Decisao

**A maquina com GPU expoe servicos HTTP e nada mais.** Nao roda Django, nem
Celery, nem banco, nem servidor de aplicacao.

| Servico na maquina com GPU | Papel |
|---|---|
| `ollama serve` | Geracao de texto |
| Wrapper HTTP do Docling (`worker-gpu/`) | PDF para Markdown |
| ComfyUI ou equivalente (futuro) | Geracao de imagem |

Todos escutam **apenas no endereco da rede privada (Tailscale)**, nunca em
`0.0.0.0`, e exigem segredo compartilhado.

**Os arquivos ficam na nuvem.** O upload vai para
`MEDIA_ROOT/<schema_name>/...` via `TenantFileSystemStorage`, com a subpasta
por tenant resolvida automaticamente. A tabela `Document` guarda o registro; a
fila carrega apenas o identificador.

Quando o Docling precisa do PDF, a nuvem o envia no corpo da requisicao HTTP
para o endpoint, ou disponibiliza uma URL assinada de curta duracao. O worker
valida o `sha256` contra `Document.file_sha256` antes de processar. **Em
nenhum caso bytes trafegam pelo broker.**

## Consequencias

- Nao existe fila `gpu` consumida remotamente, nem worker Celery fora da nuvem.
- A fila vive no banco da nuvem, no `GenerationJob`, e por isso e inspecionavel
  no painel: "job 47, passo 3 de 4, aguardando GPU desde as 14h". Se vivesse no
  Redis seria invisivel — que era exatamente a critica ao "Graceful Resume"
  original.
- Um unico mecanismo serve tanto a GPU local quanto as APIs hospedadas. Ver
  ADR-0012.
- Migrar para armazenamento S3 depois e trocar a classe de storage, mantendo o
  mesmo prefixo por tenant.
- `DATA_UPLOAD_MAX_MEMORY_SIZE` sobe para 50 MB, e o Nginx precisara de
  `client_max_body_size` correspondente.

## Correcao de documentacao exigida

`ARCHITECTURE.md:45-47` — reescrever a descricao do Worker Local, movendo
Gunicorn, Nginx, PostgreSQL e pgvector para a secao do SaaS Central.
