"""As referencias de uma pauta: o que o acervo ja tem, o que espera a pessoa e
o que as buscas acharam.

Tres perguntas que a pessoa faz diante de uma pauta sem fontes, e que a tela
responde num lugar so:

* o que o acervo JA tem sobre isso (paginas, videos, artigos, documentos)?
* a busca ja foi feita? achou algo? o achado espera conferencia (aprovar ou
  recusar em Fontes sugeridas) ou curadoria (o documento ja entrou)?
* e agora: conferir de novo, buscar com outras palavras ou seguir sem?

Artigos cientificos pedem um minimo proprio (`MINIMO_DE_ARTIGOS`): antes de ir
ao OpenAlex, conta os que o acervo ja tem e os que a busca pelas sementes
cientificas ja trouxe e ainda esperam conferencia — desses, os perto da pauta
passam a ser dela. So a falta vai a busca. Videos nao sao buscados aqui: sao
complemento, como as paginas.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger("publibot.knowledge")

MINIMO_DE_ARTIGOS = 3
# Quantos trechos a contagem do acervo olha. A geracao usa so os primeiros
# (o top_k do tenant); a contagem olha mais para dizer o que ha.
TRECHOS_CONTADOS = 20
CONSULTAS_POR_BUSCA = 3
PENDENTES_COMPARADOS = 60

TIPOS = ("pagina", "video", "artigo", "documento")
# Ainda nao virou fonte: esta convertendo ou esperando curadoria.
_EM_CURADORIA = ("uploaded", "queued", "parsing", "parsed", "pending_curation")


def consulta_da_pauta(pauta) -> str:
    return " ".join(filter(None, [pauta.title, pauta.target_keyword, pauta.briefing]))


def trechos_da_pauta(pauta, *, top_k: int | None = None) -> list:
    from apps.knowledge.models import RetrievalQuery
    from apps.knowledge.services import recuperar

    _, trechos = recuperar(
        consulta=consulta_da_pauta(pauta), origem=RetrievalQuery.Origin.ARTICLE, top_k=top_k
    )
    return trechos


def _chunk(trecho):
    return trecho.chunk if hasattr(trecho, "chunk") else trecho


def sustenta(trechos) -> bool:
    """A mesma regra da geracao: algum trecho, e algum que sustente a ideia central."""
    return bool(trechos) and any(getattr(_chunk(t), "supports_central_idea", True) for t in trechos)


def tipo_do_documento(documento) -> str:
    from apps.knowledge.models import Document

    candidato = next(iter(documento.candidatos.all()), None)
    if candidato is not None:
        return candidato.tipo
    if documento.origin == Document.Origin.YOUTUBE:
        return "video"
    if documento.origin in (Document.Origin.WEB, Document.Origin.URL):
        return "pagina"
    return "documento"


def no_acervo(pauta) -> dict:
    """{tipo: documentos distintos perto da pauta, "suficiente": bool, "em": iso}."""
    from apps.knowledge.models import Document, RetrievalSettings

    trechos = trechos_da_pauta(pauta, top_k=TRECHOS_CONTADOS)
    top_k = RetrievalSettings.carregar().top_k
    ids = []
    for trecho in trechos:
        documento_id = getattr(_chunk(trecho), "document_id", None)
        if documento_id and documento_id not in ids:
            ids.append(documento_id)
    contagem = dict.fromkeys(TIPOS, 0)
    for documento in Document.objects.filter(pk__in=ids).prefetch_related("candidatos"):
        contagem[tipo_do_documento(documento)] += 1
    contagem["suficiente"] = sustenta(trechos[:top_k])
    contagem["em"] = timezone.now().isoformat()
    return contagem


def aguardando(pauta) -> dict:
    """{tipo: {"conferencia": n, "arquivo": n, "curadoria": n}} do que as buscas
    da pauta trouxeram. "arquivo": aprovado, mas esperando o PDF ou o audio que
    a pessoa precisa enviar (ainda nao e documento)."""
    from apps.knowledge.models import CandidatoDeFonte

    saida = {tipo: {"conferencia": 0, "arquivo": 0, "curadoria": 0} for tipo in TIPOS}
    for candidato in CandidatoDeFonte.objects.filter(pauta=pauta).select_related("documento"):
        if candidato.situacao == CandidatoDeFonte.Situacao.PENDENTE:
            saida[candidato.tipo]["conferencia"] += 1
        elif candidato.situacao in (
            CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
            CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO,
        ):
            saida[candidato.tipo]["arquivo"] += 1
        elif (
            candidato.situacao == CandidatoDeFonte.Situacao.APROVADO
            and candidato.documento is not None
            and candidato.documento.status in _EM_CURADORIA
        ):
            saida[candidato.tipo]["curadoria"] += 1
    return saida


def ligar_artigos_da_base(pauta) -> int:
    """Artigos que a busca pelas sementes cientificas trouxe e ainda esperam
    conferencia, sem pauta: os perto desta passam a ser dela. Devolve quantos."""
    from apps.knowledge.models import CandidatoDeFonte, RetrievalSettings
    from apps.radar.agrupamento import _distancia, _vetor

    soltos = list(
        CandidatoDeFonte.objects.filter(
            tipo=CandidatoDeFonte.Tipo.ARTIGO,
            situacao=CandidatoDeFonte.Situacao.PENDENTE,
            pauta__isnull=True,
        )[:PENDENTES_COMPARADOS]
    )
    if not soltos:
        return 0
    limiar = RetrievalSettings.carregar().max_cosine_distance
    vetor = _vetor(f"{pauta.title}. {pauta.target_keyword}")
    ligados = 0
    for candidato in soltos:
        texto = f"{candidato.titulo}. {candidato.trecho[:500]}"
        if _distancia(vetor, _vetor(texto)) <= limiar:
            candidato.pauta = pauta
            candidato.save(update_fields=["pauta"])
            ligados += 1
    return ligados


def consultas_usadas(pauta) -> list[str]:
    busca = pauta.busca_de_fontes or {}
    usadas = []
    for tipo in ("paginas", "artigos"):
        usadas += (busca.get(tipo) or {}).get("consultas", [])
    return list(dict.fromkeys(usadas))


def _do_modelo(pauta, usadas: list[str]) -> dict:
    """{"web": [...], "artigos": [...]} pedidos ao modelo, ou {} sem modelo."""
    import json

    from apps.content.inference import SemModeloConfigurado, executar_prompt
    from apps.integrations.models import Site
    from apps.ops.orchestrator import PassoAdiado

    site = Site.objects.first()
    try:
        resultado = executar_prompt(
            key="source_queries",
            variaveis={
                "pauta": pauta.title,
                "palavra_chave": pauta.target_keyword or pauta.title,
                "ja_usadas": "\n".join(f"- {c}" for c in usadas) or "-",
                "idioma": getattr(site, "content_language", "") or "pt-BR",
            },
            site=site,
            json_schema={
                "type": "object",
                "properties": {
                    "web": {"type": "array", "items": {"type": "string"}},
                    "artigos": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["web", "artigos"],
            },
        )
        dados = json.loads(resultado.texto)
    except (SemModeloConfigurado, PassoAdiado, LookupError, ValueError) as exc:
        logger.info("Pauta %s: sem outras palavras do modelo (%s).", pauta.pk, exc)
        return {}
    return {
        chave: [str(c).strip()[:120] for c in dados.get(chave) or [] if str(c).strip()]
        for chave in ("web", "artigos")
    }


def outras_consultas(pauta) -> dict:
    """{"web": [...], "artigos": [...]} ainda nao usadas.

    Primeiro o que ja existe: as frases reais do tema do radar que originou a
    pauta (como o publico busca), o titulo e a palavra-chave. Depois, se houver
    modelo configurado, as sugestoes dele — as unicas em ingles, para artigos.
    """
    usadas = consultas_usadas(pauta)
    ja = {c.lower() for c in usadas}
    do_tema = [pauta.target_keyword, pauta.title]
    for grupo in pauta.grupos_de_demanda.all():
        do_tema += list(grupo.sinais.values_list("texto", flat=True)[:10])
    modelo = _do_modelo(pauta, usadas)

    def novas(lista):
        saida = []
        for consulta in lista:
            if consulta and consulta.lower() not in ja and consulta not in saida:
                saida.append(consulta)
        return saida[:CONSULTAS_POR_BUSCA]

    return {
        "web": novas(modelo.get("web", []) + do_tema),
        "artigos": novas(modelo.get("artigos", []) + do_tema),
    }


def registrar(pauta, chave: str, **dados) -> None:
    busca = dict(pauta.busca_de_fontes or {})
    busca[chave] = {**(busca.get(chave) or {}), **dados}
    pauta.busca_de_fontes = busca
    pauta.save(update_fields=["busca_de_fontes"])


def acumular_consultas(pauta, chave: str, consultas: list[str]) -> list[str]:
    anteriores = ((pauta.busca_de_fontes or {}).get(chave) or {}).get("consultas", [])
    return list(dict.fromkeys(anteriores + consultas))


def conferir(pauta) -> dict:
    """Reconta o acervo e, se ele agora sustenta a pauta que esperava fontes,
    devolve a pauta para a fila de geracao."""
    from apps.content.models import Topic

    acervo = no_acervo(pauta)
    registrar(pauta, "acervo", **acervo)
    if acervo["suficiente"] and pauta.status == Topic.Status.WAITING_SOURCES:
        pauta.status = Topic.Status.APPROVED
        pauta.save(update_fields=["status"])
        acervo["liberada"] = True
    return acervo


def conferir_as_que_esperam() -> int:
    """Depois de uma curadoria: as pautas aguardando fontes sao conferidas de
    novo. Devolve quantas voltaram para a geracao."""
    from apps.content.models import Topic

    liberadas = 0
    for pauta in Topic.objects.filter(status=Topic.Status.WAITING_SOURCES)[:50]:
        try:
            liberadas += bool(conferir(pauta).get("liberada"))
        except Exception:
            logger.exception("Pauta %s: falha ao conferir as referencias.", pauta.pk)
    return liberadas


def painel(pauta) -> dict:
    """O que a tela mostra, sem consultar o acervo de novo (usa a ultima conferencia)."""
    from apps.radar.models import ConfiguracaoDoRadar

    busca = pauta.busca_de_fontes or {}
    acervo = busca.get("acervo") or {}
    artigos = busca.get("artigos") or {}
    return {
        "acervo": acervo,
        "paginas": busca.get("paginas") or {},
        "artigos": artigos,
        "aguardando": aguardando(pauta),
        "falta_artigos": (
            ConfiguracaoDoRadar.carregar().artigos_cientificos
            and not artigos.get("ignorado")
            and bool(acervo)
            and acervo.get("artigo", 0) < MINIMO_DE_ARTIGOS
        ),
        "minimo_de_artigos": MINIMO_DE_ARTIGOS,
    }
