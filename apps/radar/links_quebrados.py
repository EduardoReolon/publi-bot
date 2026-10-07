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
# Ate QUASE, com um termo do assunto em comum, o artigo vira sugestao; sem
# termo em comum, so ate SEM_TERMO_EM_COMUM. Sempre sugestao: quem liga e a pessoa.
QUASE_O_ARTIGO = 0.45
SEM_TERMO_EM_COMUM = 0.25
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


BLOCOS_DE_TEXTO = {"p", "item", "cell", "quote", "head", "list", "row"}
JANELA_DO_TRECHO = 350  # caracteres de cada lado do link


def _paragrafo(ref) -> str:
    """O paragrafo (ou item de lista, celula...) em que o link esta."""
    no = ref.getparent()
    while no is not None and no.tag not in BLOCOS_DE_TEXTO:
        no = no.getparent()
    alvo = no if no is not None else ref
    paragrafo = " ".join("".join(alvo.itertext()).split())
    if len(paragrafo) <= JANELA_DO_TRECHO * 2:
        return paragrafo
    # Paragrafo longo: a janela em volta do link, e nao o comeco dele.
    texto_do_link = " ".join("".join(ref.itertext()).split())
    meio = max(paragrafo.find(texto_do_link), 0) + len(texto_do_link) // 2
    inicio = max(0, meio - JANELA_DO_TRECHO)
    trecho = paragrafo[inicio : inicio + JANELA_DO_TRECHO * 2]
    return ("…" if inicio else "") + trecho + ("…" if inicio + len(trecho) < len(paragrafo) else "")


def links_de_saida(html: str, base: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(titulo da pagina, [(url absoluta, texto, paragrafo)]) dos links para
    OUTROS sites, so os que estao no TEXTO PRINCIPAL da pagina.

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
        saida.append((url, " ".join("".join(ref.itertext()).split())[:300], _paragrafo(ref)))
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
    from apps.radar.agrupamento import _vetor
    from apps.radar.concorrentes import aderencia_da_consulta

    descricao = texto or urlparse(url).path.replace("-", " ").replace("/", " ")
    if len(descricao.split()) < 2:
        return None, None
    melhor, menor = _mais_perto(_vetor(descricao), artigos)
    if melhor is not None and menor <= PERTO_DO_ARTIGO:
        return melhor, None
    return None, aderencia_da_consulta(descricao)


def _mais_perto(vetor, artigos: list) -> tuple[object | None, float]:
    """(artigo, distancia) do artigo publicado mais perto do vetor."""
    from apps.radar.agrupamento import _distancia

    melhor, menor = None, 2.0
    for artigo, vetor_do_artigo in artigos:
        distancia = _distancia(vetor, vetor_do_artigo)
        if distancia < menor:
            melhor, menor = artigo, distancia
    return melhor, menor


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
    for link, texto, contexto in links:
        codigo = situacao_do_link(link)
        if codigo is None:
            continue
        # Perto de um artigo seu ou do tema: guarda. Qual artigo serve sai
        # depois, com a descricao completa (`completar`), como sugestao.
        artigo, proximidade = _relacionar(texto, link, artigos)
        if artigo is None and (proximidade or 0) < PERTO_DO_TEMA:
            continue
        quebrado, criado = LinkQuebrado.objects.get_or_create(
            pagina_url=url[:500],
            link_url=link[:500],
            defaults={
                "pagina_titulo": titulo,
                "dominio": _dominio(url)[:255],
                "texto": texto,
                "contexto": contexto,
                "status_http": codigo,
                "proximidade": proximidade,
            },
        )
        if criado:
            completar(quebrado, artigos)
        guardados += criado
    PaginaVerificada.objects.update_or_create(
        url=url[:500], defaults={"links": len(links), "erro": "", "verificada_em": timezone.now()}
    )
    return guardados


ARQUIVO_CDX = "https://web.archive.org/cdx/search/cdx"
HISTORICOS_POR_VEZ = 5


def ultima_copia_boa(url: str) -> tuple[str, str] | None:
    """(timestamp, endereco original) da ultima copia com HTTP 200 no Internet
    Archive. A mais recente de todas costuma ser justamente a pagina de erro."""
    from apps.knowledge.web import AGENTE

    try:
        resposta = httpx.get(
            ARQUIVO_CDX,
            params={
                "url": url,
                "output": "json",
                "filter": "statuscode:200",
                "fl": "timestamp,original",
                "limit": "-1",
            },
            headers={"User-Agent": AGENTE},
            timeout=TIMEOUT * 2,
        )
        resposta.raise_for_status()
        linhas = resposta.json() if resposta.content.strip() else []
    except (httpx.HTTPError, ValueError):
        return None
    # A primeira linha e o cabecalho (["timestamp", "original"]).
    dados = [linha for linha in linhas[1:] if len(linha) == 2]
    return (dados[-1][0], dados[-1][1]) if dados else None


def consultar_arquivo(link) -> None:
    """Preenche o link com o que a pagina que sumiu era, pelo Internet Archive.

    Marca a consulta mesmo sem copia: nao se pergunta de novo a cada hora.
    """
    import datetime

    import trafilatura

    from apps.knowledge.web import PaginaIndisponivel, baixar

    link.arquivo_consultado_em = timezone.now()
    copia = ultima_copia_boa(link.link_url)
    if copia is not None:
        carimbo, original = copia
        link.arquivo_url = f"https://web.archive.org/web/{carimbo}/{original}"[:700]
        try:
            link.arquivo_data = datetime.datetime.strptime(carimbo[:8], "%Y%m%d").date()
        except ValueError:
            link.arquivo_data = None
        try:
            # `id_` pede a pagina original, sem a barra do Archive por cima.
            conteudo, _, tipo = baixar(f"https://web.archive.org/web/{carimbo}id_/{original}")
            html = decodificar(conteudo, tipo)
            metadados = trafilatura.extract_metadata(html, default_url=original)
            link.arquivo_titulo = " ".join((getattr(metadados, "title", "") or "").split())[:300]
            texto = trafilatura.extract(html, url=original, favor_precision=True) or ""
            link.arquivo_trecho = " ".join(texto.split())[:800]
        except PaginaIndisponivel:
            pass
    link.save(
        update_fields=[
            "arquivo_consultado_em",
            "arquivo_url",
            "arquivo_data",
            "arquivo_titulo",
            "arquivo_trecho",
        ]
    )


def completar_historicos(limite: int = HISTORICOS_POR_VEZ) -> int:
    """Os links ainda sem consulta ao Internet Archive ou sem procura de contato
    (os achados antes desses campos existirem)."""
    from django.db.models import Q

    from apps.radar.models import LinkQuebrado

    pendentes = list(
        LinkQuebrado.objects.filter(
            situacao__in=[LinkQuebrado.Situacao.NOVO, LinkQuebrado.Situacao.PAUTA]
        ).filter(Q(arquivo_consultado_em__isnull=True) | Q(contato_consultado_em__isnull=True))[
            :limite
        ]
    )
    artigos = _artigos_publicados() if pendentes else []
    for link in pendentes:
        completar(link, artigos)
    return len(pendentes)


def vetor_do_artigo(artigo):
    from apps.radar.agrupamento import _vetor

    return _vetor(f"{artigo.title}. {artigo.focus_keyword}")


def _artigos_publicados() -> list:
    from apps.content.models import Article

    artigos = Article.objects.filter(status=Article.Status.PUBLISHED).exclude(published_url="")
    return [(a, vetor_do_artigo(a)) for a in artigos[:200]]


def verificar_um_lote() -> int:
    """Uma passada da tarefa de fundo, dentro do tenant."""
    from apps.radar.models import ConfiguracaoDoRadar

    if not ConfiguracaoDoRadar.carregar().procurar_links_quebrados:
        return 0
    completar_historicos()
    conferir_conquistas()
    rechecar_links()
    recomparar_links()
    paginas = paginas_para_verificar()
    if not paginas:
        return 0
    artigos = _artigos_publicados()
    return sum(verificar_pagina(url, artigos) for url in paginas)


def orientacao_da_pauta(link) -> str:
    """A orientacao da pauta, com a origem de cada parte separada."""
    partes = [
        f'Link quebrado: {link.link_url} (texto do link: "{link.texto or "-"}").',
        "",
        f"ONDE O LINK ESTA — {link.pagina_titulo or link.pagina_url} ({link.pagina_url}):",
        f'"{link.contexto}"' if link.contexto else "(trecho nao guardado)",
        "",
    ]
    if link.arquivo_url:
        data = link.arquivo_data.strftime("%d/%m/%Y") if link.arquivo_data else "?"
        partes += [
            f"A PAGINA QUE SUMIU — copia do Internet Archive de {data} ({link.arquivo_url}):",
            f"Titulo: {link.arquivo_titulo or '-'}",
            f"Comeco do texto: {link.arquivo_trecho or '-'}",
        ]
    else:
        partes.append("A PAGINA QUE SUMIU: sem copia no Internet Archive.")
    partes += [
        "",
        "Um artigo que cubra o que essa pagina cobria, no contexto em que ela era "
        "citada, pode ocupar o lugar do link.",
    ]
    return "\n".join(partes)


# Fonte do numero citado no e-mail: estudo do Pew Research Center (maio de 2024)
# sobre paginas que somem da internet. Numero com fonte convence quem nao
# entende de SEO; numero inventado derruba o e-mail inteiro.
ESTUDO_DO_PEW = "https://www.pewresearch.org/data-labs/2024/05/17/when-online-content-disappears/"


def artigo_do_link(link):
    """O seu artigo que pode entrar no lugar: o que ja cobria, ou o da pauta, publicado."""
    if link.artigo_id:
        return link.artigo
    if link.pauta_id:
        from apps.content.models import Article

        return (
            Article.objects.filter(topic=link.pauta, status=Article.Status.PUBLISHED)
            .exclude(published_url="")
            .order_by("-published_at")
            .first()
        )
    return None


def email(link) -> str:
    """O pedido de troca: curto, para quem nao entende de SEO, com a proposta no inicio.

    Abre com o fato (ha um link quebrado no texto DELE), explica em duas frases
    por que isso importa, com um numero de fonte conhecida, e so entao propoe
    a troca, dizendo exatamente onde.
    """
    artigo = artigo_do_link(link)
    substituto = artigo.published_url if artigo else "[o endereço do seu artigo]"
    titulo_do_meu = f' — "{artigo.title}", que trata do mesmo assunto' if artigo else ""
    titulo = link.pagina_titulo or link.pagina_url
    trecho = f'\n\nO trecho é este: "{link.contexto[:280]}"' if link.contexto else ""
    # O endereco morto junto do texto: e por ele que o dono acha o link no editor.
    qual = f'"{link.texto}" ({link.link_url})' if link.texto else link.link_url
    return (
        f'Assunto: Link quebrado no seu artigo "{titulo}"\n\n'
        "Olá, tudo bem?\n\n"
        f'Encontrei um link quebrado no seu artigo "{titulo}": o link {qual} '
        "leva a uma página que não existe mais.\n\n"
        "Quem clica ali cai numa página de erro e costuma sair desconfiado do resto do "
        "texto. E o Google procura mostrar páginas úteis e atualizadas; link que não leva "
        "a lugar nenhum é justamente sinal de página desatualizada. Acontece com todo "
        "mundo: um estudo do Pew Research Center (2024) mostrou que um quarto das páginas "
        f"publicadas entre 2013 e 2023 já saiu do ar ({ESTUDO_DO_PEW}).\n\n"
        f"Uma sugestão que resolve em um minuto: trocar esse link por {substituto}"
        f"{titulo_do_meu}.{trecho}\n\n"
        f"Página: {link.pagina_url}\n\n"
        "Obrigado pelo conteúdo!"
    )


# -- Contato do site ------------------------------------------------------------
# So o que o proprio site publica como contato: link mailto e link de WhatsApp.
# E-mail solto no texto, formulario e "adivinhar contato@" ficam de fora: seria
# arbitrario, e e-mail errado queima a abordagem.
PAGINAS_DE_CONTATO = ("", "contato", "contato/", "fale-conosco", "fale-conosco/", "contact")
_MAILTO = re.compile(r'href=["\']mailto:([^"\'?]+)', re.IGNORECASE)
_WHATSAPP = re.compile(
    r'href=["\']https?://(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\+?\d{10,15})',
    re.IGNORECASE,
)


def contato_no_html(html: str, dominio: str) -> tuple[str, str]:
    """(e-mail, whatsapp) publicados na pagina; o e-mail do proprio dominio primeiro."""
    emails = [e.strip().lower() for e in _MAILTO.findall(html) if "@" in e]
    do_site = [e for e in emails if e.split("@", 1)[1].removeprefix("www.").endswith(dominio)]
    email_achado = (do_site or emails or [""])[0]
    whatsapp = next(iter(_WHATSAPP.findall(html)), "")
    return email_achado[:254], whatsapp[:30]


def procurar_contato(link) -> None:
    """Procura na propria pagina, na inicial e nas de contato. Um site por vez:
    se outro link do mesmo site ja achou, reaproveita."""
    from apps.knowledge.web import PaginaIndisponivel, baixar
    from apps.radar.models import LinkQuebrado

    link.contato_consultado_em = timezone.now()
    campos = ["contato_consultado_em", "contato_email", "contato_whatsapp", "contato_fonte"]
    ja_achado = (
        LinkQuebrado.objects.filter(dominio=link.dominio, contato_consultado_em__isnull=False)
        .exclude(pk=link.pk)
        .first()
    )
    if ja_achado is not None:
        link.contato_email = ja_achado.contato_email
        link.contato_whatsapp = ja_achado.contato_whatsapp
        link.contato_fonte = ja_achado.contato_fonte
        link.save(update_fields=campos)
        return

    raiz = f"{urlparse(link.pagina_url).scheme or 'https'}://{urlparse(link.pagina_url).netloc}/"
    for endereco in [link.pagina_url, *(raiz + caminho for caminho in PAGINAS_DE_CONTATO)]:
        try:
            conteudo, url_final, tipo = baixar(endereco)
        except PaginaIndisponivel:
            continue
        email_achado, whatsapp = contato_no_html(decodificar(conteudo, tipo), link.dominio)
        if email_achado or whatsapp:
            link.contato_email, link.contato_whatsapp = email_achado, whatsapp
            link.contato_fonte = url_final[:500]
            break
    link.save(update_fields=campos)


def descricao_do_link(link) -> str:
    """O que se sabe do assunto do link: titulo da pagina que sumiu, texto e trecho."""
    return " ".join(p for p in (link.arquivo_titulo, link.texto, link.contexto[:300]) if p)


def _termos(texto: str) -> set[str]:
    """Os termos que dizem o assunto, pelo comeco (5 letras): "obesidade" e
    "obesos" nao casam, "precificacao" e "precificar" sim. Simples de proposito."""
    from apps.knowledge.services import _PALAVRAS_VAZIAS, normalizar_para_impressao

    return {
        t[:5]
        for t in re.findall(r"[a-z0-9]+", normalizar_para_impressao(texto or ""))
        if len(t) >= 4 and t not in _PALAVRAS_VAZIAS and t not in TERMOS_GENERICOS
    }


# Palavras de link que nao dizem o assunto ("clique aqui", "saiba mais").
TERMOS_GENERICOS = frozenset(
    "aqui clique saiba mais veja leia confira acesse link pagina site artigo post "
    "blog guia texto materia sobre neste nesta este esta isso essa esse".split()
)


def _texto_do_artigo_para_termos(artigo) -> str:
    """Titulo, palavra-chave e intertitulos: o que o artigo diz que cobre."""
    intertitulos = [
        linha.lstrip("# ")
        for linha in (artigo.body_markdown or "").splitlines()
        if linha.startswith("#")
    ]
    return " ".join([artigo.title, artigo.focus_keyword or "", *intertitulos])


def termos_em_comum(link, artigo) -> list[str]:
    """Os termos que o assunto do link (titulo da pagina que sumiu e texto do
    link) divide com o artigo. Vazio: o artigo e so do mesmo tema geral."""
    do_link = _termos(f"{link.arquivo_titulo} {link.texto}")
    return sorted(do_link & _termos(_texto_do_artigo_para_termos(artigo)))


def _serve(link, artigo, distancia: float) -> bool:
    """Proximidade de texto sozinha engana: com um artigo so publicado, ele e o
    "mais perto" de tudo. Para sugerir, alem de perto, o artigo precisa dividir
    um termo do assunto com a pagina que sumiu; sem termo nenhum em comum, so
    se for muito perto."""
    if distancia <= SEM_TERMO_EM_COMUM:
        return True
    return distancia <= QUASE_O_ARTIGO and bool(termos_em_comum(link, artigo))


def assunto_do_link(link) -> str:
    """O assunto da pagina que sumiu, para achar o artigo que a substitui: o
    titulo e o comeco dela (Internet Archive) e o texto do link. O paragrafo do
    site que cita fala de muita coisa; entra so quando nao ha mais nada."""
    assunto = " ".join(
        p for p in (link.arquivo_titulo, link.texto, (link.arquivo_trecho or "")[:300]) if p
    )
    if len(assunto.split()) < 3:
        assunto = f"{assunto} {(link.contexto or '')[:300]}".strip()
    return assunto


def comparar_com_artigos(link, artigos: list | None = None, *, so_melhorar: bool = False) -> None:
    """Procura, entre os publicados, o artigo que pode entrar no lugar do link,
    com a descricao completa (titulo da pagina que sumiu, texto e trecho).

    Fica sempre como SUGESTAO (`artigo_parecido`, com a proximidade): quem liga
    o artigo ao link e a pessoa ("Serve"). Os que ela disse que nao servem nao
    voltam. `so_melhorar`: comparando so com um artigo novo, troca a sugestao
    apenas se ele for melhor.
    """
    from apps.radar.agrupamento import _vetor
    from apps.radar.models import LinkQuebrado

    if link.artigo_id or link.pauta_id or link.situacao != LinkQuebrado.Situacao.NOVO:
        return
    link.comparado_em = timezone.now()
    campos = ["artigo_parecido", "parecido_proximidade", "comparado_em"]
    descricao = assunto_do_link(link)
    if len(descricao.split()) < 2:
        link.save(update_fields=["comparado_em"])
        return
    if artigos is None:
        artigos = _artigos_publicados()
    recusados = {str(x) for x in link.artigos_recusados or []}
    vetor = _vetor(descricao)
    melhor, menor = _mais_perto(vetor, [(a, v) for a, v in artigos if str(a.pk) not in recusados])
    if melhor is not None and _serve(link, melhor, menor):
        proximidade = round(1 - menor, 2)
        if (
            not so_melhorar
            or link.parecido_proximidade is None
            or (proximidade > link.parecido_proximidade)
        ):
            link.artigo_parecido, link.parecido_proximidade = melhor, proximidade
    elif not so_melhorar:
        link.artigo_parecido, link.parecido_proximidade = None, None
    link.save(update_fields=campos)


def usar_artigo_parecido(link) -> None:
    """A pessoa confirmou: o artigo sugerido entra no lugar do link."""
    link.artigo, link.artigo_parecido, link.parecido_proximidade = (
        link.artigo_parecido,
        None,
        None,
    )
    link.save(update_fields=["artigo", "artigo_parecido", "parecido_proximidade"])


def nao_serve(link) -> None:
    """O artigo sugerido (ou ligado) nao serve para este link: nao volta a ser
    sugerido aqui, e o proximo mais perto que sirva vira a sugestao."""
    artigo = link.artigo_parecido or link.artigo
    if artigo is None:
        return
    link.artigos_recusados = [*(link.artigos_recusados or []), str(artigo.pk)]
    link.artigo = link.artigo_parecido = None
    link.parecido_proximidade = None
    link.save(
        update_fields=["artigos_recusados", "artigo", "artigo_parecido", "parecido_proximidade"]
    )
    comparar_com_artigos(link)


RECOMPARACOES_POR_VEZ = 20


def recomparar_links(limite: int = RECOMPARACOES_POR_VEZ) -> int:
    """Os links em aberto ainda nao comparados com a descricao completa (os
    antigos, de quando o artigo era ligado sozinho pelo texto do link)."""
    from apps.radar.models import LinkQuebrado

    pendentes = list(
        LinkQuebrado.objects.filter(
            situacao=LinkQuebrado.Situacao.NOVO,
            artigo__isnull=True,
            pauta__isnull=True,
            comparado_em__isnull=True,
        )[:limite]
    )
    if pendentes:
        artigos = _artigos_publicados()
        for link in pendentes:
            comparar_com_artigos(link, artigos)
    return len(pendentes)


def reavaliar_aderencia(link) -> None:
    """Com o titulo da pagina que sumiu e o trecho do artigo, a proximidade com o
    seu negocio sai bem melhor que so do texto do link ("plataforma EAD")."""
    if link.artigo_id:
        return
    from apps.radar.concorrentes import aderencia_da_consulta

    descricao = descricao_do_link(link)
    if len(descricao.split()) >= 2:
        link.proximidade = aderencia_da_consulta(descricao)
        link.save(update_fields=["proximidade"])


def completar(link, artigos: list | None = None) -> None:
    """Tudo o que se descobre depois de achar o link: o que a pagina era, o
    artigo seu que a substitui, a proximidade com o negocio e o contato do site."""
    consultar_arquivo(link)
    comparar_com_artigos(link, artigos)
    reavaliar_aderencia(link)
    procurar_contato(link)


RECHECAR_PRIMEIRO_EM_DIAS = 1
RECHECAR_A_CADA_DIAS = 7
RECHECAGENS_POR_VEZ = 10


def rechecar(link) -> bool:
    """Confere de novo se o link continua quebrado. Voltou a funcionar: sai da
    lista (descartado). Devolve se continua quebrado."""
    from apps.radar.models import LinkQuebrado

    codigo = situacao_do_link(link.link_url)
    link.rechecado_em = timezone.now()
    campos = ["rechecado_em"]
    if codigo is None:
        link.situacao = LinkQuebrado.Situacao.DESCARTADO
        campos.append("situacao")
    else:
        link.status_http = codigo
        campos.append("status_http")
    link.save(update_fields=campos)
    return codigo is not None


def rechecar_links(limite: int = RECHECAGENS_POR_VEZ) -> int:
    """Os links ainda em aberto, um dia depois de achados e depois de semana em
    semana. Devolve quantos voltaram a funcionar."""
    from django.db.models import Q

    from apps.radar.models import LinkQuebrado

    agora = timezone.now()
    devidos = LinkQuebrado.objects.filter(
        situacao__in=[LinkQuebrado.Situacao.NOVO, LinkQuebrado.Situacao.PAUTA]
    ).filter(
        Q(
            rechecado_em__isnull=True,
            encontrado_em__lt=agora - timezone.timedelta(days=RECHECAR_PRIMEIRO_EM_DIAS),
        )
        | Q(rechecado_em__lt=agora - timezone.timedelta(days=RECHECAR_A_CADA_DIAS))
    )
    return sum(not rechecar(link) for link in devidos.order_by("rechecado_em")[:limite])


def reprocessar(link) -> None:
    """Confere se o link continua quebrado e le a pagina de novo (trecho, titulo,
    texto do link), completando tudo de novo."""
    from apps.knowledge.web import PaginaIndisponivel, baixar

    if not rechecar(link):
        return

    try:
        conteudo, url_final, tipo = baixar(link.pagina_url)
        titulo, links = links_de_saida(decodificar(conteudo, tipo), url_final)
    except PaginaIndisponivel:
        titulo, links = "", []
    for url, texto, contexto in links:
        if url == link.link_url:
            link.texto, link.contexto = texto or link.texto, contexto
            break
    if titulo:
        link.pagina_titulo = titulo
    link.save(update_fields=["texto", "contexto", "pagina_titulo"])
    link.contato_consultado_em = None  # procura de novo, sem reaproveitar
    completar(link)


CONFERIR_DEPOIS_EM_DIAS = 7
CONFERENCIAS_POR_VEZ = 3


def conferir_conquistas(limite: int = CONFERENCIAS_POR_VEZ) -> int:
    """Depois do contato, a pagina e lida de novo de semana em semana: se o link
    do seu artigo apareceu nela, o link foi conquistado."""
    from apps.knowledge.web import PaginaIndisponivel, baixar
    from apps.radar.models import LinkQuebrado

    limite_de_data = timezone.now() - timezone.timedelta(days=CONFERIR_DEPOIS_EM_DIAS)
    candidatos = LinkQuebrado.objects.filter(situacao=LinkQuebrado.Situacao.CONTATADO).filter(
        models_q_conferir(limite_de_data)
    )[:limite]
    conquistados = 0
    for link in candidatos:
        link.conferido_depois_em = timezone.now()
        artigo = artigo_do_link(link)
        if artigo is not None and artigo.published_url:
            try:
                conteudo, _, tipo = baixar(link.pagina_url)
                if _endereco_sem_esquema(artigo.published_url) in decodificar(conteudo, tipo):
                    link.situacao = LinkQuebrado.Situacao.CONQUISTADO
                    link.conquistado_em = timezone.now()
                    conquistados += 1
            except PaginaIndisponivel:
                pass
        link.save(update_fields=["conferido_depois_em", "situacao", "conquistado_em"])
    return conquistados


def models_q_conferir(limite_de_data):
    from django.db.models import Q

    return Q(conferido_depois_em__isnull=True) | Q(conferido_depois_em__lt=limite_de_data)


def _endereco_sem_esquema(url: str) -> str:
    """O link pode estar com http ou https, com ou sem www."""
    partes = urlparse(url)
    return (partes.netloc.removeprefix("www.") + partes.path).rstrip("/")


# Abaixo disto, o artigo novo parece tratar de outra coisa que a pagina que
# sumiu. Distancia de cosseno entre o artigo e o que a pagina antiga cobria.
COBERTURA_BOA = 0.75
COBERTURA_FRACA = 0.6


def o_que_a_pagina_cobria(link) -> str:
    return " ".join(p for p in (link.arquivo_titulo, link.arquivo_trecho, link.contexto) if p)


def _proximidades_guardadas(artigo, texto_do_artigo: str, links) -> dict:
    """{link_id: proximidade} entre o artigo e o que cada pagina cobria.

    Vetorizar o artigo e cada link custa segundos numa CPU so, e a pagina do
    artigo mostra isto a cada abertura: guarda o resultado, refeito so quando o
    texto ou os links mudam.
    """
    import hashlib

    from django.core.cache import cache

    from apps.radar.agrupamento import _distancia, _vetor

    cobrias = {str(link.pk): o_que_a_pagina_cobria(link) for link in links}
    impressao = hashlib.sha256(
        (texto_do_artigo + "".join(f"{k}{v}" for k, v in sorted(cobrias.items()))).encode()
    ).hexdigest()[:20]
    chave = f"cobertura:{artigo.pk}:{impressao}"
    guardadas = cache.get(chave)
    if guardadas is not None:
        return guardadas
    try:
        vetor_do_artigo = _vetor(texto_do_artigo)
    except Exception:  # sem embedding, fica so o lembrete do titulo
        return {}
    saida = {}
    for pk, cobria in cobrias.items():
        if not cobria:
            continue
        try:
            saida[pk] = round(1.0 - _distancia(vetor_do_artigo, _vetor(cobria)), 2)
        except Exception:  # sem vetor para este link: fica so o lembrete do titulo
            logger.warning("Cobertura do link %s nao calculada.", pk, exc_info=True)
    cache.set(chave, saida, 60 * 60 * 24 * 30)
    return saida


def cobertura_do_artigo(artigo) -> list[dict]:
    """Para cada link quebrado da pauta: o artigo responde o que a pagina que
    sumiu respondia? Aviso, nao trava — quem decide e a pessoa.

    A conferencia e por proximidade de texto (o mesmo embedding do radar), e o
    titulo diferente do da pagina antiga so vira lembrete: e pelo titulo que o
    dono do site reconhece a troca.
    """
    if not artigo.topic_id:
        return []
    links = list(artigo.topic.links_quebrados.all())
    if not links:
        return []
    from django.utils.html import strip_tags

    corpo = artigo.body_markdown or strip_tags(artigo.body_html or "")
    texto_do_artigo = f"{artigo.title}\n{corpo[:3000]}"
    proximidades = _proximidades_guardadas(artigo, texto_do_artigo, links)
    saida = []
    for link in links:
        proximidade = proximidades.get(str(link.pk))
        titulo_antigo = link.arquivo_titulo or link.texto
        saida.append(
            {
                "link": link,
                "titulo_antigo": titulo_antigo,
                "titulo_diferente": bool(titulo_antigo)
                and titulo_antigo.strip().lower() != artigo.title.strip().lower(),
                "proximidade": proximidade,
                "fraca": proximidade is not None and proximidade < COBERTURA_FRACA,
                "boa": proximidade is not None and proximidade >= COBERTURA_BOA,
            }
        )
    return saida


def comparar_com_o_publicado(artigo) -> int:
    """Artigo recem-publicado: os links ainda sem artigo sao comparados com ele.
    Devolve quantos ganharam sugestao (ou trocaram por uma melhor)."""
    from apps.radar.models import LinkQuebrado

    if artigo.status != artigo.Status.PUBLISHED or not artigo.published_url:
        return 0
    so_ele = [(artigo, vetor_do_artigo(artigo))]
    mudaram = 0
    for link in LinkQuebrado.objects.filter(
        situacao=LinkQuebrado.Situacao.NOVO, artigo__isnull=True, pauta__isnull=True
    ):
        antes = (link.artigo_id, link.artigo_parecido_id)
        comparar_com_artigos(link, so_ele, so_melhorar=True)
        mudaram += antes != (link.artigo_id, link.artigo_parecido_id)
    return mudaram


def ofertas_do_artigo() -> dict:
    """{id do artigo: {"oferecido": n, "conquistado": n}} dos links ja contatados,
    para a tela avisar quando o mesmo artigo ja foi oferecido em outro lugar."""
    from apps.radar.models import LinkQuebrado

    contagem: dict = {}
    for link in LinkQuebrado.objects.filter(
        situacao__in=[LinkQuebrado.Situacao.CONTATADO, LinkQuebrado.Situacao.CONQUISTADO]
    ).select_related("artigo", "pauta"):
        artigo = artigo_do_link(link)
        if artigo is None:
            continue
        item = contagem.setdefault(artigo.pk, {"oferecido": 0, "conquistado": 0, "links": set()})
        item["oferecido"] += 1
        item["conquistado"] += link.situacao == LinkQuebrado.Situacao.CONQUISTADO
        item["links"].add(link.pk)
    return contagem


# Enviado ha mais que isto sem a troca: provavelmente nao vai acontecer.
PARADO_EM_DIAS = 45


def grupos() -> list[tuple[str, str, str]]:
    """(chave, titulo, explicacao) dos grupos da tela, na ordem de acao."""
    from django.utils.translation import gettext as _

    return [
        (
            "prontos",
            _("Prontos para enviar"),
            _("Seu artigo publicado entra no lugar: copie o e-mail e marque como enviado."),
        ),
        (
            "confirmar",
            _("Artigo candidato: confira"),
            _(
                "Um artigo seu parece cobrir o assunto. Diga se serve (vai para Prontos para "
                "enviar) ou nao serve (aparece o proximo candidato, se houver)."
            ),
        ),
        (
            "pauta",
            _("Viraram pauta: artigo a caminho"),
            _(
                "A pauta foi criada para o link; o e-mail fica pronto quando o artigo dela for "
                "publicado."
            ),
        ),
        (
            "sem_artigo",
            _("Sem artigo seu"),
            _("Nenhum artigo seu cobre o assunto. Vire pauta se valer a pena."),
        ),
        (
            "enviados",
            _("Enviados: esperando a troca"),
            _(
                "O PubliBot confere a pagina de semana em semana; quando o link do seu artigo "
                "aparece, vira conquistado."
            ),
        ),
        (
            "parados",
            _("Enviados ha mais de %(n)s dias sem troca") % {"n": PARADO_EM_DIAS},
            _(
                "Provavelmente nao vai acontecer. Tente outro contato (e marque de novo a data) "
                "ou descarte."
            ),
        ),
        ("conquistados", _("Links conquistados"), _("O site trocou o link pelo do seu artigo.")),
    ]


def grupo_do_link(link, hoje=None) -> str:
    """Em qual grupo da tela o link fica (`link.meu_artigo` ja calculado)."""
    from apps.radar.models import LinkQuebrado

    if link.situacao == LinkQuebrado.Situacao.CONQUISTADO:
        return "conquistados"
    if link.situacao == LinkQuebrado.Situacao.CONTATADO:
        hoje = hoje or timezone.localdate()
        parado = link.contatado_em and (hoje - link.contatado_em).days > PARADO_EM_DIAS
        return "parados" if parado else "enviados"
    if link.meu_artigo is not None:
        return "prontos"
    if link.pauta_id:
        return "pauta"
    if link.artigo_parecido_id:
        return "confirmar"
    return "sem_artigo"
