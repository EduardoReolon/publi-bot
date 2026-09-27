"""Versoes de um artigo publicado.

Atualizar um artigo que ja esta no ar cria uma linha NOVA de `Article`, que
aponta para a anterior (`previous_version`) e herda o `remote_id`. O que foi ao
ar continua guardado como foi — e o historico editorial, e o que permite
responder "o que mudou e por que".

A versao nova e uma copia completa (secoes, citacoes, FAQ, capas), entra em
revisao, e passa pelas mesmas travas de qualquer artigo. Publicada, vai ao site
pela rota de atualizacao (`PUT /publications/{remote_id}/`), para a MESMA
pagina; as anteriores ficam como substituidas.
"""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from apps.content.models import Article, ArticleRevision


class VersaoRecusada(ValueError):
    """Nao da para criar versao nova deste artigo."""


# Campos que NAO passam para a versao nova: identidade, publicacao e revisao
# sao da versao, nao do artigo.
_NAO_COPIAR = {
    "id",
    "status",
    "previous_version",
    "version_number",
    "update_notes",
    "reviewed_by",
    "reviewed_at",
    "review_seconds",
    "human_edit_ratio",
    "scheduled_for",
    "published_at",
    "idempotency_key",
    "publish_attempts",
    "last_publish_error",
    "last_error_code",
    "next_retry_at",
    "created_at",
    "updated_at",
}


def _copiar_linhas(consulta, artigo_novo: Article) -> None:
    for linha in consulta:
        linha.pk = None
        linha.id = uuid.uuid4()
        linha._state.adding = True
        linha.article = artigo_novo
        linha.save()


@transaction.atomic
def criar_nova_versao(artigo: Article, *, notas: str = "", por=None) -> Article:
    """Cria a versao seguinte de um artigo publicado. Devolve a nova.

    Se ja existe uma versao em aberto (em revisao, aprovada, com falha), devolve
    ELA, com as notas acrescentadas: duas versoes paralelas do mesmo artigo
    fariam uma apagar o trabalho da outra ao publicar.
    """
    if artigo.status != Article.Status.PUBLISHED or not artigo.remote_id:
        raise VersaoRecusada(
            "so artigo publicado (com o id que o site devolveu) pode ganhar versao nova."
        )
    aberta = artigo.versao_em_aberto
    if aberta is not None:
        if notas and notas not in aberta.update_notes:
            aberta.update_notes = (aberta.update_notes + "\n\n" + notas).strip()
            aberta.save(update_fields=["update_notes"])
        return aberta

    campos = {
        f.attname: getattr(artigo, f.attname)
        for f in Article._meta.concrete_fields
        if f.name not in _NAO_COPIAR
    }
    nova = Article.objects.create(
        **campos,
        status=Article.Status.PENDING_REVIEW,
        previous_version=artigo,
        version_number=artigo.version_number + 1,
        update_notes=notas,
    )
    _copiar_linhas(artigo.sections.all(), nova)
    _copiar_linhas(artigo.citations.all(), nova)
    _copiar_linhas(artigo.faq.all(), nova)
    _copiar_linhas(artigo.images.all(), nova)
    # O ponto de partida da revisao: o texto que esta no ar. E contra ele que a
    # proporcao editada desta versao sera medida.
    ArticleRevision.objects.create(
        article=nova,
        version=1,
        body_markdown=artigo.body_markdown,
        source=ArticleRevision.Source.HUMAN,
        editor=por,
    )
    return nova


def marcar_anteriores_como_substituidas(artigo: Article) -> int:
    """Depois de a versao nova ir ao ar: todas as anteriores saem de 'publicado'."""
    anteriores = []
    atual = artigo.previous_version
    while atual is not None:
        anteriores.append(atual.pk)
        atual = atual.previous_version
    return Article.objects.filter(pk__in=anteriores, status=Article.Status.PUBLISHED).update(
        status=Article.Status.SUPERSEDED, updated_at=timezone.now()
    )


def notas_da_sugestao(sugestao) -> str:
    """O texto de 'o que atualizar' a partir de uma sugestao do radar."""
    evidencia = sugestao.evidencia or {}
    if sugestao.tipo == "acrescentar":
        linhas = [f"O publico passou a procurar: {evidencia.get('tema', '')}."]
        linhas += [
            f"- {s['texto']}" + (f" ({s['volume']}/mes)" if s.get("volume") else "")
            for s in evidencia.get("sinais", [])
        ]
        linhas.append("Responda estas perguntas numa secao nova ou no FAQ.")
    elif sugestao.tipo == "quase_la":
        linhas = ["Quase na primeira pagina para estas buscas (Search Console):"]
        linhas += [
            f"- {c['consulta']} (posicao {c['posicao']}, {c['impressoes']} impressoes)"
            for c in evidencia.get("consultas", [])
        ]
        linhas.append("Deixe a resposta a elas mais completa e mais visivel.")
    else:
        linhas = [
            f"A posicao media caiu de {evidencia.get('posicao_anterior')} para "
            f"{evidencia.get('posicao')}. Confira se fontes e dados ainda valem."
        ]
    return "\n".join(linhas)
