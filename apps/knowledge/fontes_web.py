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


COMUNIDADES = ("reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com")
SUFIXOS_OFICIAIS = (".gov.br", ".gov", ".jus.br", ".leg.br", ".mil.br", ".mp.br", ".def.br")


def natureza_sugerida(candidato) -> str:
    """A natureza que a tela pre-seleciona ao aprovar. A pessoa pode trocar.

    Antes era "veiculo" para tudo que nao fosse artigo, video inclusive — e o
    perfil da natureza decide como a fonte e citada e quanto tempo vale.
    """
    from apps.knowledge.models import CandidatoDeFonte

    if candidato.tipo == CandidatoDeFonte.Tipo.ARTIGO:
        return "cientifico"
    if candidato.tipo == CandidatoDeFonte.Tipo.VIDEO:
        return "video"
    anfitriao = (candidato.dominio or urlparse(candidato.url).hostname or "").lower()
    anfitriao = anfitriao.removeprefix("www.")
    if any(anfitriao == s.lstrip(".") or anfitriao.endswith(s) for s in SUFIXOS_OFICIAIS):
        return "normativo"
    if any(anfitriao == c or anfitriao.endswith(f".{c}") for c in COMUNIDADES):
        return "comunidade"
    return "veiculo"


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


def bloqueada(url: str) -> bool:
    """Se o caminho mais especifico que cobre a URL e BLOQUEAR."""
    caminho = caminho_de(url)
    return caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.BLOQUEAR


def confiavel(caminho: CaminhoConfiavel | None) -> bool:
    return caminho is not None and caminho.nivel != CaminhoConfiavel.Nivel.BLOQUEAR


def _ja_conhecida(url: str) -> bool:
    return (
        CandidatoDeFonte.objects.filter(url=url).exists()
        or Document.objects.filter(source_url=url).exists()
    )


def buscar_fontes(pauta, *, limite: int | None = None, variar: bool = False) -> list:
    """Busca o que pode sustentar a pauta e registra como candidatas.

    Primeiro confere o acervo (`referencias.no_acervo`). Artigos cientificos:
    so a falta ate `MINIMO_DE_ARTIGOS` vai ao OpenAlex, depois de contar os do
    acervo e os que as sementes cientificas ja trouxeram. Paginas: caminhos
    PREFERIR primeiro (com `site:`), depois a consulta aberta. URL ja vista nao
    volta; de caminho APROVAR, entra direto no acervo.

    `variar`: a busca de novo, com palavras ainda nao usadas
    (`referencias.outras_consultas`). Cada busca fica registrada na pauta.
    """
    from apps.knowledge import referencias
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    limite = limite or config.fontes_por_pauta
    termo = pauta.target_keyword or pauta.title
    acervo = referencias.conferir(pauta)
    outras = referencias.outras_consultas(pauta) if variar else None
    agora = timezone.now().isoformat()

    academicos: list[CandidatoDeFonte] = []
    ignorado = ((pauta.busca_de_fontes or {}).get("artigos") or {}).get("ignorado")
    if config.artigos_cientificos and not ignorado:
        academicos = _buscar_artigos(pauta, acervo, outras["artigos"] if outras else [termo])

    consultas = (
        outras["web"]
        if outras
        else [
            f"site:{c.prefixo} {termo}"
            for c in CaminhoConfiavel.objects.filter(nivel=CaminhoConfiavel.Nivel.PREFERIR)[:3]
        ]
        + [termo]
    )
    novos = _buscar_paginas(pauta, consultas, limite)
    referencias.registrar(
        pauta,
        "paginas",
        em=agora,
        consultas=referencias.acumular_consultas(pauta, "paginas", consultas),
        novos=len(novos),
        variada=variar,
    )
    logger.info(
        "Pauta %s: %s candidato(s) a fonte, %s artigo(s) cientifico(s).",
        pauta.pk,
        len(novos),
        len(academicos),
    )
    return academicos + novos


def _buscar_artigos(pauta, acervo: dict, consultas: list[str]) -> list:
    from apps.knowledge import referencias
    from apps.knowledge.academicos import BaseIndisponivel, buscar_para_pauta

    # Na base: os do acervo e os ja achados para a pauta que esperam a pessoa
    # (conferencia ou curadoria) — contando os que as sementes trouxeram.
    ligados = referencias.ligar_artigos_da_base(pauta)
    esperando = referencias.aguardando(pauta)["artigo"]
    da_base = acervo.get("artigo", 0) + esperando["conferencia"] + esperando["curadoria"]
    falta = referencias.MINIMO_DE_ARTIGOS - da_base
    registro = {"em": timezone.now().isoformat(), "da_base": da_base, "ligados": ligados}
    novos: list = []
    if falta > 0 and consultas:
        try:
            for consulta in consultas:
                if len(novos) >= falta:
                    break
                novos += buscar_para_pauta(pauta, limite=falta - len(novos), consulta=consulta)
            registro["consultas"] = referencias.acumular_consultas(pauta, "artigos", consultas)
        except BaseIndisponivel as exc:
            logger.warning("Pauta %s: artigos cientificos nao buscados: %s", pauta.pk, exc)
            registro["erro"] = str(exc)[:200]
    registro["novos"] = len(novos)
    registro["buscou"] = falta > 0
    referencias.registrar(pauta, "artigos", **registro)
    return novos


def _buscar_paginas(pauta, consultas: list[str], limite: int) -> list[CandidatoDeFonte]:
    from apps.radar.models import ChamadaExterna
    from apps.radar.provedores import buscar

    novos: list[CandidatoDeFonte] = []
    for consulta in consultas:
        if len(novos) >= limite:
            break
        resultado = buscar(consulta, finalidade=ChamadaExterna.Finalidade.FONTES)
        for item in resultado.resultados:
            if len(novos) >= limite:
                break
            url = item.url[:500]
            if _ja_conhecida(url) or bloqueada(url):
                continue
            caminho = caminho_de(url)
            candidato = CandidatoDeFonte.objects.create(
                url=url,
                titulo=item.titulo[:500],
                trecho=item.trecho,
                dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                consulta=consulta[:500],
                pauta=pauta,
                preferido=confiavel(caminho),
            )
            if caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.APROVAR:
                aprovar(candidato, categoria=caminho.categoria, automatico=True)
            novos.append(candidato)
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

    if candidato.tipo == CandidatoDeFonte.Tipo.VIDEO:
        from apps.knowledge.videos import aprovar_video

        return aprovar_video(candidato, categoria=categoria, por=por, automatico=automatico)
    if candidato.tipo == CandidatoDeFonte.Tipo.ARTIGO:
        from apps.knowledge.academicos import aprovar_artigo

        return aprovar_artigo(candidato, categoria=categoria, por=por, automatico=automatico)

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


def recusar(
    candidato: CandidatoDeFonte, *, por=None, motivo: str = "", bloquear: str = ""
) -> CaminhoConfiavel | None:
    """Recusa a pagina. Com `bloquear` ("site" ou "caminho"), nada dali volta.

    "site" bloqueia o dominio inteiro; "caminho", a pasta da pagina
    (`exemplo.com/forum/tópico` -> `exemplo.com/forum`) — para o site que tem
    uma parte boa e outra nao.
    """
    candidato.situacao = CandidatoDeFonte.Situacao.RECUSADO
    candidato.motivo = motivo[:2000]
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    if bloquear not in {"site", "caminho"}:
        return None
    endereco = normalizar_caminho(candidato.url)
    if candidato.tipo == CandidatoDeFonte.Tipo.VIDEO and candidato.dominio:
        # Num video, o "site" e o canal: bloquear o YouTube inteiro nao e o
        # que ninguem quer ao recusar um video.
        prefixo = candidato.dominio
    elif bloquear == "site":
        prefixo = endereco.split("/", 1)[0]
    else:
        prefixo = endereco.rsplit("/", 1)[0] if "/" in endereco else endereco
    caminho, _ = CaminhoConfiavel.objects.update_or_create(
        prefixo=prefixo[:300],
        defaults={
            "nivel": CaminhoConfiavel.Nivel.BLOQUEAR,
            "categoria": None,
            "observacao": (motivo or "bloqueado ao recusar uma sugestao")[:300],
            "criado_por": por,
        },
    )
    # O que ja estava esperando decisao, dali, sai da fila junto.
    for outro in CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.PENDENTE):
        if bloqueada(outro.url):
            outro.situacao = CandidatoDeFonte.Situacao.RECUSADO
            outro.motivo = f"caminho bloqueado: {prefixo}"
            outro.decidido_por = por
            outro.decidido_em = timezone.now()
            outro.save()
    return caminho


def curar_automaticamente(documento: Document) -> int:
    """Indexa todos os blocos e conclui a curadoria. So para caminho APROVAR."""
    from apps.knowledge.blocos import preparar_blocos
    from apps.knowledge.services import indexar_blocos, marcar_curado

    blocos = {bloco.ordem for bloco in preparar_blocos(documento)}
    criados = indexar_blocos(document=documento, blocos_marcados=blocos)
    marcar_curado(document=documento, revisado_por=None)
    return criados


def caminho_do_canal(canal_id: str) -> CaminhoConfiavel | None:
    """O caminho confiavel de um canal do YouTube, pelo id do canal."""
    if not canal_id:
        return None
    return CaminhoConfiavel.objects.filter(prefixo=f"youtube.com/channel/{canal_id}").first()
