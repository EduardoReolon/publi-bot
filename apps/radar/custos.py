"""Livro-caixa das chamadas externas e o teto de custo.

Duas travas, e as duas sao conferidas ANTES de a chamada sair:

* o teto do tenant (`ConfiguracaoDoRadar.teto_mensal_usd`), escolhido na tela;
* o teto maximo da instalacao (`RADAR_TETO_MAXIMO_USD` no `.env`), que limita
  o do tenant. E o que vale quando alguem digita 2000 no lugar de 20.

A conferencia usa a ESTIMATIVA do custo; o registro usa o custo que o provedor
informou na resposta. A estimativa so precisa ser conservadora.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar

logger = logging.getLogger("publibot.radar")

# Estimativas por chamada, em US$, para a conferencia do teto. Precos da
# DataForSEO em 2026; o custo gravado vem da resposta.
ESTIMATIVAS = {
    ("dataforseo", "serp"): Decimal("0.002"),
    ("dataforseo", "volume"): Decimal("0.09"),
    # Fila padrao: cerca de um terco do preco ao vivo.
    ("dataforseo", "serp_fila"): Decimal("0.0006"),
    ("dataforseo", "volume_fila"): Decimal("0.06"),
    # Sem tabela conferida para estes dois: estimativas CONSERVADORAS. O que
    # vale e o custo que a resposta informa, e e ele que vai para o registro.
    ("dataforseo", "labs"): Decimal("0.05"),
    ("dataforseo", "avaliacoes_fila"): Decimal("0.05"),
}


class TetoAtingido(RuntimeError):
    """A proxima chamada paga passaria do teto do mes."""


def gasto_do_mes() -> Decimal:
    inicio = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = ChamadaExterna.objects.filter(criado_em__gte=inicio).aggregate(t=Sum("custo_usd"))["t"]
    return total or Decimal("0")


def teto_efetivo(config: ConfiguracaoDoRadar | None = None) -> Decimal:
    config = config or ConfiguracaoDoRadar.carregar()
    maximo = Decimal(str(getattr(settings, "RADAR_TETO_MAXIMO_USD", 20)))
    return min(config.teto_mensal_usd, maximo)


def conferir_teto(estimativa: Decimal) -> None:
    """Levanta `TetoAtingido` se a chamada estourar o teto do mes."""
    teto = teto_efetivo()
    gasto = gasto_do_mes()
    if gasto + estimativa > teto:
        raise TetoAtingido(
            f"o teto mensal de US$ {teto} seria ultrapassado (gasto no mes: "
            f"US$ {gasto:.4f}, esta chamada: ~US$ {estimativa})."
        )


def registrar(
    *,
    provedor: str,
    endpoint: str,
    finalidade: str,
    consulta: str = "",
    custo: Decimal | float | str = 0,
    itens: int = 0,
    sucesso: bool = True,
    erro: str = "",
) -> ChamadaExterna:
    return ChamadaExterna.objects.create(
        provedor=provedor,
        endpoint=endpoint[:200],
        finalidade=finalidade,
        consulta=(consulta or "")[:500],
        custo_usd=Decimal(str(custo or 0)),
        itens=itens,
        sucesso=sucesso,
        erro=(erro or "")[:2000],
    )


def resumo_do_mes() -> dict:
    """Gasto e uso do mes corrente, por provedor e finalidade, para o painel."""
    inicio = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    linhas = (
        ChamadaExterna.objects.filter(criado_em__gte=inicio)
        .values("provedor", "finalidade")
        .annotate(total=Sum("custo_usd"))
        .order_by("provedor", "finalidade")
    )
    from django.db.models import Count

    contagens = {
        (c["provedor"], c["finalidade"]): c["n"]
        for c in ChamadaExterna.objects.filter(criado_em__gte=inicio)
        .values("provedor", "finalidade")
        .annotate(n=Count("id"))
    }
    return {
        "gasto": gasto_do_mes(),
        "teto": teto_efetivo(),
        "linhas": [
            {**linha, "chamadas": contagens.get((linha["provedor"], linha["finalidade"]), 0)}
            for linha in linhas
        ],
    }
