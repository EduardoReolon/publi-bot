"""A UNICA porta entre as redes sociais e o resto do PubliBot.

Tudo o que o modulo le do nucleo (artigo, negocio, metricas do site, guia
editorial, modelo de linguagem, perguntas) passa por aqui, em formas simples
(dataclass, dict). Se um dia as redes virarem um servico separado, e este
arquivo que vira cliente de API — o resto do modulo nao muda. O teste
`test_fronteira.py` garante que nenhum outro arquivo do modulo importa o nucleo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú\"“(0-9])")
_MARCAS = re.compile(r"\[[^\]]*\]\([^)]*\)|<[^>]+>|\*\*|__|`")


def _texto_limpo(markdown: str) -> str:
    """Markdown do artigo sem links, HTML e lista de referencias."""
    corpo = re.split(r"\n#+\s*Refer[eê]ncias|\n<details", markdown or "")[0]

    def ancora(achado: re.Match) -> str:
        texto = achado.group(0)
        return re.sub(r"^\[([^\]]*)\]\([^)]*\)$", r"\1", texto) if texto.startswith("[") else ""

    return _MARCAS.sub(ancora, corpo)


@dataclass
class ArtigoParaRedes:
    id: str
    titulo: str
    url: str
    resumo: str
    palavra_chave: str
    secoes: list[tuple[str, str]] = field(default_factory=list)
    frases: list[str] = field(default_factory=list)
    texto: str = ""
    publicado_em: object = None
    remote_id: str = ""
    capa: str = ""  # caminho no storage
    capa_url: str = ""  # URL publica


def artigo(artigo_id) -> ArtigoParaRedes | None:
    from apps.content.capas import capa_escolhida, url_publica_da_capa
    from apps.content.models import Article

    obj = Article.objects.filter(pk=artigo_id).first()
    if obj is None:
        return None
    secoes = [
        (s.heading, _texto_limpo(s.body_markdown))
        for s in obj.sections.all()
        if (s.body_markdown or "").strip()
    ]
    texto = _texto_limpo(obj.body_markdown)
    frases = []
    for paragrafo in texto.split("\n"):
        paragrafo = paragrafo.strip().lstrip("#-* ").strip()
        if len(paragrafo.split()) < 6:
            continue
        frases += [f.strip() for f in _FIM_DE_FRASE.split(paragrafo) if len(f.split()) >= 6]
    capa = capa_escolhida(obj)
    return ArtigoParaRedes(
        id=str(obj.pk),
        titulo=obj.title,
        url=obj.published_url,
        resumo=obj.meta_description or obj.excerpt,
        palavra_chave=obj.focus_keyword,
        secoes=secoes,
        frases=frases,
        texto=texto,
        publicado_em=obj.published_at,
        remote_id=obj.remote_id,
        capa=capa.image.name if capa and capa.image else "",
        capa_url=url_publica_da_capa(capa) if capa else "",
    )


def artigos_no_ar(desde=None) -> list[str]:
    """Ids dos artigos publicados (a versao que esta no ar), mais novos primeiro."""
    from apps.content.models import Article

    consulta = Article.objects.filter(status=Article.Status.PUBLISHED).exclude(published_url="")
    if desde is not None:
        consulta = consulta.filter(published_at__gte=desde)
    return [str(pk) for pk in consulta.order_by("-published_at").values_list("pk", flat=True)]


def negocio() -> dict:
    """Tema, publico, oferta, dores e regioes, como o Negocio e o Radar guardam."""
    from apps.editorial.models import PerfilDoNegocio
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = PerfilDoNegocio.objects.first()
    radar = ConfiguracaoDoRadar.carregar()
    regioes = []
    for regiao in radar.regioes or []:
        nome = str(regiao.get("nome") or "").split(",")[0].strip()
        if nome:
            regioes.append(nome)
    return {
        "tema": getattr(perfil, "tema", "") or "",
        "publico": getattr(perfil, "publico", "") or "",
        "oferta": getattr(perfil, "oferta", "") or "",
        "dores": radar.lista_de_dores,
        "regioes": regioes,
        "idioma": getattr(site(), "content_language", "") or "pt-BR",
    }


def site():
    from apps.integrations.models import Site

    return Site.objects.first()


def sinais(artigo: ArtigoParaRedes) -> dict:
    """O que o resto do PubliBot ja sabe do artigo: quase na primeira pagina do
    Google (sugestao aberta do Radar) e conversoes no site (90 dias)."""
    from apps.integrations.models import ConversaoDoSite
    from apps.radar.models import SugestaoDeAtualizacao

    quase_la = SugestaoDeAtualizacao.objects.filter(
        url=artigo.url, tipo="quase_la", situacao="aberta"
    ).exists()
    conversoes = 0
    if artigo.remote_id:
        desde = timezone.localdate() - timedelta(days=90)
        conversoes = ConversaoDoSite.objects.filter(
            dia__gte=desde, jornada__contains=[{"remote_id": artigo.remote_id}]
        ).count()
    return {"quase_la": quase_la, "conversoes": conversoes}


def demanda(limite: int = 300) -> list[dict]:
    """Os grupos de demanda do Radar (o que o publico busca), com volume e o
    vetor que o Radar ja calculou: [{"rotulo", "volume", "vetor"}]."""
    from apps.radar.models import GrupoDeDemanda

    grupos = (
        GrupoDeDemanda.objects.exclude(centroide__isnull=True)
        .exclude(situacao="descartado")
        .order_by("-volume_total")[:limite]
    )
    return [
        {"rotulo": g.rotulo, "volume": g.volume_total, "vetor": list(g.centroide)} for g in grupos
    ]


def endereco_do_site() -> str:
    """A pagina inicial do site do cliente (destino de post sem artigo)."""
    alvo = site()
    return (getattr(alvo, "base_url", "") or "").strip()


def conversoes_pelas_redes(remote_id: str, desde) -> int:
    """Conversoes que entraram (ou voltaram) por rede social e passaram pelo
    artigo, desde a data: o que o post daquele artigo provavelmente trouxe."""
    from django.db.models import Q

    from apps.integrations.models import ConversaoDoSite

    if not remote_id:
        return 0
    return (
        ConversaoDoSite.objects.filter(dia__gte=desde, jornada__contains=[{"remote_id": remote_id}])
        .filter(Q(canal_de_entrada="social") | Q(canal_final="social"))
        .count()
    )


# -- Modelo de linguagem e vetores -------------------------------------------------
def modelo_indisponivel() -> tuple[type[Exception], ...]:
    """As excecoes de "sem modelo agora" (nao configurado, ou placa ocupada)."""
    from apps.content.inference import SemModeloConfigurado
    from apps.ops.orchestrator import PassoAdiado

    return (SemModeloConfigurado, PassoAdiado, LookupError)


def executar(
    chave: str,
    variaveis: dict,
    *,
    json_schema: dict | None = None,
    imagens: list[tuple[str, bytes]] | None = None,
) -> str:
    """`imagens`: [(tipo, bytes)] para modelo que enxerga (descrever uma foto)."""
    from apps.content.inference import executar_prompt

    return executar_prompt(
        key=chave,
        variaveis=variaveis,
        site=site(),
        json_schema=json_schema,
        com_convite=False,
        imagens=imagens,
    ).texto


class TranscricaoAdiada(Exception):
    """O worker da placa esta ocupado (ou desligado): tentar mais tarde."""


class TranscricaoImpossivel(Exception):
    """Sem worker de transcricao, ou o audio nao serviu."""


def transcrever(nome: str, conteudo: bytes, *, dono: str) -> str:
    """Audio -> texto, pelo mesmo worker (Whisper) que transcreve o acervo."""
    from apps.knowledge.extraction import (
        ConversorOcupado,
        ExtracaoIndisponivel,
        transcrever_audio,
    )

    idioma = (getattr(site(), "content_language", "") or "pt")[:2]
    try:
        segmentos, _duracao = transcrever_audio(
            nome, conteudo, idioma=idioma, dono=dono, timeout=600.0
        )
    except ConversorOcupado as exc:
        raise TranscricaoAdiada(str(exc)) from exc
    except ExtracaoIndisponivel as exc:
        raise TranscricaoImpossivel(str(exc)) from exc
    return " ".join(texto.strip() for _inicio, texto in segmentos).strip()


class LinkIlegivel(Exception):
    """A pagina nao abriu, nao e publica ou nao tem texto aproveitavel."""


def ler_link(url: str) -> dict:
    """O texto principal de uma pagina de terceiros (noticia, estudo), com
    titulo e nome do site. Mesma busca protegida do acervo (so endereco
    publico, redirecionamentos conferidos)."""
    from apps.knowledge.web import PaginaIndisponivel, baixar, extrair_pagina

    try:
        conteudo, final, tipo = baixar(url)
        if "pdf" in (tipo or "").lower():
            raise LinkIlegivel("e um PDF: cole o trecho que importa no texto.")
        pagina = extrair_pagina(conteudo, url=final)
    except PaginaIndisponivel as exc:
        raise LinkIlegivel(str(exc)) from exc
    texto = _texto_limpo(pagina.markdown)
    if len(texto.split()) < 80:
        raise LinkIlegivel(
            "quase sem texto (pagina com login, paywall ou feita so de imagem): cole o "
            "trecho que importa no texto."
        )
    return {
        "url": final,
        "titulo": (pagina.titulo or "")[:300],
        "site": (pagina.site or "")[:120],
        "data": pagina.data.isoformat() if pagina.data else "",
        "texto": texto[:12000],
    }


def vetores(textos: list[str], *, consulta: bool = False) -> list[list[float]]:
    from apps.knowledge.embeddings import get_embedding_client

    cliente = get_embedding_client()
    if consulta:
        return [cliente.embed_query(t) for t in textos]
    return cliente.embed_passage(textos)


def termos_a_evitar(texto: str) -> list[str]:
    """Termos proibidos do guia editorial que aparecem no texto."""
    from apps.editorial.services import conferir_texto, perfil_atual

    conferencia = conferir_texto(texto, perfil_atual())
    return [
        f'"{a.expressao}"' + (f' (use "{a.sugestao}")' if a.sugestao else "")
        for a in conferencia.proibidos
    ]


def cuidados_de_linguagem(texto: str) -> list[str]:
    """Linguagem que estigmatiza e temas sensiveis sem o cuidado (aviso)."""
    from apps.editorial.linguagem import cuidados

    return [
        f'"{a.expressao}"' + (f' (prefira "{a.sugestao}")' if a.sugestao else "") + f": {a.motivo}"
        for a in cuidados(texto)
    ]


def instrucoes_sensiveis(texto: str) -> str:
    """As instrucoes dos temas sensiveis presentes no material."""
    from apps.editorial.linguagem import INSTRUCAO_GERAL, instrucoes_sensiveis

    return "\n".join(x for x in [INSTRUCAO_GERAL.lstrip("- "), instrucoes_sensiveis(texto)] if x)


# -- Perguntas ------------------------------------------------------------------------
def criar_pergunta(texto: str, referencia: str) -> str | None:
    """Uma pergunta vinda de comentario vira Pergunta do PubliBot (a mesma fila
    das perguntas do site). Sem nome de quem perguntou. Devolve o id."""
    from apps.content.models import Question

    alvo = site()
    if alvo is None:
        return None
    pergunta, _ = Question.objects.get_or_create(
        site=alvo,
        remote_id=referencia[:120],
        defaults={
            "question_text": texto[:500],
            "submitted_at": timezone.now(),
            "retention_until": timezone.now() + timedelta(days=90),
        },
    )
    return str(pergunta.pk)


def respostas(pergunta_ids: list[str]) -> dict[str, dict]:
    """As respostas aprovadas (ou publicadas) das perguntas: {id: {texto, url}}."""
    from apps.content.models import Answer

    prontas = Answer.objects.filter(
        question_id__in=pergunta_ids, status__in=["approved_scheduled", "published"]
    )
    return {
        str(r.question_id): {"texto": _texto_limpo(r.body_markdown), "url": r.published_url}
        for r in prontas
    }


# -- Enderecos ------------------------------------------------------------------------
def endereco_publico(caminho: str) -> str:
    """URL absoluta no dominio do tenant (link rastreado, imagens, link na bio)."""
    from django.conf import settings
    from django.db import connection

    tenant = getattr(connection, "tenant", None)
    dominios = getattr(tenant, "domains", None)
    dominio = None
    if dominios is not None:
        dominio = dominios.filter(is_primary=True).first() or dominios.first()
    if dominio is None:
        return caminho
    return f"{settings.ESQUEMA_PUBLICO}://{dominio.domain}{caminho}"
