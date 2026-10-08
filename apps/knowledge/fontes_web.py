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
import re
import time
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


def _de_instituicao_confiavel(url: str) -> bool:
    """Link de instituicao que a curadoria do catalogo de dados marcou como
    confiavel. O catalogo mora no schema public: sem ele, nao."""
    try:
        from apps.dados.catalogo import link_confiavel

        return link_confiavel(url)
    except Exception:
        logger.debug("Catalogo de dados indisponivel para %s.", url)
        return False


def dispensar_da_pauta(candidato: CandidatoDeFonte, *, por=None, motivo: str = "") -> None:
    """ "Nao serve para esta pauta": sai da frente e nao volta NESTA pauta, mas
    nao e recusada — a busca de outra pauta que a achar a recebe."""
    candidato.situacao = CandidatoDeFonte.Situacao.FORA_DA_PAUTA
    fora_de = (candidato.metricas or {}).get("fora_de") or []
    if candidato.pauta_id and str(candidato.pauta_id) not in fora_de:
        fora_de = [*fora_de, str(candidato.pauta_id)]
    candidato.metricas = {**(candidato.metricas or {}), "fora_de": fora_de}
    candidato.motivo = motivo[:2000]
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()


def reaproveitar(url: str, pauta) -> CandidatoDeFonte | None:
    """A fonte que uma pauta dispensou ("nao serve para esta pauta") e que a
    busca de OUTRA pauta achou: passa para ela, esperando decisao. Nunca volta
    para uma pauta que ja a dispensou."""
    if pauta is None:
        return None
    candidato = CandidatoDeFonte.objects.filter(
        url=url, situacao=CandidatoDeFonte.Situacao.FORA_DA_PAUTA
    ).first()
    if candidato is None or str(pauta.pk) in ((candidato.metricas or {}).get("fora_de") or []):
        return None
    metricas = {
        k: v
        for k, v in (candidato.metricas or {}).items()
        if k not in ("frente", "lado", "sugestao_da_ia")
    }
    candidato.pauta = pauta
    candidato.situacao = CandidatoDeFonte.Situacao.PENDENTE
    candidato.metricas = metricas
    candidato.motivo = ""
    candidato.decidido_por = None
    candidato.decidido_em = None
    candidato.save()
    return candidato


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
    da_base = acervo.get("artigo", 0) + sum(esperando.values())
    falta = referencias.MINIMO_DE_ARTIGOS - da_base
    registro = {"em": timezone.now().isoformat(), "da_base": da_base, "ligados": ligados}
    novos: list = []
    if falta > 0 and consultas:
        try:
            for n, consulta in enumerate(consultas):
                if len(novos) >= falta:
                    break
                if n:
                    time.sleep(1.1)  # busca semantica: 1 por segundo no OpenAlex
                novos += buscar_para_pauta(pauta, limite=falta - len(novos), consulta=consulta)
            registro["consultas"] = referencias.acumular_consultas(pauta, "artigos", consultas)
        except BaseIndisponivel as exc:
            logger.warning("Pauta %s: artigos cientificos nao buscados: %s", pauta.pk, exc)
            registro["erro"] = str(exc)[:200]
    registro["novos"] = len(novos)
    registro["buscou"] = falta > 0
    referencias.registrar(pauta, "artigos", **registro)
    return novos


def _buscar_paginas(
    pauta, consultas: list[str], limite: int, *, papel: str = ""
) -> list[CandidatoDeFonte]:
    """`papel` DISCURSO: a busca do "o que se diz" (caixa de ideias). Ai nada
    entra sozinho no acervo (nem caminho APROVAR, nem provisoria), e o site
    bloqueado como fonte aparece mesmo assim: o discurso e justamente o que se
    diz por ai."""
    from apps.knowledge.provisorias import acolher_se_ligado
    from apps.knowledge.videos import tratar_como_video
    from apps.radar.models import ChamadaExterna
    from apps.radar.provedores import buscar

    discurso = papel == CandidatoDeFonte.Papel.DISCURSO
    novos: list[CandidatoDeFonte] = []
    for consulta in consultas:
        if len(novos) >= limite:
            break
        resultado = buscar(consulta, finalidade=ChamadaExterna.Finalidade.FONTES)
        for item in resultado.resultados:
            if len(novos) >= limite:
                break
            url = item.url[:500]
            if (reaproveitado := reaproveitar(url, pauta)) is not None:
                reaproveitado.papel = papel
                reaproveitado.save(update_fields=["papel"])
                novos.append(reaproveitado)
                continue
            if _ja_conhecida(url) or (bloqueada(url) and not discurso):
                continue
            caminho = caminho_de(url)
            candidato = tratar_como_video(
                CandidatoDeFonte.objects.create(
                    url=url,
                    titulo=item.titulo[:500],
                    trecho=item.trecho,
                    dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                    consulta=consulta[:500],
                    pauta=pauta,
                    preferido=confiavel(caminho),
                    papel=papel,
                )
            )
            if discurso:
                pass  # so a pessoa decide (ver `aprovar_como_discurso`)
            elif caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.APROVAR:
                aprovar(candidato, categoria=caminho.categoria, automatico=True)
            elif caminho is None and _de_instituicao_confiavel(url):
                # Instituicao de dados publicos marcada confiavel no catalogo
                # (IBGE, Banco Central...): entra como documento oficial.
                from apps.knowledge.perfis import categoria_da_natureza

                aprovar(candidato, categoria=categoria_da_natureza("normativo"), automatico=True)
            else:
                acolher_se_ligado(candidato)
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

    if candidato.papel == CandidatoDeFonte.Papel.DISCURSO:
        # A trava: discurso nunca vira documento (e a busca do acervo so le
        # documentos). Para usar como evidencia, a pessoa troca o papel antes.
        return aprovar_como_discurso(candidato, por=por)
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


def aprovar_como_discurso(candidato: CandidatoDeFonte, *, por=None) -> CandidatoDeFonte:
    """O "o que se diz": guarda o texto da pagina NO CANDIDATO, sem criar
    documento — fica fora do acervo e da busca, e so chega ao artigo da pauta
    dele, no bloco do debate, marcado como discurso. Video: titulo e descricao.
    Depois segue as citacoes da pagina (`seguir_citacoes`): a fonte primaria
    que ela cita vira sugestao de EVIDENCIA."""
    from apps.knowledge.web import PaginaIndisponivel, baixar, extrair_pagina

    candidato.papel = CandidatoDeFonte.Papel.DISCURSO
    html = b""
    if candidato.tipo == CandidatoDeFonte.Tipo.VIDEO:
        # O que o video DIZ esta na fala: a transcricao, quando ha legenda.
        # Sem ela, fica titulo e descricao (a tela avisa).
        from apps.knowledge.videos import verificar_legenda

        verificar_legenda(candidato)
    if candidato.tipo == CandidatoDeFonte.Tipo.PAGINA and not candidato.texto_extraido:
        try:
            html, final, _tipo = baixar(candidato.url)
            candidato.texto_extraido = extrair_pagina(html, url=final).markdown[:20000]
        except PaginaIndisponivel as exc:
            candidato.motivo = f"texto nao capturado: {exc}"[:2000]
    if not candidato.texto_extraido:
        candidato.texto_extraido = "\n".join(x for x in [candidato.titulo, candidato.trecho] if x)
    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = None
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    if html:
        seguir_citacoes(candidato, html)
    return candidato


# Links que costumam ser a fonte primaria por tras de uma materia.
_PRIMARIA = re.compile(
    r"\.pdf($|\?)|doi\.org/|/(estudo|pesquisa|relatorio|report|study|research|publicac|"
    r"survey|paper|dados|indicadores)",
    re.I,
)
MAXIMO_DE_CITACOES = 5


def seguir_citacoes(candidato: CandidatoDeFonte, html: bytes | str) -> list[CandidatoDeFonte]:
    """A investigacao mais barata que existe: a materia diz "segundo pesquisa
    X" e quase sempre linka a pesquisa. Os links do texto que parecem fonte
    primaria (PDF, DOI, pagina de estudo/relatorio, site de governo, de
    universidade ou de instituicao confiavel) viram sugestao de EVIDENCIA para
    a mesma pauta — para conferir se a materia leu certo. So algoritmo."""
    from urllib.parse import urljoin, urlparse

    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    origem = urlparse(candidato.url).hostname or ""
    achados: list[CandidatoDeFonte] = []
    vistos: set[str] = set()
    for href, rotulo in re.findall(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        url = urljoin(candidato.url, href.strip())[:500]
        anfitriao = urlparse(url).hostname or ""
        if not url.startswith("http") or anfitriao == origem or url in vistos:
            continue
        vistos.add(url)
        oficial = re.search(
            r"\.(gov|edu|ac|org)(\.[a-z]{2})?$", anfitriao
        ) or _de_instituicao_confiavel(url)
        if not (_PRIMARIA.search(url) or oficial):
            continue
        if (reaproveitado := reaproveitar(url, candidato.pauta)) is not None:
            achados.append(reaproveitado)
            continue
        if _ja_conhecida(url) or bloqueada(url):
            continue
        texto = re.sub(r"<[^>]+>|\s+", " ", rotulo).strip()
        achados.append(
            CandidatoDeFonte.objects.create(
                url=url,
                titulo=(texto or url)[:500],
                trecho=f"Citada por {candidato.dominio or origem}: {candidato.titulo}"[:1000],
                dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                consulta=f"citada em {candidato.url}"[:500],
                pauta=candidato.pauta,
                preferido=confiavel(caminho_de(url)),
                # Na caixa de ideias, a fonte primaria fica na frente do discurso que a citou.
                metricas={
                    k: v
                    for k, v in {
                        "frente": (candidato.metricas or {}).get("frente"),
                        "lado": "citada",
                        "citada_por": candidato.url,
                    }.items()
                    if v
                },
            )
        )
        if len(achados) >= MAXIMO_DE_CITACOES:
            break
    return achados


def voltar_a_sugestao(candidato: CandidatoDeFonte) -> CandidatoDeFonte:
    """Volta a ser sugestao, com todas as opcoes: o recusado (arrependimento) e
    o aprovado que ainda espera o PDF ou o audio (nada virou documento, entao
    nao ha o que desfazer no acervo). Bloqueio de site ou area, se houve, fica:
    ele se desfaz em Caminhos confiaveis."""
    candidato.situacao = CandidatoDeFonte.Situacao.PENDENTE
    candidato.motivo = ""
    candidato.decidido_por = None
    candidato.decidido_em = None
    candidato.save(update_fields=["situacao", "motivo", "decidido_por", "decidido_em"])
    return candidato


def recusar(
    candidato: CandidatoDeFonte, *, por=None, motivo: str = "", bloquear: str = ""
) -> CaminhoConfiavel | None:
    """Recusa a pagina. Com `bloquear` ("site" ou "caminho"), nada dali volta.

    "site" bloqueia o dominio inteiro; "caminho", a pasta da pagina
    (`exemplo.com/forum/tópico` -> `exemplo.com/forum`) — para o site que tem
    uma parte boa e outra nao.
    """
    from apps.knowledge.provisorias import descartar_resumo

    descartar_resumo(candidato)
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
