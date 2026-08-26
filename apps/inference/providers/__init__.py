"""Adaptadores de provedor de LLM.

Substituem o LangChain (ADR-0008). A troca de provedor e o problema que
motivava o framework, e aqui ela custa duas classes — nao dezenas de
dependencias transitivas e um laco de agente cujo estado compete com o
`GenerationJob`.

O que torna isso barato: Ollama, Together, Groq, OpenAI, DeepSeek, vLLM e
LM Studio falam todos o mesmo `/v1/chat/completions`. Um unico adaptador cobre
todos; so a Anthropic precisa de codigo proprio.

Trocar de provedor passa a ser editar uma linha de `InferenceConnection` no
painel: sem deploy, sem codigo.
"""

from apps.inference.providers.base import (
    LLMClient,
    LLMResponse,
    ProviderPermanentError,
    ProviderTransientError,
    get_provider,
)

__all__ = [
    "LLMClient",
    "LLMResponse",
    "ProviderPermanentError",
    "ProviderTransientError",
    "get_provider",
]
