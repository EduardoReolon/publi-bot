"""Reordenacao dos candidatos da busca por um cross-encoder.

O embedding compara a consulta com cada trecho SEPARADAMENTE: cada um vira um
vetor, e a distancia entre vetores e uma aproximacao. O cross-encoder le a
consulta e o trecho JUNTOS e da uma nota de relevancia — mais caro, e bem mais
preciso. Por isso ele so roda sobre os poucos candidatos que a busca ja
separou, e nunca sobre o acervo inteiro.

Desligado por padrao (`RAG_RERANKER_MODEL` vazio): sao ~1 GB a mais de modelo
na memoria. O padrao sugerido e multilingue, porque o acervo mistura fontes em
ingles com pautas em portugues.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache

from django.conf import settings


class Reordenador(ABC):
    model_name: str

    @abstractmethod
    def notas(self, consulta: str, textos: list[str]) -> list[float]:
        """Uma nota por texto, maior = mais relevante. Na ordem recebida."""


class FastEmbedReordenador(Reordenador):
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._modelo = None
        self._trava = threading.Lock()

    def _carregar(self):
        if self._modelo is None:
            with self._trava:
                if self._modelo is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    self._modelo = TextCrossEncoder(
                        self.model_name,
                        cache_dir=settings.EMBEDDING_CACHE_DIR,
                        local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
                    )
        return self._modelo

    def notas(self, consulta: str, textos: list[str]) -> list[float]:
        if not textos:
            return []
        return [float(n) for n in self._carregar().rerank(consulta, textos)]


class ReordenadorFalso(Reordenador):
    """Para testes: nota = quantas palavras da consulta aparecem no texto."""

    model_name = "reordenador-falso"

    def notas(self, consulta: str, textos: list[str]) -> list[float]:
        palavras = {p for p in consulta.lower().split() if len(p) > 2}
        return [float(sum(1 for p in palavras if p in t.lower())) for t in textos]


@lru_cache(maxsize=1)
def get_reordenador() -> Reordenador | None:
    """O reordenador configurado, ou None quando desligado."""
    modelo = getattr(settings, "RAG_RERANKER_MODEL", "")
    if not modelo:
        return None
    if modelo == "falso":
        return ReordenadorFalso()
    return FastEmbedReordenador(modelo)
