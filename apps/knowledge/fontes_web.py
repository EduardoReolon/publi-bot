"""Fontes encontradas na web: busca, candidatos, confianca e aprovacao.

A web aqui e FORNECEDORA de fontes, nunca fonte direta. O que a busca acha
vira CANDIDATO; a escrita so usa o que passou por curadoria. A unica excecao
e deliberada e visivel: o caminho que a pessoa marcou como "aprovar
automaticamente".

A busca usa o mesmo buscador do radar (e o mesmo livro-caixa e teto). Os
caminhos marcados como PREFERIR entram como consultas restritas (`site:`),
antes da consulta aberta.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from django.utils import timezone

from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte, Document, DocumentCategory

logger = logging.getLogger("publibot.knowledge")

# Plataformas em que qualquer pessoa publica. Confiar no dominio inteiro seria
# confiar em todo usuario delas: a confianca ali precisa apontar um canal, um
# autor ou um subdominio proprio.
PLATAFORMAS_ABERTAS = (
    "youtube.com",
    "youtu.be",
    "medium.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "quora.com",
    "blogspot.com",
    "wordpress.com",
    "substack.com",
    "tumblr.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "pinterest.com",
    "sites.google.com",
    "github.io",
    "wikipedia.org",
)

# Editavel por qualquer um, sempre. Pode ser PREFERIDA (e boa para achar a
# referencia primaria), nunca aprovada sem curadoria.
NUNCA_APROVAR_SOZINHO = ("wikipedia.org",)


class CaminhoRecusado(ValueError):
    """O prefixo nao pode receber o nivel de confianca pedido."""


def normalizar_caminho(endereco: str) -> str:
    """`https://www.gov.br/caixa/sinapi/` -> `gov.br/caixa/sinapi`."""
    endereco = endereco.strip()
    if "//" not in endereco:
        endereco = f"https://{endereco}"
    partes = urlparse(endereco)
    anfitriao = (partes.hostname or "").lower().removeprefix("www.")
    caminho = partes.path.rstrip("/")
    return f"{anfitriao}{caminho}"


def _plataforma(anfitriao: str) -> str | None:
    for plataforma in PLATAFORMAS_ABERTAS:
        if anfitriao == plataforma or anfitriao.endswith(f".{plataforma}"):
            return plataforma
    return None


def conferir_caminho(prefixo: str, nivel: str) -> str:
    """Normaliza e confere o prefixo. Levanta `CaminhoRecusado`."""
    normalizado = normalizar_caminho(prefixo)
    anfitriao, _, caminho = normalizado.partition("/")
    if not anfitriao or "." not in anfitriao:
        raise CaminhoRecusado("informe um endereco, como gov.br/caixa/sinapi.")

    plataforma = _plataforma(anfitriao)
    if plataforma:
        # `fulano.blogspot.com` ja e o autor; `blogspot.com` sozinho nao.
        e_a_propria_plataforma = anfitriao == plataforma
        if e_a_propria_plataforma and not caminho:
            raise CaminhoRecusado(
                f"{plataforma} e aberto a qualquer usuario: confiar no dominio inteiro "
                f"seria confiar em todos eles. Aponte o canal ou o autor "
                f"(ex.: {plataforma}/@canal)."
            )
        if nivel == CaminhoConfiavel.Nivel.APROVAR and plataforma in NUNCA_APROVAR_SOZINHO:
            raise CaminhoRecusado(
                f"{plataforma} e editavel por qualquer pessoa, a qualquer momento: "
                f"pode ser preferido na busca, mas nao aprovado sem curadoria."
            )
    return normalizado


def caminho_de(url: str) -> CaminhoConfiavel | None:
    """O caminho confiavel mais especifico que cobre a URL, se houver.

    Casa por segmento, e nao por texto: `gov.br/caixa` cobre
    `gov.br/caixa/sinapi`, mas nao `gov.br/caixapreta`.
    """
    alvo = normalizar_caminho(url)
    melhor = None
    for caminho in CaminhoConfiavel.objects.all():
        prefixo = caminho.prefixo
        if (alvo == prefixo or alvo.startswith(f"{prefixo}/")) and (
            melhor is None or len(prefixo) > len(melhor.prefixo)
        ):
            melhor = caminho
    return melhor


def _ja_conhecida(url: str) -> bool:
    return (
        CandidatoDeFonte.objects.filter(url=url).exists()
        or Document.objects.filter(source_url=url).exists()
    )


def buscar_fontes(pauta, *, limite: int | None = None) -> list[CandidatoDeFonte]:
    """Busca paginas que podem sustentar a pauta e as registra como candidatas.

    Caminhos PREFERIR primeiro (com `site:`), depois a consulta aberta. URL ja
    vista — candidata, recusada ou documento — nao volta. Da caminho APROVAR,
    a pagina entra direto no acervo, sem curadoria.
    """
    from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar
    from apps.radar.provedores import buscar

    config = ConfiguracaoDoRadar.carregar()
    limite = limite or config.fontes_por_pauta
    termo = pauta.target_keyword or pauta.title

    consultas = [
        f"site:{c.prefixo} {termo}"
        for c in CaminhoConfiavel.objects.filter(nivel=CaminhoConfiavel.Nivel.PREFERIR)[:3]
    ]
    consultas.append(termo)

    novos: list[CandidatoDeFonte] = []
    for consulta in consultas:
        if len(novos) >= limite:
            break
        resultado = buscar(consulta, finalidade=ChamadaExterna.Finalidade.FONTES)
        for item in resultado.resultados:
            if len(novos) >= limite:
                break
            url = item.url[:500]
            if _ja_conhecida(url):
                continue
            caminho = caminho_de(url)
            candidato = CandidatoDeFonte.objects.create(
                url=url,
                titulo=item.titulo[:500],
                trecho=item.trecho,
                dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                consulta=consulta[:500],
                pauta=pauta,
                preferido=bool(caminho),
            )
            if caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.APROVAR:
                aprovar(candidato, categoria=caminho.categoria, automatico=True)
            novos.append(candidato)

    logger.info("Pauta %s: %s candidato(s) a fonte.", pauta.pk, len(novos))
    return novos


def aprovar(
    candidato: CandidatoDeFonte,
    *,
    categoria: DocumentCategory,
    por=None,
    automatico: bool = False,
) -> CandidatoDeFonte:
    """Busca a pagina e a manda para o acervo.

    Manual: segue para a conversao e depois para a curadoria, como qualquer
    documento. Automatico (caminho APROVAR): a conversao ja indexa tudo e
    conclui a curadoria.
    """
    from apps.knowledge.entradas import ingerir_url
    from apps.knowledge.tasks import iniciar_ingestao
    from apps.knowledge.web import PaginaIndisponivel

    try:
        resultado = ingerir_url(
            candidato.url,
            category=categoria,
            uploaded_by=por,
            origin=Document.Origin.WEB,
            iniciar=False,
        )
    except PaginaIndisponivel as exc:
        candidato.situacao = CandidatoDeFonte.Situacao.FALHOU
        candidato.motivo = str(exc)[:2000]
        candidato.decidido_por = por
        candidato.decidido_em = timezone.now()
        candidato.save()
        return candidato

    documento = resultado.document
    if not resultado.ja_existia:
        if automatico:
            Document.objects.filter(pk=documento.pk).update(auto_curate=True)
        iniciar_ingestao(documento)

    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.motivo = "aprovado por caminho confiavel" if automatico else ""
    candidato.save()
    return candidato


def recusar(candidato: CandidatoDeFonte, *, por=None, motivo: str = "") -> None:
    candidato.situacao = CandidatoDeFonte.Situacao.RECUSADO
    candidato.motivo = motivo[:2000]
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()


def curar_automaticamente(documento: Document) -> int:
    """Indexa todos os blocos e conclui a curadoria. So para caminho APROVAR."""
    from apps.knowledge.blocos import preparar_blocos
    from apps.knowledge.services import indexar_blocos, marcar_curado

    blocos = {bloco.ordem for bloco in preparar_blocos(documento)}
    criados = indexar_blocos(document=documento, blocos_marcados=blocos)
    marcar_curado(document=documento, revisado_por=None)
    return criados
