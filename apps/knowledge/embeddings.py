"""Cliente de embeddings.

A interface expoe apenas `embed_query()` e `embed_passage()`. **Nao existe um
`embed()` cru, de proposito.**

O modelo em uso, `intfloat/multilingual-e5-large`, exige que o texto seja
prefixado com `query: ` ou `passage: ` conforme o papel. Esquecer o prefixo NAO
levanta erro: apenas derruba a revocacao, em silencio, de forma que so
apareceria como "o RAG nao acha nada bom" semanas depois. Tornar impossivel
chamar sem prefixo e mais barato que confiar em disciplina.

O modelo tambem trunca em 512 tokens sem avisar, entao `contar_tokens()` usa o
tokenizador real e nao uma estimativa por caracteres.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np
from django.conf import settings


class EmbeddingClient(ABC):
    """Contrato de embeddings.

    Query e passagem sao metodos separados porque, neste modelo, sao operacoes
    genuinamente diferentes — nao e uma formalidade.
    """

    model_name: str
    dimensions: int

    @abstractmethod
    def embed_query(self, texto: str) -> list[float]:
        """Vetoriza uma CONSULTA (o que o usuario procura)."""

    @abstractmethod
    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        """Vetoriza PASSAGENS (o que esta no corpus)."""

    @abstractmethod
    def contar_tokens(self, texto: str) -> int:
        """Conta tokens com o tokenizador real do modelo."""


def _normalizar(vetor) -> list[float]:
    """Normaliza em L2.

    Verificado: este modelo devolve vetores com norma em torno de 29, nao 1.
    Normalizar na gravacao mantem a distancia de cosseno consistente e permite
    comparar valores entre execucoes.
    """
    arr = np.asarray(vetor, dtype=np.float32)
    norma = np.linalg.norm(arr)
    if norma == 0:
        return arr.tolist()
    return (arr / norma).tolist()


class FastEmbedClient(EmbeddingClient):
    """Implementacao com fastembed (ONNX em CPU, sem torch).

    Roda na nuvem e nao na GPU: indexar um documento pode esperar a GPU acordar,
    mas CONSULTAR nao pode. Se o embedding vivesse so na maquina local, buscar
    no indice com ela desligada seria impossivel (ADR-0005).
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.dimensions = settings.EMBEDDING_DIM
        self._modelo = None
        self._tokenizer = None
        self._trava = threading.Lock()

    def _carregar(self):
        # Carga preguicosa e com trava: o modelo ocupa cerca de 2 GB e leva
        # alguns segundos para subir. Carregar no import faria todo comando de
        # gerenciamento pagar esse custo.
        if self._modelo is None:
            with self._trava:
                if self._modelo is None:
                    from fastembed import TextEmbedding

                    self._modelo = TextEmbedding(
                        self.model_name,
                        cache_dir=settings.EMBEDDING_CACHE_DIR,
                        local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
                    )
        return self._modelo

    def embed_query(self, texto: str) -> list[float]:
        modelo = self._carregar()
        return _normalizar(next(iter(modelo.query_embed([texto]))))

    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        if not textos:
            return []
        modelo = self._carregar()
        # `passage: ` e obrigatorio para este modelo; o metodo `embed` do
        # fastembed nao o adiciona sozinho.
        prefixados = [f"passage: {t}" for t in textos]
        return [_normalizar(v) for v in modelo.embed(prefixados)]

    def contar_tokens(self, texto: str) -> int:
        if self._tokenizer is None:
            from tokenizers import Tokenizer

            caminho = self._caminho_do_tokenizer()
            self._tokenizer = Tokenizer.from_file(str(caminho))
        # Conta sobre o texto ja prefixado: e isso que o modelo recebe, e o
        # prefixo tambem ocupa tokens do orcamento de 512.
        return len(self._tokenizer.encode(f"passage: {texto}").ids)

    def _caminho_do_tokenizer(self):
        from pathlib import Path

        self._carregar()
        base = Path(settings.EMBEDDING_CACHE_DIR)
        candidatos = list(base.glob("**/tokenizer.json"))
        if not candidatos:
            raise FileNotFoundError(
                f"tokenizer.json nao encontrado em {base}. O modelo foi baixado?"
            )
        return candidatos[0]


class FakeEmbeddingClient(EmbeddingClient):
    """Cliente deterministico para testes.

    Nao e um atalho: e o que permite testar a logica de recuperacao, os
    limiares e o fluxo de curadoria sem carregar 2 GB de modelo a cada execucao
    da suite. Os vetores sao derivados do hash do texto, entao o mesmo texto
    sempre produz o mesmo vetor e textos diferentes produzem vetores
    diferentes — que e tudo o que esses testes precisam.
    """

    def __init__(self, model_name: str = "fake-determinista", dimensions: int | None = None):
        self.model_name = model_name
        self.dimensions = dimensions or settings.EMBEDDING_DIM

    def _vetor(self, texto: str) -> list[float]:
        import hashlib

        semente = int.from_bytes(hashlib.sha256(texto.encode()).digest()[:8], "big")
        rng = np.random.default_rng(semente)
        return _normalizar(rng.standard_normal(self.dimensions))

    def embed_query(self, texto: str) -> list[float]:
        return self._vetor(texto)

    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    def contar_tokens(self, texto: str) -> int:
        # Aproximacao suficiente para teste; o cliente real usa o tokenizador.
        return max(1, len(texto) // 4)


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """Cliente configurado, reaproveitado entre chamadas.

    O cache existe porque cada instanciacao do modelo real custa segundos e
    cerca de 2 GB de memoria.
    """
    from django.utils.module_loading import import_string

    classe = import_string(settings.EMBEDDING_CLIENT)
    return classe()
