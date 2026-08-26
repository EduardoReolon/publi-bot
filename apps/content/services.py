"""Producao de artigos: tese, redacao e as travas de qualidade."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.content.models import (
    Article,
    ArticleCitation,
    ArticleRevision,
    PromptRun,
    PromptTemplate,
    PromptVersion,
)
from apps.content.rendering import (
    Fonte,
    contar_palavras,
    markdown_para_html,
    proporcao_editada,
    substituir_marcadores,
    validar_saida_do_modelo,
)

logger = logging.getLogger("publibot.content")


# Esquema da saida do filtro de consenso. Estruturado de proposito: pedir texto
# livre obrigaria a interpretar a resposta com expressao regular, e a
# divergencia entre fontes — que e o que mais importa aqui — se perderia.
ESQUEMA_DA_TESE = {
    "type": "object",
    "properties": {
        "tese": {"type": "string"},
        "concordancia": {"type": "string", "enum": ["alta", "parcial", "conflito"]},
        "pontos_divergentes": {"type": "array", "items": {"type": "string"}},
        "chunks_usados": {"type": "array", "items": {"type": "string"}},
        "chunks_descartados": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "motivo": {"type": "string"}},
                "required": ["id", "motivo"],
            },
        },
    },
    "required": ["tese", "concordancia", "pontos_divergentes"],
}

MAPA_DE_CONCORDANCIA = {
    "alta": Article.Consensus.HIGH,
    "parcial": Article.Consensus.PARTIAL,
    "conflito": Article.Consensus.CONFLICT,
}


class SemFontesSuficientes(RuntimeError):
    """Nenhum trecho passou o limiar.

    Levantado ANTES de qualquer chamada ao modelo: gerar um artigo sem
    fundamentacao gastaria inferencia para produzir exatamente o que o produto
    existe para evitar.
    """


def escolher_versao_de_prompt(key: str, *, site_overrides: dict | None = None) -> PromptVersion:
    """Resolve qual versao usar, na ordem Site -> PromptVersion -> settings.

    O override por site e o que permite, por exemplo, um site em italiano usar
    um modelo melhor em italiano apenas para a redacao, sem duplicar prompt
    nenhum.
    """
    ativas = list(
        PromptVersion.objects.filter(template__key=key, is_active=True).select_related("template")
    )
    if not ativas:
        raise LookupError(f"nenhuma versao ativa para o prompt {key!r}")

    if len(ativas) == 1:
        escolhida = ativas[0]
    else:
        # Sorteio ponderado entre as variantes ativas: e assim que o teste A/B
        # distribui trafego.
        pesos = [max(v.traffic_weight, 0) for v in ativas]
        escolhida = random.choices(ativas, weights=pesos or None, k=1)[0]  # noqa: S311

    if site_overrides:
        override = site_overrides.get(key)
        if override:
            # Nao grava: o override vale para esta execucao. Persistir mudaria a
            # versao para todos os sites.
            escolhida.model_name = override

    return escolhida


@dataclass(frozen=True)
class ResultadoDaTese:
    tese: str
    concordancia: str
    pontos_divergentes: list[str]
    bruto: dict


def montar_contexto_das_fontes(trechos) -> str:
    """Monta o bloco de fontes para o prompt, com delimitadores explicitos.

    Conteudo nao confiavel (texto vindo de um PDF de terceiro) fica SEMPRE
    dentro de delimitadores, e o prompt de sistema diz que o delimitado e dado a
    analisar, nunca instrucao a obedecer. Um PDF pode conter texto invisivel —
    fonte branca sobre branco, tamanho zero — que o extrator le normalmente e o
    curador nao ve.
    """
    partes = []
    for i, t in enumerate(trechos, start=1):
        chunk = t.chunk if hasattr(t, "chunk") else t
        partes.append(
            f'<fonte numero="{i}" autores="{chunk.source_authors}" '
            f'ano="{chunk.source_year or ""}">\n{chunk.content}\n</fonte>'
        )
    return "\n\n".join(partes)


def interpretar_tese(texto: str) -> ResultadoDaTese:
    """Le a saida estruturada do filtro de consenso."""
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"o filtro de consenso nao devolveu JSON valido: {texto[:200]}") from exc

    concordancia = str(dados.get("concordancia", "")).lower()
    if concordancia not in MAPA_DE_CONCORDANCIA:
        raise ValueError(
            f"concordancia {concordancia!r} invalida; esperado um de {sorted(MAPA_DE_CONCORDANCIA)}"
        )

    return ResultadoDaTese(
        tese=dados.get("tese", ""),
        concordancia=concordancia,
        pontos_divergentes=list(dados.get("pontos_divergentes") or []),
        bruto=dados,
    )


@transaction.atomic
def registrar_citacoes(article: Article, trechos) -> None:
    """Grava de que trechos o artigo saiu, e qual foi a fonte primaria.

    A fonte primaria e a de maior autoridade entre as recuperadas — e a unica
    cuja URL vira link de saida.
    """
    article.citations.all().delete()

    if not trechos:
        return

    ordenados = sorted(
        trechos,
        key=lambda t: (t.chunk if hasattr(t, "chunk") else t).source_authority,
        reverse=True,
    )
    primaria = ordenados[0]
    chunk_primario = primaria.chunk if hasattr(primaria, "chunk") else primaria

    for t in trechos:
        chunk = t.chunk if hasattr(t, "chunk") else t
        ArticleCitation.objects.create(
            article=article,
            super_chunk=chunk,
            rank=getattr(t, "posicao", 1),
            distance=getattr(t, "distancia", 0.0),
            used_as_primary=chunk.pk == chunk_primario.pk,
            source_title=chunk.source_title,
            source_url=chunk.source_url,
        )

    article.primary_source = chunk_primario.document
    article.outbound_link_url = chunk_primario.source_url
    article.anchor_text = _texto_ancora(chunk_primario)
    article.save(update_fields=["primary_source", "outbound_link_url", "anchor_text"])


def _texto_ancora(chunk) -> str:
    autores = chunk.source_authors or chunk.source_title
    ano = chunk.source_year
    return f"{autores}, {ano}" if ano else autores


def fontes_para_substituicao(article: Article) -> dict[int, Fonte]:
    """Monta o mapa de marcador para URL, a partir das citacoes gravadas.

    As URLs vem daqui — de documentos confirmados por humano — e nunca de algo
    que o modelo tenha escrito.
    """
    mapa: dict[int, Fonte] = {}
    for citacao in article.citations.order_by("rank"):
        if citacao.source_url:
            mapa[citacao.rank] = Fonte(
                url=citacao.source_url,
                anchor=_texto_ancora(citacao.super_chunk)
                if citacao.super_chunk
                else citacao.source_title,
            )
    return mapa


@transaction.atomic
def aplicar_rascunho(
    article: Article, markdown_bruto: str, *, prompt_run: PromptRun | None = None
) -> Article:
    """Valida a saida do modelo, insere os links e converte para HTML."""
    validar_saida_do_modelo(markdown_bruto)

    markdown_final = substituir_marcadores(markdown_bruto, fontes_para_substituicao(article))

    dominios = _dominios_das_citacoes(article)
    html = markdown_para_html(markdown_final)
    if dominios:
        from apps.content.rendering import sanitizar_html

        html = sanitizar_html(html, dominios_permitidos=dominios)

    proxima_versao = (article.revisions.count() or 0) + 1
    ArticleRevision.objects.create(
        article=article,
        version=proxima_versao,
        body_markdown=markdown_final,
        source=ArticleRevision.Source.LLM,
        prompt_run=prompt_run,
    )

    article.body_markdown = markdown_final
    article.body_html = html
    article.word_count = contar_palavras(markdown_final)
    article.status = Article.Status.PENDING_REVIEW
    article.save()
    return article


def _dominios_das_citacoes(article: Article) -> set[str]:
    from urllib.parse import urlparse

    dominios = set()
    for citacao in article.citations.all():
        if citacao.source_url:
            anfitriao = (urlparse(citacao.source_url).hostname or "").lower()
            dominios.add(anfitriao.removeprefix("www."))
    return {d for d in dominios if d}


@transaction.atomic
def aplicar_edicao_humana(article: Article, markdown_editado: str, *, editor) -> Article:
    """Grava a versao revisada e mede quanto o humano de fato mudou."""
    ultima_do_modelo = (
        article.revisions.filter(source=ArticleRevision.Source.LLM).order_by("-version").first()
    )
    base = ultima_do_modelo.body_markdown if ultima_do_modelo else ""

    proxima_versao = (article.revisions.count() or 0) + 1
    ArticleRevision.objects.create(
        article=article,
        version=proxima_versao,
        body_markdown=markdown_editado,
        source=ArticleRevision.Source.HUMAN,
        editor=editor,
    )

    article.body_markdown = markdown_editado
    article.body_html = markdown_para_html(markdown_editado)
    article.word_count = contar_palavras(markdown_editado)
    article.human_edit_ratio = proporcao_editada(base, markdown_editado)
    article.save()
    return article


class RevisaoInsuficiente(PermissionError):
    """Falta uma condicao para aprovar o artigo."""


def aprovar_e_agendar(article: Article, *, revisor, quando, exige_revisor_tecnico: bool = False):
    """Aprova o artigo e o coloca na fila de publicacao.

    As travas aqui nao sao burocracia. Cada uma corresponde a uma forma
    conhecida de o conteudo sair errado de um jeito que so aparece depois de
    publicado.
    """
    if exige_revisor_tecnico and not getattr(revisor, "is_technical_reviewer", False):
        raise RevisaoInsuficiente("este site exige revisor com credencial tecnica registrada.")

    if article.exige_confirmacao_de_divergencia and not article.thesis_json.get(
        "divergencia_confirmada"
    ):
        raise RevisaoInsuficiente(
            "as fontes divergem entre si. O revisor precisa confirmar "
            "explicitamente como o texto trata a divergencia antes de aprovar."
        )

    if not article.author_name:
        raise RevisaoInsuficiente("o artigo precisa de autor identificado antes de ser publicado.")

    article.status = Article.Status.APPROVED_SCHEDULED
    article.reviewed_by = revisor
    article.reviewed_at = timezone.now()
    article.scheduled_for = quando
    if not article.slug:
        article.slug = slugify(article.title)[:300]
    article.save()
    return article


def garantir_prompts_padrao() -> None:
    """Cria os prompts iniciais, se ainda nao existirem.

    Ficam no banco, e nao em constantes no codigo, para que ajustar o
    comportamento do modelo nao exija deploy.
    """
    from apps.content.prompts_iniciais import PROMPTS_INICIAIS

    for chave, dados in PROMPTS_INICIAIS.items():
        template, _ = PromptTemplate.objects.get_or_create(
            key=chave, defaults={"description": dados["descricao"]}
        )
        if not template.versions.exists():
            PromptVersion.objects.create(
                template=template,
                version=1,
                variant="A",
                system_prompt=dados["sistema"],
                user_prompt_template=dados["usuario"],
                variables=dados.get("variaveis", []),
                model_name=dados.get("modelo", ""),
                temperature=dados.get("temperatura", 0.2),
                is_active=True,
            )
