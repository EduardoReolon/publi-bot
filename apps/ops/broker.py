"""Leitura do estado da fila, para diagnostico.

Existe por causa de uma falha silenciosa: sem nenhum worker do Celery rodando,
a mensagem e publicada com sucesso e fica na fila para sempre. Nao ha erro em
lugar nenhum — o despacho funcionou. Quem esta olhando a tela de espera ve um
"criando ambiente..." que nunca termina, e o console do servidor so mostra o
proprio navegador consultando o status.

O que distingue os casos e a profundidade da fila:

    fila > 0 e o trabalho nao anda  ->  ninguem esta consumindo
    fila == 0 e o trabalho nao anda ->  um worker pegou e esta lento ou morreu

Nenhuma das duas conclusoes sai de `app.control.ping()`. O ping viaja por
fanout (pidbox), e o transporte `sqla` — o broker de desenvolvimento sem Redis,
ADR-0013 — nao o entrega: com um worker de pe e consumindo, `ping()` devolve
lista vazia. Medido neste projeto. `_size()` faz parte da base dos transportes
virtuais e responde igual no Redis e no `sqla`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("publibot.ops")

FILA_PADRAO = "celery"


def mensagens_pendentes(fila: str = FILA_PADRAO) -> int | None:
    """Quantas mensagens esperam um worker.

    Devolve `None` quando a resposta nao e confiavel — broker fora do ar ou
    transporte sem `_size()`. `None` significa "nao sei", e quem chama nao deve
    trata-lo como zero: a diferenca entre "a fila esta vazia" e "nao consegui
    ler a fila" e justamente o que este modulo existe para preservar.
    """
    from core.celery import app

    try:
        with app.connection_for_read() as conexao:
            canal = conexao.default_channel
            medir = getattr(canal, "_size", None)
            if medir is None:
                return None
            return int(medir(fila))
    except Exception:
        # Diagnostico nunca derruba quem o consulta: esta funcao e chamada de
        # dentro de uma view que ja esta lidando com um problema.
        logger.warning("Nao foi possivel ler a profundidade da fila %r.", fila, exc_info=True)
        return None
