"""Para o bloco do modo investigativo (na pauta e na revisao do artigo)."""

from __future__ import annotations

from django import template

register = template.Library()


@register.simple_tag
def frentes_da_pauta(pauta) -> dict:
    """As frentes da pauta, cada uma com as fontes que esperam decisao (prontas
    para o cartao de curadoria) e as contagens; e o que o cartao precisa."""
    from apps.knowledge.fontes_web import natureza_sugerida
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.views import contexto_da_curadoria

    frentes = [
        {
            **f,
            "ancora": f"frente-{n}",
            "pendentes": [],
            "esperando_arquivo": [],
            "falharam": [],
            "aprovadas": [],
            "recusadas": 0,
        }
        for n, f in enumerate((getattr(pauta, "debate", None) or {}).get("frentes") or [], 1)
    ]
    por_nome = {f["nome"]: f for f in frentes}
    total_esperando = 0
    if frentes:
        for candidato in (
            CandidatoDeFonte.objects.filter(pauta=pauta)
            .select_related("documento")
            .order_by("encontrado_em")
        ):
            frente = por_nome.get((candidato.metricas or {}).get("frente", ""))
            if frente is None:
                continue
            if candidato.situacao == CandidatoDeFonte.Situacao.PENDENTE:
                candidato.natureza_padrao = natureza_sugerida(candidato)
                frente["pendentes"].append(candidato)
                total_esperando += 1
            elif candidato.situacao == CandidatoDeFonte.Situacao.RECUSADO:
                frente["recusadas"] += 1
            elif candidato.situacao in (
                CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO,
                CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
            ):
                # Aprovada, mas espera o audio ou o PDF: o envio fica aqui mesmo.
                frente["esperando_arquivo"].append(candidato)
                total_esperando += 1
            elif candidato.situacao == CandidatoDeFonte.Situacao.FALHOU:
                frente["falharam"].append(candidato)
            else:
                frente["aprovadas"].append(candidato)
    sugeridas = sum(
        1 for f in frentes for c in f["pendentes"] if (c.metricas or {}).get("sugestao_da_ia")
    )
    return {
        "frentes": frentes,
        "esperando": total_esperando,
        "sugeridas": sugeridas,
        **contexto_da_curadoria(),
    }
