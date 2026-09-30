"""Depois de publicar: o que o artigo novo muda no radar, nas pautas e nos links.

A nota de um tema so era refeita quando ele ganhava sinal novo, e a pauta
guardava a canibalizacao do dia em que nasceu. Um artigo publicado depois
passava despercebido: o tema parado continuava com nota de assunto inedito, e
a pauta aberta, sem aviso de que ja ha texto sobre aquilo. Aqui, a cada
publicacao:

* os temas vivos do radar tem a nota refeita (a canibalizacao muda);
* as pautas ainda sem artigo sao comparadas com o publicado e, se perto,
  ganham o aviso (nada e descartado: a pessoa decide);
* os links quebrados ainda sem artigo sao comparados com ele.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("publibot.radar")

# Canibalizacao (0 a 1) a partir da qual a pauta aberta ganha o aviso.
AVISO_NA_PAUTA = 0.5


def rever_temas() -> int:
    """Refaz a nota dos temas vivos com o que ja foi escrito hoje."""
    from apps.radar.agrupamento import pontuar, vetor_do_negocio, vetores_do_que_ja_foi_escrito
    from apps.radar.coleta import _que_convertem
    from apps.radar.models import GrupoDeDemanda

    grupos = list(GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.NOVO))
    if not grupos:
        return 0
    negocio = vetor_do_negocio()
    ja_escrito = vetores_do_que_ja_foi_escrito()
    convertem = _que_convertem()
    for grupo in grupos:
        pontuar(grupo, vetor_do_negocio=negocio, ja_escrito=ja_escrito, que_convertem=convertem)
    return len(grupos)


def rever_pautas(artigo) -> int:
    """As pautas abertas perto do artigo publicado ganham o aviso. Devolve quantas."""
    from apps.content.models import Topic
    from apps.radar.agrupamento import _distancia, _vetor, canibalizacao

    abertas = (
        Topic.objects.exclude(status__in=[Topic.Status.REJECTED, Topic.Status.USED])
        .filter(articles__isnull=True)
        .exclude(pk=artigo.topic_id)
        .distinct()
    )
    vetor = _vetor(artigo.title)
    avisadas = 0
    for pauta in abertas:
        risco = round(canibalizacao(_distancia(_vetor(pauta.title), vetor)), 3)
        if risco < AVISO_NA_PAUTA or (
            pauta.artigo_parecido_id and risco <= pauta.cannibalization_score
        ):
            continue
        pauta.artigo_parecido = artigo
        pauta.cannibalization_score = max(risco, pauta.cannibalization_score)
        pauta.save(update_fields=["artigo_parecido", "cannibalization_score"])
        avisadas += 1
    return avisadas


def depois_de_publicar(artigo) -> None:
    """Cada parte isolada: falhar numa nao impede as outras."""
    from apps.radar.links_quebrados import comparar_com_o_publicado

    for nome, passo in (
        ("temas", rever_temas),
        ("pautas", lambda: rever_pautas(artigo)),
        ("links quebrados", lambda: comparar_com_o_publicado(artigo)),
    ):
        try:
            passo()
        except Exception:
            logger.exception("Depois de publicar %s: falha ao rever %s.", artigo.pk, nome)
