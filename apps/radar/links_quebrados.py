"""Links quebrados em sites do assunto ("broken link building").

Uma tarefa de fundo, devagar: algumas paginas por hora, dos sites que ja
aparecem nas buscas guardadas do radar (menos o proprio site e os
concorrentes confirmados). De cada pagina, os links para outros sites; de cada
link, so um pedido leve para saber se o destino ainda existe.

O que sobra e oportunidade quando o texto do link e perto de um artigo
publicado (a sugestao de troca vai pronta) ou do tema do site (o assunto pode
virar pauta). O dono do site ganha — conserta um erro que prejudica a pagina
dele —, e e isso que faz o pedido funcionar sem ser troca de links.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from django.utils import timezone

logger = logging.getLogger("publibot.radar")

PAGINAS_POR_VEZ = 5
LINKS_POR_PAGINA = 40
REVERIFICAR_EM_DIAS = 90
TIMEOUT = 10.0
# Quebrado de verdade: a pagina sumiu. 403, 429 e 5xx sao ambiguos (bloqueio
# de robo, instabilidade) e nao contam.
QUEBRADO = {404, 410}
PERTO_DO_ARTIGO = 0.35  # distancia de cosseno do texto do link ao titulo
PERTO_DO_TEMA = 0.3  # proximidade com o negocio (0 a 1), sem artigo


# Links que nao sao citacao no texto, mesmo quando aparecem nele: loja de
# aplicativo, rede social, botao de compartilhar. Quebrados ou nao, nao ha o
# que sugerir no lugar.
SEM_INTERESSE = (
    "play.google.com",
    "apps.apple.com",
    "itunes.apple.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "wa.me",
    "api.whatsapp.com",
    "t.me",
    "pinterest.com",
    "tiktok.com",
)


def _dominio(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _sem_interesse(dominio: str) -> bool:
    return any(dominio == d or dominio.endswith(f".{d}") for d in SEM_INTERESSE)


def _link_torto_do_proprio_site(dominio: str, proprio: str) -> bool:
    """`euax.com.brpolitica-de-privacidade`: o proprio site com a barra faltando.

    E um erro do site, sim, mas de link interno: nao ha conteudo nosso que
    substitua a politica de privacidade dele.
    """
    return bool(proprio) and dominio.startswith(proprio) and dominio != proprio


def decodificar(conteudo: bytes, tipo: str = "") -> str:
    """Pelo charset do cabecalho; sem ele, UTF-8 e, se nao for, Windows-1252.

    Decodificar tudo como UTF-8 transformava "concorrencia" em "concorr?ncia"
    nas paginas em Latin-1.
    """
    achado = re.search(r"charset=([\w-]+)", tipo or "", re.IGNORECASE)
    if achado:
        try:
            return conteudo.decode(achado.group(1))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252", errors="replace")


def links_de_saida(html: str, base: str) -> tuple[str, list[tuple[str, str]]]:
    """(titulo da pagina, [(url absoluta, texto)]) dos links para OUTROS sites,
    so os que estao no TEXTO PRINCIPAL da pagina.

    O texto principal sai da mesma extracao das fontes (trafilatura): menu,
    rodape, barra lateral e o que esta escondido ficam de fora. E ai que o link
    quebrado importa — e ai que o dono do site aceita trocar por outro.
    """
    import trafilatura

    documento = trafilatura.bare_extraction(
        html, url=base, include_links=True, favor_precision=True, with_metadata=True
    )
    if documento is None:
        return "", []
    titulo = " ".join((documento.title or "").split())[:300]
    if documento.body is None:
        return titulo, []

    proprio = _dominio(base)
    vistos, saida = set(), []
    for ref in documento.body.iter("ref"):
        href = (ref.get("target") or "").strip()
        url = urljoin(base, href).split("#", 1)[0]
        if not url.startswith(("http://", "https://")) or url in vistos:
            continue
        dominio = _dominio(url)
        if (
            not dominio
            or dominio == proprio
            or _sem_interesse(dominio)
            or _link_torto_do_proprio_site(dominio, proprio)
        ):
            continue
        vistos.add(url)
        saida.append((url, " ".join("".join(ref.itertext()).split())[:300]))
    return titulo, saida[:LINKS_POR_PAGINA]


def codigo_http(url: str) -> int | None:
    """O codigo HTTP do destino; 0 se o dominio sumiu; None se nao deu para saber."""
    from apps.knowledge.web import AGENTE, PaginaIndisponivel, conferir_destino

    try:
        conferir_destino(url)
    except PaginaIndisponivel:
        return None  # endereco interno ou proibido: nao e da nossa conta
    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": AGENTE}
        ) as cliente:
            resposta = cliente.head(url)
            if resposta.status_code in {403, 405, 501}:
                resposta = cliente.get(url)
    except httpx.ConnectError:
        # Dominio que nao resolve mais (ou servidor que sumiu): link morto.
        return 0
    except httpx.HTTPError:
        return None
    return resposta.status_code


def situacao_do_link(url: str) -> int | None:
    """O codigo HTTP se o link esta quebrado (404/410, ou 0 se o dominio sumiu)."""
    codigo = codigo_http(url)
    return codigo if codigo == 0 or codigo in QUEBRADO else None


def paginas_para_verificar(limite: int = PAGINAS_POR_VEZ) -> list[str]:
    """Paginas das buscas guardadas, de sites do assunto, ainda nao verificadas."""
    from apps.radar.concorrentes import _dominio_proprio, _ignorado
    from apps.radar.models import (
        ConcorrenteSugerido,
        ConfiguracaoDoRadar,
        PaginaVerificada,
        ResultadoOrganico,
    )

    proprio = _dominio_proprio()
    concorrentes = {c["dominio"] for c in ConfiguracaoDoRadar.carregar().lista_de_concorrentes}
    recusados = set(
        ConcorrenteSugerido.objects.filter(
            situacao=ConcorrenteSugerido.Situacao.RECUSADO
        ).values_list("dominio", flat=True)
    )
    recentes = set(
        PaginaVerificada.objects.filter(
            verificada_em__gte=timezone.now() - timezone.timedelta(days=REVERIFICAR_EM_DIAS)
        ).values_list("url", flat=True)
    )
    saida = []
    for url in (
        ResultadoOrganico.objects.order_by("-criado_em", "posicao")
        .values_list("url", flat=True)
        .distinct()[:2000]
    ):
        dominio = _dominio(url)
        if (
            url in recentes
            or url in saida
            or dominio in {proprio, *concorrentes, *recusados}
            or _ignorado(dominio)
        ):
            continue
        saida.append(url)
        if len(saida) >= limite:
            break
    return saida


def _relacionar(texto: str, url: str, artigos: list) -> tuple[object | None, float | None]:
    """(artigo publicado mais perto, proximidade com o tema) do texto do link."""
    from apps.radar.agrupamento import _distancia, _vetor
    from apps.radar.concorrentes import aderencia_da_consulta

    descricao = texto or urlparse(url).path.replace("-", " ").replace("/", " ")
    if len(descricao.split()) < 2:
        return None, None
    vetor = _vetor(descricao)
    melhor, menor = None, 1.0
    for artigo, vetor_do_artigo in artigos:
        distancia = _distancia(vetor, vetor_do_artigo)
        if distancia < menor:
            melhor, menor = artigo, distancia
    if melhor is not None and menor <= PERTO_DO_ARTIGO:
        return melhor, None
    return None, aderencia_da_consulta(descricao)


def verificar_pagina(url: str, artigos: list) -> int:
    """Confere os links de uma pagina. Devolve quantos quebrados guardou."""
    from apps.knowledge.web import PaginaIndisponivel, baixar
    from apps.radar.models import LinkQuebrado, PaginaVerificada

    try:
        conteudo, url_final, tipo = baixar(url)
    except PaginaIndisponivel as exc:
        PaginaVerificada.objects.update_or_create(
            url=url[:500], defaults={"erro": str(exc)[:300], "verificada_em": timezone.now()}
        )
        return 0
    if "html" not in (tipo or "html").lower():
        PaginaVerificada.objects.update_or_create(
            url=url[:500], defaults={"erro": "nao e HTML", "verificada_em": timezone.now()}
        )
        return 0
    titulo, links = links_de_saida(decodificar(conteudo, tipo), url_final)
    guardados = 0
    for link, texto in links:
        codigo = situacao_do_link(link)
        if codigo is None:
            continue
        artigo, proximidade = _relacionar(texto, link, artigos)
        if artigo is None and (proximidade or 0) < PERTO_DO_TEMA:
            continue
        _, criado = LinkQuebrado.objects.get_or_create(
            pagina_url=url[:500],
            link_url=link[:500],
            defaults={
                "pagina_titulo": titulo,
                "dominio": _dominio(url)[:255],
                "texto": texto,
                "status_http": codigo,
                "artigo": artigo,
                "proximidade": proximidade,
            },
        )
        guardados += criado
    PaginaVerificada.objects.update_or_create(
        url=url[:500], defaults={"links": len(links), "erro": "", "verificada_em": timezone.now()}
    )
    return guardados


def _artigos_publicados() -> list:
    from apps.content.models import Article
    from apps.radar.agrupamento import _vetor

    artigos = Article.objects.filter(status=Article.Status.PUBLISHED).exclude(published_url="")
    return [(a, _vetor(f"{a.title}. {a.focus_keyword}")) for a in artigos[:200]]


def verificar_um_lote() -> int:
    """Uma passada da tarefa de fundo, dentro do tenant."""
    from apps.radar.models import ConfiguracaoDoRadar

    if not ConfiguracaoDoRadar.carregar().procurar_links_quebrados:
        return 0
    paginas = paginas_para_verificar()
    if not paginas:
        return 0
    artigos = _artigos_publicados()
    return sum(verificar_pagina(url, artigos) for url in paginas)


def email(link) -> str:
    """O pedido de troca, curto, pronto para copiar."""
    substituto = link.artigo.published_url if link.artigo else "[o endereco do seu artigo]"
    return (
        f'Ola! Lendo "{link.pagina_titulo or link.pagina_url}" ({link.pagina_url}), '
        f'vi que o link "{link.texto or link.link_url}" aponta para {link.link_url}, '
        "que nao existe mais (a pagina da erro). Isso costuma atrapalhar o proprio "
        "texto no Google.\n\n"
        f"Tenho um conteudo sobre o mesmo assunto que pode substituir: {substituto}\n\n"
        "Fica a sugestao — e obrigado pelo material."
    )
