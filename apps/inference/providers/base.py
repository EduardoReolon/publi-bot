"""Contrato comum aos provedores de LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Falha ao chamar um provedor."""


class ProviderTransientError(ProviderError):
    """Vale a pena tentar de novo: timeout, 5xx, 429, conexao recusada.

    A GPU local desligada cai aqui — o trabalho nao se perde, so espera.
    """


class ProviderPermanentError(ProviderError):
    """Nao adianta tentar de novo: credencial invalida, payload malformado,
    modelo inexistente.

    A distincao existe porque tratar tudo como transitorio faz uma chave
    rotacionada gerar tentativas infinitas em vez de um alerta.
    """


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImagemGerada:
    """Uma opcao de imagem, como o provedor a devolveu.

    Bytes crus e nao caminho de arquivo: quem grava decide o formato (WebP,
    sempre) e o lugar. O provedor so entrega pixels.
    """

    conteudo: bytes
    formato: str = ""
    prompt_revisado: str = ""


class ImageClient(ABC):
    """Interface de geracao de imagem.

    Separada de `LLMClient` porque a assinatura nao cabe: uma chamada de imagem
    devolve VARIAS opcoes, nao um texto, e nao tem contagem de tokens. Forcar as
    duas na mesma interface produziria parametros ignorados dos dois lados.
    """

    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def generate(
        self, *, model: str, prompt: str, quantidade: int = 3, tamanho: str = "1024x1024"
    ) -> list[ImagemGerada]:
        """Gera `quantidade` opcoes DIFERENTES para o mesmo texto.

        Varias de uma vez, e nao uma: escolher exige comparar. Uma opcao unica
        transforma a revisao em "aceita ou pede de novo", que e mais lento e
        entrega pior.
        """

    @abstractmethod
    def health(self) -> bool:
        """Se o endpoint responde."""


class LLMClient(ABC):
    """Interface que todo provedor implementa."""

    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        """Uma rodada de conversa.

        `json_schema` pede saida estruturada quando o provedor suporta. E o que
        permite ao filtro de consenso devolver
        `{tese, concordancia, pontos_divergentes}` em vez de texto livre que
        precisaria ser interpretado com expressao regular.
        """

    @abstractmethod
    def health(self) -> bool:
        """Se o endpoint responde."""


def get_provider(connection, *, timeout: float | None = None) -> LLMClient:
    """Instancia o adaptador certo para uma conexao.

    Este e o unico lugar do sistema que sabe qual classe atende qual tipo. Todo
    o resto trabalha contra `LLMClient`.
    """
    from apps.inference.models import InferenceConnection
    from apps.inference.providers.anthropic import AnthropicClient
    from apps.inference.providers.openai_compatible import OpenAICompatibleClient
    from apps.inference.security import decifrar_chave

    mapa = {
        InferenceConnection.Kind.OPENAI_COMPATIBLE: OpenAICompatibleClient,
        InferenceConnection.Kind.ANTHROPIC: AnthropicClient,
    }
    classe = mapa.get(connection.kind)
    if classe is None:
        raise ProviderPermanentError(
            f"A conexao {connection.name!r} e do tipo {connection.kind!r}, que nao "
            f"gera texto. Tipos de texto: {sorted(mapa)}"
        )

    return classe(
        base_url=connection.base_url,
        api_key=decifrar_chave(connection),
        timeout=timeout if timeout is not None else 300.0,
    )


def get_image_provider(connection, *, timeout: float | None = None) -> ImageClient:
    """Instancia o adaptador de imagem de uma conexao.

    Espelha `get_provider`, e pelo mesmo motivo: este e o unico lugar que sabe
    qual classe atende qual tipo de conexao.
    """
    from apps.inference.models import InferenceConnection
    from apps.inference.providers.openai_compatible import OpenAICompatibleImageClient
    from apps.inference.security import decifrar_chave

    if connection.kind != InferenceConnection.Kind.IMAGE:
        raise ProviderPermanentError(
            f"A conexao {connection.name!r} e do tipo {connection.kind!r}, que nao "
            f"gera imagem. Cadastre uma conexao do tipo 'image'."
        )

    return OpenAICompatibleImageClient(
        base_url=connection.base_url,
        api_key=decifrar_chave(connection),
        timeout=timeout if timeout is not None else 300.0,
    )
