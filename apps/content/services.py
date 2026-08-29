"""Producao de artigos: tese, redacao e as travas de qualidade."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from urllib.parse import urlparse

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


class SemEmbasamentoCentral(SemFontesSuficientes):
    """Ha fontes, mas nenhuma sustenta a ideia central da publicacao.

    E um caso diferente de "nao achei nada": a busca trouxe material, ele so
    nao sustenta a afirmacao que o texto existe para fazer. Publicar assim
    produziria o pior resultado possivel — um texto que PARECE fundamentado,
    com links e tudo, cuja tese ninguem verificou.

    A geracao para aqui de proposito. Quem resolve e uma pessoa: acrescenta o
    artigo de referencia que falta ao acervo, ou ajusta a pauta para algo que o
    acervo sustente, e manda gerar de novo.

    Subclasse de `SemFontesSuficientes` porque a saida e a mesma — o trabalho
    para e alguem e avisado — e porque nenhum lugar que trata a falta de fontes
    deve deixar este caso passar por engano.
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
    validar_saida_do_modelo(markdown_bruto, max_marcadores=MAXIMO_DE_FONTES_NO_ARTIGO)

    markdown_final = substituir_marcadores(
        markdown_bruto,
        fontes_para_substituicao(article),
        ao_final=article.link_placement == Article.LinkPlacement.END,
    )

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


@transaction.atomic
def aplicar_rascunho_de_resposta(question, markdown_bruto: str, *, trechos, site=None):
    """Grava a resposta a uma pergunta, com as mesmas travas do artigo.

    Uma resposta publicada no site de um cliente tem o mesmo peso editorial que
    um artigo: sai do mesmo acervo, leva link para a mesma fonte primaria e
    passa pela mesma revisao humana. Reaproveitar as travas aqui — em vez de
    escrever um caminho mais curto — e o que impede a resposta de virar a porta
    dos fundos por onde um link alucinado chega ao ar.
    """
    from apps.content.models import Answer, AnswerCitation

    validar_saida_do_modelo(markdown_bruto)

    answer, _ = Answer.objects.get_or_create(question=question)

    # As citacoes vem antes da substituicao: sao elas que definem para onde
    # cada marcador aponta.
    answer.citations.all().delete()
    ordenados = sorted(trechos, key=lambda t: _chunk_de(t).source_authority, reverse=True)
    chunk_primario = _chunk_de(ordenados[0]) if ordenados else None

    for t in trechos:
        chunk = _chunk_de(t)
        AnswerCitation.objects.create(
            answer=answer,
            super_chunk=chunk,
            rank=getattr(t, "posicao", 1),
            distance=getattr(t, "distancia", 0.0),
            used_as_primary=chunk_primario is not None and chunk.pk == chunk_primario.pk,
            source_title=chunk.source_title,
            source_url=chunk.source_url,
        )

    fontes = {
        c.rank: Fonte(url=c.source_url, anchor=_texto_ancora(c.super_chunk))
        for c in answer.citations.order_by("rank")
        if c.source_url
    }
    markdown_final = substituir_marcadores(markdown_bruto, fontes)

    dominios = {
        (urlparse(c.source_url).hostname or "").lower().removeprefix("www.")
        for c in answer.citations.all()
        if c.source_url
    }
    html = markdown_para_html(markdown_final)
    if dominios := {d for d in dominios if d}:
        from apps.content.rendering import sanitizar_html

        html = sanitizar_html(html, dominios_permitidos=dominios)

    answer.body_markdown = markdown_final
    answer.body_html = html
    if chunk_primario is not None:
        answer.outbound_link_url = chunk_primario.source_url
        answer.anchor_text = _texto_ancora(chunk_primario)
    if not answer.author_name:
        answer.author_name = getattr(site, "default_author", "") or ""
        answer.author_credentials = getattr(site, "default_author_credentials", "") or ""
    answer.status = Answer.Status.PENDING_REVIEW
    answer.save()
    return answer


def _chunk_de(trecho):
    """Aceita tanto `TrechoRecuperado` quanto o proprio `SuperChunk`."""
    return trecho.chunk if hasattr(trecho, "chunk") else trecho


def aprovar_resposta_e_agendar(answer, *, revisor, quando, exige_revisor_tecnico: bool = False):
    """Mesma porta de aprovacao do artigo, aplicada a resposta."""
    if exige_revisor_tecnico and not getattr(revisor, "is_technical_reviewer", False):
        raise RevisaoInsuficiente("este site exige revisor com credencial tecnica registrada.")

    if not answer.author_name:
        raise RevisaoInsuficiente("a resposta precisa de autor identificado antes de publicar.")

    answer.status = answer.Status.APPROVED_SCHEDULED
    answer.reviewed_by = revisor
    answer.reviewed_at = timezone.now()
    answer.scheduled_for = quando
    answer.save()
    return answer


# ---------------------------------------------------------------------------
# Redacao em varias rodadas
# ---------------------------------------------------------------------------
# Um artigo inteiro numa chamada so exige janela grande e entrega texto medio: o
# modelo dilui a atencao entre quinze fontes e seis assuntos. Quebrado em
# rodadas, cada chamada e curta, cabe num modelo pequeno e pode ser refeita
# sozinha na revisao.
#
# O texto publicado continua saindo por `aplicar_rascunho`, com as mesmas travas
# de link e sanitizacao. As secoes sao material de trabalho, nao um segundo
# caminho para o ar.

# Um plano com uma secao so nao e plano: ou a pauta e estreita demais, ou o
# modelo devolveu lixo. Vale falhar e deixar a pessoa ajustar a pauta.
MINIMO_DE_SECOES = 2
MAXIMO_DE_SECOES = 8

# Um tema por publicacao; dois no limite, quando sao faces do mesmo assunto.
# Acima disso o texto nao responde bem a nenhum deles e, para busca organica,
# compete consigo mesmo.
MAXIMO_DE_TEMAS = 2

# Poucas referencias, so as mais importantes. O formato imitado aqui e o de um
# artigo de divulgacao bem apurado, nao o de uma tese: uma pagina cheia de
# links de saida descaracteriza a curadoria e dilui o valor de cada um deles.
MAXIMO_DE_FONTES_NO_ARTIGO = 2


class PlanoInvalido(ValueError):
    """O planejamento nao devolveu algo utilizavel."""


@dataclass(frozen=True)
class SecaoPlanejada:
    titulo: str
    objetivo: str
    palavras_chave: list[str]
    fontes: list[int]
    sustenta_ideia_central: bool = False


@dataclass(frozen=True)
class PlanoDoArtigo:
    palavra_chave: str
    palavras_secundarias: list[str]
    intencao: str
    publico: str
    secoes: list[SecaoPlanejada]
    ideia_central: str = ""
    temas: list[str] = field(default_factory=list)
    fontes_da_ideia_central: list[int] = field(default_factory=list)


def interpretar_plano(texto: str, *, total_de_fontes: int) -> PlanoDoArtigo:
    """Le o JSON do planejamento e recusa o que nao da para usar.

    A validacao dos numeros de fonte nao e zelo: o modelo escolhe quais fontes
    cabem a cada secao, e um numero inventado faria a secao ser escrita com a
    fonte errada — ou com nenhuma, o que e pior, porque o texto sai mesmo assim
    e parece fundamentado.
    """
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise PlanoInvalido(f"o planejamento nao devolveu JSON valido: {texto[:200]}") from exc

    secoes_brutas = dados.get("secoes") or []
    if not isinstance(secoes_brutas, list):
        raise PlanoInvalido("o campo 'secoes' precisa ser uma lista.")
    if not MINIMO_DE_SECOES <= len(secoes_brutas) <= MAXIMO_DE_SECOES:
        raise PlanoInvalido(
            f"o plano veio com {len(secoes_brutas)} secao(oes); "
            f"o aceitavel e de {MINIMO_DE_SECOES} a {MAXIMO_DE_SECOES}."
        )

    secoes = []
    for bruta in secoes_brutas:
        titulo = str(bruta.get("titulo") or "").strip()
        if not titulo:
            raise PlanoInvalido("uma das secoes veio sem titulo.")

        # Numero fora da faixa e descartado em silencio; ficar sem nenhum e que
        # e erro. Uma secao sem fonte nao tem como ser escrita com fundamento.
        fontes = [n for n in _inteiros(bruta.get("fontes")) if 1 <= n <= total_de_fontes]
        if not fontes:
            raise PlanoInvalido(
                f"a secao {titulo!r} nao ficou com nenhuma fonte valida "
                f"(o artigo tem {total_de_fontes})."
            )

        secoes.append(
            SecaoPlanejada(
                titulo=titulo[:200],
                objetivo=str(bruta.get("objetivo") or "").strip(),
                palavras_chave=_textos(bruta.get("palavras_chave")),
                fontes=fontes,
                sustenta_ideia_central=bool(bruta.get("sustenta_ideia_central")),
            )
        )

    ideia_central = str(dados.get("ideia_central") or "").strip()
    fontes_centrais = [
        n for n in _inteiros(dados.get("fontes_da_ideia_central")) if 1 <= n <= total_de_fontes
    ]
    temas = _textos(dados.get("temas"))

    _exigir_embasamento_central(ideia_central, fontes_centrais, secoes)

    # Tema demais nao invalida o plano — corta-se o excedente. A ideia central,
    # essa sim, e uma so, e ja foi conferida acima.
    if len(temas) > MAXIMO_DE_TEMAS:
        logger.warning(
            "O plano trouxe %s temas; mantidos os %s primeiros.", len(temas), MAXIMO_DE_TEMAS
        )
        temas = temas[:MAXIMO_DE_TEMAS]

    return PlanoDoArtigo(
        palavra_chave=str(dados.get("palavra_chave") or "").strip()[:120],
        palavras_secundarias=_textos(dados.get("palavras_secundarias"))[:8],
        intencao=str(dados.get("intencao") or "").strip()[:200],
        publico=str(dados.get("publico") or "").strip()[:200],
        secoes=secoes,
        ideia_central=ideia_central,
        temas=temas,
        fontes_da_ideia_central=fontes_centrais,
    )


def _exigir_embasamento_central(
    ideia_central: str, fontes_centrais: list[int], secoes: list[SecaoPlanejada]
) -> None:
    """Recusa um plano cuja ideia central nao se apoia em fonte nenhuma.

    Esta e a trava que o produto existe para ter. Os paragrafos secundarios
    podem se apoiar em conhecimento geral — e legitimo e deixa o texto legivel.
    A ideia central, nao: e a afirmacao que a publicacao faz, e um texto que a
    faz sem fonte e exatamente o texto que parece fundamentado e nao e.

    O caminho de saida nao e um texto pior. E parar, avisar, e alguem
    acrescentar o artigo de referencia que falta.
    """
    if not ideia_central:
        raise PlanoInvalido("o plano veio sem ideia central.")

    if not fontes_centrais:
        raise SemEmbasamentoCentral(
            f"nenhuma das fontes recuperadas sustenta a ideia central "
            f"({ideia_central!r}). Acrescente ao acervo um artigo de referencia "
            f"sobre isso, ou ajuste a pauta para o que o acervo ja sustenta, e "
            f"mande gerar de novo."
        )

    if not any(s.sustenta_ideia_central for s in secoes):
        raise SemEmbasamentoCentral(
            f"o plano nao marcou nenhuma secao como responsavel pela ideia "
            f"central ({ideia_central!r}). Sem isso ela nao seria afirmada em "
            f"lugar nenhum do texto, ou seria afirmada sem fonte."
        )


def _inteiros(valor) -> list[int]:
    if not isinstance(valor, list):
        return []
    numeros = []
    for item in valor:
        try:
            numeros.append(int(item))
        except (TypeError, ValueError):
            continue
    return numeros


def _textos(valor) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [str(item).strip() for item in valor if str(item).strip()]


@transaction.atomic
def aplicar_plano(article: Article, plano: PlanoDoArtigo, *, trechos) -> list:
    """Grava o plano como secoes vazias, prontas para serem escritas.

    Substitui o plano anterior por inteiro. Replanejar e o caminho para quando o
    esqueleto ficou errado, e manter secoes do plano velho misturadas com as do
    novo produziria um artigo que nenhum dos dois planos previa.
    """
    from apps.content.models import ArticleSection

    article.sections.all().delete()

    if plano.palavra_chave:
        article.focus_keyword = plano.palavra_chave
    article.secondary_keywords = plano.palavras_secundarias
    article.audience = plano.publico
    article.search_intent = plano.intencao
    article.central_idea = plano.ideia_central
    article.save(
        update_fields=[
            "focus_keyword",
            "secondary_keywords",
            "audience",
            "search_intent",
            "central_idea",
        ]
    )

    # O numero da fonte no plano e a posicao dela na lista recuperada, que e a
    # mesma numeracao do marcador [[FONTE_n]]. Guardar o id resolve a traducao
    # uma vez so, aqui.
    #
    # A lista chega ora como `TrechoRecuperado` (vindo da busca), ora como
    # `SuperChunk` puro (recarregado do payload do passo anterior). O mesmo
    # desembrulho que `montar_contexto_das_fontes` faz.
    por_numero = {
        i: str((t.chunk if hasattr(t, "chunk") else t).pk) for i, t in enumerate(trechos, start=1)
    }

    criadas = []
    for ordem, secao in enumerate(plano.secoes, start=1):
        fontes = list(secao.fontes)
        if secao.sustenta_ideia_central:
            # A secao que afirma a ideia central recebe, alem das suas, as
            # fontes que sustentam essa ideia. Sem isso ela poderia ser escrita
            # sem ter na frente aquilo que precisa citar.
            fontes += [n for n in plano.fontes_da_ideia_central if n not in fontes]

        criadas.append(
            ArticleSection.objects.create(
                article=article,
                order=ordem,
                level=2,
                heading=secao.titulo,
                intent=secao.objetivo,
                keywords=secao.palavras_chave,
                carries_central_idea=secao.sustenta_ideia_central,
                chunk_ids=[por_numero[n] for n in fontes if n in por_numero],
            )
        )
    return criadas


def esqueleto_do_artigo(article: Article, *, exceto=None) -> str:
    """O indice do artigo, para uma secao saber o que as outras cobrem.

    E o unico contexto que uma secao recebe sobre o resto: barato em tokens e
    suficiente para nao invadir o assunto do vizinho, que e o defeito classico
    de escrever aos pedacos.
    """
    linhas = []
    for secao in article.sections.all():
        marca = " (esta)" if exceto is not None and secao.pk == exceto.pk else ""
        objetivo = f" — {secao.intent}" if secao.intent else ""
        linhas.append(f"{secao.order}. {secao.heading}{marca}{objetivo}")
    return "\n".join(linhas)


def montar_markdown_das_secoes(article: Article) -> str:
    """Junta abertura, secoes e fecho num Markdown unico.

    A abertura e o fecho ficam no `thesis_json` porque nao sao secoes: nao tem
    titulo proprio no texto publicado e nao devem aparecer na lista que a
    revisao oferece para refazer isoladamente.
    """
    moldura = (article.thesis_json or {}).get("moldura") or {}
    partes = []

    abertura = (moldura.get("abertura") or "").strip()
    if abertura:
        partes.append(abertura)

    for secao in article.sections.all():
        corpo = secao.body_markdown.strip()
        if not corpo:
            continue
        marcas = "#" * max(2, min(secao.level, 4))
        partes.append(f"{marcas} {secao.heading}\n\n{corpo}")

    fecho = (moldura.get("fecho") or "").strip()
    if fecho:
        partes.append(fecho)

    return "\n\n".join(partes)


# ---------------------------------------------------------------------------
# Refazer, na revisao
# ---------------------------------------------------------------------------
@transaction.atomic
def marcar_secoes_para_refazer(article: Article, ordens: set[int]) -> int:
    """Esvazia o texto das secoes escolhidas, para o passo reescreve-las.

    Esvaziar em vez de sinalizar mantem UMA regra sobre o que falta escrever —
    "secao sem texto" — valida tanto na geracao inicial quanto aqui. Um segundo
    criterio (um campo "precisa refazer") daria duas respostas possiveis para a
    mesma pergunta, e uma delas ficaria errada.

    O que a pessoa editou a mao e preservado nas revisoes do artigo; o texto da
    secao em si e substituido, porque foi isso que ela pediu.
    """
    from apps.content.models import ArticleSection

    alvo = article.sections.filter(order__in=ordens)
    total = alvo.count()
    alvo.update(body_markdown="", status=ArticleSection.Status.PLANNED, prompt_run=None)
    return total


@transaction.atomic
def limpar_plano(article: Article) -> None:
    """Descarta o esqueleto inteiro, para replanejar do zero.

    Separado de `marcar_secoes_para_refazer` porque sao decisoes de tamanhos
    diferentes: uma troca o texto de uma secao, a outra pode mudar quantas
    secoes o artigo tem e sobre o que cada uma fala. A interface precisa
    manter essa distancia.
    """
    article.sections.all().delete()

    tese = dict(article.thesis_json or {})
    # A moldura descreve um esqueleto que nao existe mais; mante-la faria a
    # abertura prometer secoes que o novo plano pode nao ter.
    tese.pop("moldura", None)
    article.thesis_json = tese
    article.save(update_fields=["thesis_json"])
