"""Limite de requisicoes.

Sem limite, `/seo-context/` vira amplificador: devolve a home inteira e a lista
de publicacoes, gerada do zero a cada chamada. E tentativas de adivinhar a
chave saem de graca.
"""

from __future__ import annotations

from django.core.cache import cache
from django.http import JsonResponse

LIMITE_POR_IP_POR_MINUTO = 60
LIMITE_DE_PUBLICACAO_POR_MINUTO = 10
NEGACOES_ATE_BLOQUEIO = 10
BLOQUEIO_SEGUNDOS = 900


def excedeu(chave: str, limite: int, janela: int = 60) -> bool:
    contador_atual = cache.get_or_set(chave, 0, janela)
    if contador_atual >= limite:
        return True
    try:
        cache.incr(chave)
    except ValueError:
        cache.set(chave, 1, janela)
    return False


def resposta_de_limite(segundos: int = 60) -> JsonResponse:
    resposta = JsonResponse(
        {
            "error": {
                "code": "rate_limited",
                "message": "Limite de requisicoes excedido.",
                "details": {"retry_after": segundos},
            }
        },
        status=429,
    )
    # O cliente respeita este cabecalho em vez de aplicar o proprio backoff:
    # o servidor sabe melhor quando volta a aceitar.
    resposta["Retry-After"] = str(segundos)
    return resposta


def registrar_negacao(ip: str) -> None:
    """Conta 401 seguidos e bloqueia o IP ao atingir o limite."""
    chave = f"publibot:negacoes:{ip}"
    total = cache.get(chave, 0) + 1
    cache.set(chave, total, BLOQUEIO_SEGUNDOS)
    if total >= NEGACOES_ATE_BLOQUEIO:
        cache.set(f"publibot:bloqueado:{ip}", "1", BLOQUEIO_SEGUNDOS)


def esta_bloqueado(ip: str) -> bool:
    return cache.get(f"publibot:bloqueado:{ip}") is not None
