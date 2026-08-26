"""Configuracao dos testes de contrato.

Isolados do resto da suite porque rodam com outro settings: o no de referencia
e um site comum, sem multi-tenancy. O `tests/conftest.py` importa os models de
tenancy no nivel do modulo, o que quebraria aqui.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def cache_limpo():
    """Cada teste comeca sem nonces gravados.

    Sem isto, o nonce de um teste anterior faria o seguinte ser recusado — e a
    falha pareceria aleatoria conforme a ordem de execucao.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
