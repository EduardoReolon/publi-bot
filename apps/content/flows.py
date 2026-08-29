"""Fluxos de geracao, registrados no orquestrador.

Aqui os componentes viram um pipeline. Ate esta ligacao existir, `recuperar`,
`interpretar_tese` e `aplicar_rascunho` eram funcoes testadas que ninguem
chamava — o motor estava construido e nunca era montado.

Cada passo e uma funcao que recebe o `GenerationJob` e devolve um dicionario.
Tres regras vem do orquestrador e explicam o formato:

1. **Um passo por chamada.** O `avancar()` executa no maximo um passo e grava
   o resultado. Uma inferencia de dois minutos nao segura as outras.
2. **O passo re-entra pelo banco.** O que um passo precisa do anterior vem de
   `job.step_payloads`, nunca de memoria — o processo pode ter morrido entre
   um e outro.
3. **`PassoAdiado` nao gasta tentativa.** Esperar capacidade nao e falhar.

Por isso os payloads carregam **ids**, e nao objetos: recarregar do banco
garante que o passo trabalha sobre o estado atual, e nao sobre um retrato de
minutos atras.
"""

from __future__ import annotations

import logging

from django.utils.text import slugify

from apps.content.inference import executar_prompt
from apps.content.models import Article, ArticleSection, Question, Topic
from apps.content.rendering import validar_saida_do_modelo
from apps.content.services import (
    SemFontesSuficientes,
    aplicar_plano,
    aplicar_rascunho,
    esqueleto_do_artigo,
    interpretar_plano,
    interpretar_tese,
    montar_contexto_das_fontes,
    montar_markdown_das_secoes,
    registrar_citacoes,
)
from apps.knowledge.models import RetrievalQuery, SuperChunk
from apps.knowledge.services import recuperar
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import Continuar, Fluxo, Passo, registrar_fluxo

logger = logging.getLogger("publibot.content")


def _site_do_tenant():
    """O site deste tenant, se houver.

    A relacao e 1 para 1 por regra de produto (ADR-0003): um cliente com tres
    sites usa tres tenants. Dentro de um schema existe no maximo um `Site`.
    """
    from apps.integrations.models import Site

    return Site.objects.first()


def _chunks_do_payload(job: GenerationJob, passo: str) -> list[SuperChunk]:
    """Recarrega os trechos pelos ids gravados, na ordem em que foram usados.

    A ordem importa: ela e o numero do marcador `[[FONTE_n]]` que o modelo
    recebe e que depois vira link. Reordenar aqui trocaria as fontes de lugar
    no texto publicado.
    """
    ids = (job.step_payloads or {}).get(passo, {}).get("chunk_ids", [])
    por_id = {str(c.pk): c for c in SuperChunk.objects.filter(pk__in=ids)}
    return [por_id[i] for i in ids if i in por_id]


# ---------------------------------------------------------------------------
# Artigo pilar
# ---------------------------------------------------------------------------
def passo_recuperar_fontes(job: GenerationJob) -> dict:
    """Busca no acervo o que sustenta a pauta.

    Falha aqui e falha barata e correta: sem fonte acima do limiar, gerar o
    artigo gastaria inferencia para produzir exatamente o que o produto existe
    para evitar — texto sem fundamentacao.
    """
    topic = Topic.objects.filter(pk=job.target_object_id).first()
    if topic is None:
        raise ValueError(f"pauta {job.target_object_id} nao existe")

    consulta = " ".join(filter(None, [topic.title, topic.target_keyword, topic.briefing]))

    _, trechos = recuperar(consulta=consulta, origem=RetrievalQuery.Origin.ARTICLE)

    if not trechos:
        raise SemFontesSuficientes(
            f"nenhum trecho do acervo ficou abaixo do limiar de distancia para "
            f"a pauta {topic.title!r}. Envie documentos sobre o tema, ou ajuste "
            f"a pauta para algo que o acervo sustente."
        )

    return {
        "chunk_ids": [str(t.chunk.pk) for t in trechos],
        "distancias": [round(t.distancia, 4) for t in trechos],
        "topic_id": str(topic.pk),
    }


def passo_filtrar_consenso(job: GenerationJob) -> dict:
    """Consolida as fontes numa tese e classifica a concordancia entre elas.

    O passo cria o `Article` porque e aqui que existe conteudo para ele: antes
    da tese nao ha nada a guardar, e criar o registro antes deixaria artigos
    vazios no banco a cada falha de recuperacao.
    """
    topic = Topic.objects.get(pk=job.target_object_id)
    trechos = _chunks_do_payload(job, "0")
    site = _site_do_tenant()

    resultado = executar_prompt(
        key="consensus_filter",
        variaveis={"tema": topic.title, "fontes": montar_contexto_das_fontes(trechos)},
        site=site,
        job=job,
    )

    tese = interpretar_tese(resultado.texto)

    from apps.content.services import MAPA_DE_CONCORDANCIA

    article = Article.objects.create(
        topic=topic,
        title=topic.title,
        slug=slugify(topic.title)[:300],
        focus_keyword=topic.target_keyword,
        thesis_json=tese.bruto,
        consensus=MAPA_DE_CONCORDANCIA[tese.concordancia],
        single_source=len(trechos) == 1,
        author_name=getattr(site, "default_author", "") or "",
        author_credentials=getattr(site, "default_author_credentials", "") or "",
        status=Article.Status.DRAFTING,
    )
    registrar_citacoes(article, trechos)

    # A pauta vira USED aqui, e nao no fim: a partir do momento em que existe
    # um artigo ligado a ela, sugeri-la de novo produziria dois textos sobre o
    # mesmo tema competindo entre si.
    topic.status = Topic.Status.USED
    topic.save(update_fields=["status"])

    return {
        "article_id": str(article.pk),
        "concordancia": tese.concordancia,
        "tese": tese.tese,
        "fonte_unica": article.single_source,
    }


def passo_planejar(job: GenerationJob) -> dict:
    """Decide a estrutura do artigo e as palavras-chave, numa chamada curta.

    Existir como passo proprio e o que torna possivel escrever secao a secao
    depois: sem um esqueleto decidido antes, cada secao seria escrita as cegas e
    duas delas responderiam a mesma pergunta.
    """
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])
    trechos = _chunks_do_payload(job, "0")
    site = _site_do_tenant()

    resultado = executar_prompt(
        key="article_outline",
        variaveis={
            "titulo": article.title,
            "tese": payload.get("tese", ""),
            "fontes": montar_contexto_das_fontes(trechos),
            "palavra_chave": article.focus_keyword or article.title,
            "publico": article.audience or _publico_padrao(site),
            "idioma": _idioma(site),
        },
        site=site,
        job=job,
    )

    plano = interpretar_plano(resultado.texto, total_de_fontes=len(trechos))
    secoes = aplicar_plano(article, plano, trechos=trechos)

    logger.info(
        "Artigo %s planejado com %s secao(oes), palavra-chave %r.",
        article.pk,
        len(secoes),
        plano.palavra_chave,
    )
    return {
        "article_id": str(article.pk),
        "secoes": len(secoes),
        "palavra_chave": plano.palavra_chave,
        "palavras_secundarias": plano.palavras_secundarias,
    }


def passo_redigir_secoes(job: GenerationJob):
    """Escreve UMA secao por chamada, e pede para ser chamado de novo.

    Uma chamada por secao, e nao um laco escrevendo todas: cada inferencia tem o
    contexto minimo dela, e um trabalho interrompido no meio retoma na secao
    seguinte em vez de refazer o artigo. Quem sabe o que falta e o banco — as
    secoes sem texto.
    """
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])
    site = _site_do_tenant()

    pendentes = [s for s in article.sections.all() if not s.escrita]
    if not pendentes:
        return {"article_id": str(article.pk), "secoes_escritas": article.sections.count()}

    secao = pendentes[0]
    trechos = _chunks_por_id(secao.chunk_ids)

    resultado = executar_prompt(
        key="section_draft",
        variaveis={
            "titulo_do_artigo": article.title,
            "titulo_da_secao": secao.heading,
            "objetivo": secao.intent,
            "palavras_chave": ", ".join(secao.keywords) or article.focus_keyword,
            "esqueleto": esqueleto_do_artigo(article, exceto=secao),
            "fontes": montar_contexto_das_fontes(trechos),
            "idioma": _idioma(site),
        },
        site=site,
        job=job,
    )

    # A validacao de link roda por secao, e nao so na montagem: uma URL escrita
    # pelo modelo precisa derrubar a secao que a produziu, e nao um artigo
    # inteiro que ja custou cinco outras chamadas.
    validar_saida_do_modelo(resultado.texto)

    secao.body_markdown = resultado.texto.strip()
    secao.status = ArticleSection.Status.WRITTEN
    secao.prompt_run = resultado.prompt_run
    secao.save(update_fields=["body_markdown", "status", "prompt_run", "updated_at"])

    restantes = len(pendentes) - 1
    logger.info(
        "Artigo %s: secao %s/%s escrita (%s).",
        article.pk,
        secao.order,
        article.sections.count(),
        secao.heading[:60],
    )

    progresso = {
        "article_id": str(article.pk),
        "secoes_escritas": article.sections.count() - restantes,
        "secoes_totais": article.sections.count(),
    }
    return progresso if restantes == 0 else Continuar(progresso)


def passo_abertura_e_fecho(job: GenerationJob) -> dict:
    """Escreve a abertura e o fecho, agora que o corpo existe.

    Por ultimo de proposito: abertura escrita antes do corpo promete o que o
    artigo nao cumpre, e e o defeito mais comum de texto gerado.
    """
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])
    site = _site_do_tenant()

    resultado = executar_prompt(
        key="article_framing",
        variaveis={
            "titulo": article.title,
            "tese": payload.get("tese", ""),
            "esqueleto": esqueleto_do_artigo(article),
            "palavra_chave": article.focus_keyword or article.title,
            "idioma": _idioma(site),
        },
        site=site,
        job=job,
    )

    moldura = _ler_json(resultado.texto, contexto="abertura e fecho")
    abertura = str(moldura.get("abertura") or "").strip()
    fecho = str(moldura.get("fecho") or "").strip()

    validar_saida_do_modelo(f"{abertura}\n\n{fecho}")

    tese = dict(article.thesis_json or {})
    tese["moldura"] = {"abertura": abertura, "fecho": fecho}
    article.thesis_json = tese
    article.save(update_fields=["thesis_json"])

    return {"article_id": str(article.pk), "tem_abertura": bool(abertura)}


def passo_metadados_de_busca(job: GenerationJob) -> dict:
    """Titulo, meta description e resumo — a partir da abertura ja escrita."""
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])
    site = _site_do_tenant()
    moldura = (article.thesis_json or {}).get("moldura") or {}

    resultado = executar_prompt(
        key="seo_metadata",
        variaveis={
            "titulo": article.title,
            "abertura": moldura.get("abertura", ""),
            "palavra_chave": article.focus_keyword or article.title,
            "idioma": _idioma(site),
        },
        site=site,
        job=job,
    )

    dados = _ler_json(resultado.texto, contexto="metadados de busca")
    titulos = [str(t).strip() for t in (dados.get("titulos") or []) if str(t).strip()]

    # As opcoes de titulo ficam guardadas para a revisao escolher. Trocar o
    # titulo aqui, sem ninguem ver, mudaria a URL de um artigo que a pauta ja
    # nomeou.
    tese = dict(article.thesis_json or {})
    tese["titulos_sugeridos"] = titulos[:5]
    article.thesis_json = tese
    article.meta_description = str(dados.get("meta_description") or "").strip()[:160]
    article.excerpt = str(dados.get("resumo") or "").strip()
    article.save(update_fields=["thesis_json", "meta_description", "excerpt"])

    return {"article_id": str(article.pk), "titulos_sugeridos": len(titulos)}


def passo_montar(job: GenerationJob) -> dict:
    """Junta as pecas e passa pela mesma trava de sempre.

    A montagem nao tem caminho proprio para o ar: chama `aplicar_rascunho`, que
    recusa URL escrita pelo modelo, troca os marcadores por links vindos das
    citacoes gravadas e sanitiza. Ter um segundo caminho aqui seria abrir a
    porta dos fundos por onde um link alucinado chegaria ao site do cliente.
    """
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])

    markdown = montar_markdown_das_secoes(article)
    if not markdown.strip():
        raise ValueError(f"o artigo {article.pk} nao tem nenhuma secao escrita.")

    aplicar_rascunho(article, markdown)
    article.refresh_from_db()

    logger.info(
        "Artigo %s montado (%s palavras, %s secoes) e aguardando revisao.",
        article.pk,
        article.word_count,
        article.sections.count(),
    )
    return {"article_id": str(article.pk), "palavras": article.word_count}


def _chunks_por_id(ids: list[str]) -> list[SuperChunk]:
    """Recarrega os trechos na ordem gravada.

    A ordem e a numeracao do marcador `[[FONTE_n]]` que o modelo ve. Reordenar
    aqui trocaria as fontes de lugar no texto.
    """
    por_id = {str(c.pk): c for c in SuperChunk.objects.filter(pk__in=ids)}
    return [por_id[i] for i in ids if i in por_id]


def _idioma(site) -> str:
    return getattr(site, "content_language", "pt-BR") or "pt-BR"


def _publico_padrao(site) -> str:
    return getattr(site, "niche", "") or "leitores nao especialistas"


def _ler_json(texto: str, *, contexto: str) -> dict:
    import json

    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{contexto}: o modelo nao devolveu JSON valido: {texto[:200]}") from exc
    if not isinstance(dados, dict):
        raise ValueError(f"{contexto}: esperado um objeto JSON, veio {type(dados).__name__}.")
    return dados


def passo_redigir(job: GenerationJob) -> dict:
    """Escreve o artigo e o deixa aguardando revisao humana.

    O texto do modelo nunca vira HTML direto: `aplicar_rascunho` recusa
    qualquer URL escrita pelo modelo, troca os marcadores por links vindos das
    citacoes gravadas e sanitiza o resultado.
    """
    payload = (job.step_payloads or {}).get("1", {})
    article = Article.objects.get(pk=payload["article_id"])
    trechos = _chunks_do_payload(job, "0")
    site = _site_do_tenant()

    resultado = executar_prompt(
        key="seo_draft",
        variaveis={
            "titulo": article.title,
            "tese": payload.get("tese", ""),
            "fontes": montar_contexto_das_fontes(trechos),
            "palavra_chave": article.focus_keyword or article.title,
            "idioma": getattr(site, "content_language", "pt-BR") or "pt-BR",
        },
        site=site,
        job=job,
    )

    aplicar_rascunho(article, resultado.texto, prompt_run=resultado.prompt_run)
    article.refresh_from_db()

    logger.info(
        "Artigo %s redigido (%s palavras) e aguardando revisao.",
        article.pk,
        article.word_count,
    )
    return {"article_id": str(article.pk), "palavras": article.word_count}


# ---------------------------------------------------------------------------
# Resposta a pergunta
# ---------------------------------------------------------------------------
def passo_recuperar_para_pergunta(job: GenerationJob) -> dict:
    question = Question.objects.filter(pk=job.target_object_id).first()
    if question is None:
        raise ValueError(f"pergunta {job.target_object_id} nao existe")

    _, trechos = recuperar(consulta=question.question_text, origem=RetrievalQuery.Origin.QA)

    if not trechos:
        raise SemFontesSuficientes(
            "o acervo nao sustenta esta pergunta. Responder assim mesmo seria "
            "produzir texto sem fonte — exatamente o que o produto evita."
        )

    return {
        "chunk_ids": [str(t.chunk.pk) for t in trechos],
        "distancias": [round(t.distancia, 4) for t in trechos],
        "melhor_distancia": round(trechos[0].distancia, 4),
    }


def passo_responder(job: GenerationJob) -> dict:
    """Escreve a resposta e a deixa aguardando revisao.

    Uma resposta publicada no site de um cliente tem o mesmo peso de um artigo:
    passa pela mesma revisao humana obrigatoria e pelas mesmas regras de link.
    """
    question = Question.objects.get(pk=job.target_object_id)
    trechos = _chunks_do_payload(job, "0")
    site = _site_do_tenant()

    resultado = executar_prompt(
        key="qa_answer",
        variaveis={
            "pergunta": question.question_text,
            "fontes": montar_contexto_das_fontes(trechos),
            "idioma": getattr(site, "content_language", "pt-BR") or "pt-BR",
        },
        site=site,
        job=job,
    )

    from apps.content.services import aplicar_rascunho_de_resposta

    answer = aplicar_rascunho_de_resposta(question, resultado.texto, trechos=trechos, site=site)

    # ANSWERED so depois de publicada; aqui ela ainda espera revisao humana.
    question.status = Question.Status.PENDING_REVIEW
    question.save(update_fields=["status"])

    return {"answer_id": str(answer.pk), "status": answer.status}


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
# O artigo sai em seis rodadas curtas, e nao numa chamada grande. Cada passo tem
# o contexto minimo do que faz: o planejamento ve as fontes e devolve um plano,
# cada secao ve as fontes dela e o esqueleto, a abertura ve o esqueleto pronto e
# os metadados veem a abertura. Nenhum deles precisa do artigo inteiro na
# frente — que e o que permite um modelo pequeno fazer isto bem.
#
# "redigir secoes" se repete: uma chamada por secao, com `Continuar`.
registrar_fluxo(
    Fluxo(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        passos=[
            Passo(numero=0, nome="recuperar fontes", executar=passo_recuperar_fontes),
            Passo(numero=1, nome="filtrar consenso", executar=passo_filtrar_consenso),
            Passo(numero=2, nome="planejar", executar=passo_planejar),
            Passo(numero=3, nome="redigir secoes", executar=passo_redigir_secoes),
            Passo(numero=4, nome="abertura e fecho", executar=passo_abertura_e_fecho),
            Passo(numero=5, nome="metadados de busca", executar=passo_metadados_de_busca),
            Passo(numero=6, nome="montar", executar=passo_montar),
        ],
    )
)

registrar_fluxo(
    Fluxo(
        kind=GenerationJob.Kind.QA_ANSWER,
        passos=[
            Passo(numero=0, nome="recuperar fontes", executar=passo_recuperar_para_pergunta),
            Passo(numero=1, nome="responder", executar=passo_responder),
        ],
    )
)
