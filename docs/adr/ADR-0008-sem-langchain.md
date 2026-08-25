# ADR-0008 — Orquestracao sem LangChain e sem CrewAI

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O `ARCHITECTURE.md` mandava orquestrar via LangChain ou CrewAI, mas a stack do
README nao os mencionava e nada estava instalado.

A motivacao declarada para consideralos era concreta e legitima: **poder trocar
o provedor de LLM**. Hoje Ollama; amanha Together; depois OpenAI.

## Decisao

**Nao usar nenhum dos dois.**

A troca de provedor e resolvida por uma interface fina, `LLMClient`, com
implementacoes selecionadas pelo campo `kind` de `InferenceConnection`:

- `OpenAICompatibleProvider` — atende Ollama, Together, Groq, OpenAI, DeepSeek,
  vLLM e LM Studio, porque todos falam o mesmo `/v1/chat/completions`.
- `AnthropicProvider` — o unico que exige codigo proprio.

Trocar de provedor passa a ser **editar uma linha no painel**, sem deploy e sem
codigo.

O motivo decisivo para recusar os frameworks e outro: **o estado do laco de
agente vive DENTRO do framework, exatamente onde a retomada apos interrupcao
precisa que ele NAO esteja.** As duas abstracoes disputam o mesmo papel, e uma
delas teria que ceder. Para uma cadeia de tres ou quatro passos deterministicos
e conhecidos de antemao, o framework so acrescenta dezenas de dependencias
transitivas e um concorrente para a fonte da verdade.

## Consequencias

- Cada passo persiste seu resultado em `GenerationJob.step_payloads`, no banco.
  Os passos sao **dados**, nao codigo.
- O plano de longo prazo (gerar paragrafo a paragrafo, aplicar revisoes e
  contraprovas) e servido por esse desenho: adicionar um passo novo e uma linha
  a mais na maquina de estados, sem refatoracao.
- Chamadas ao Ollama usam `httpx` com tempo limite explicito. Com pool `solo` do
  Celery o `soft_time_limit` nao funciona (depende de SIGALRM), entao o limite
  precisa vir do cliente HTTP.

## Correcao de documentacao exigida

`ARCHITECTURE.md:31` e `:137` — remover as mencoes a LangChain e CrewAI.
