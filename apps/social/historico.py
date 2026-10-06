"""O passado da conta: posts de antes do PubliBot, com resultado e comentarios.

Ao conectar uma conta que ja existe, o PubliBot nao comeca do zero: importa os
posts (ate `MAXIMO`), o resultado de cada um (alcance, salvos,
compartilhamentos...) e os comentarios. Isso:

* calibra a regua da conta — "funcionou" e contra a mediana dela, e a mediana
  passa a ter historia desde o primeiro dia;
* alimenta o diagnostico (diagnostico.py): o que deu certo e errado antes;
* liga o gasto com anuncios aos posts que foram impulsionados (anuncios.py).

As APIs limitam chamadas por hora, entao a importacao anda em lotes: cada
rodada busca a lista inteira (barata) e o detalhe de ate `POR_RODADA` posts
que ainda nao tem; a medicao diaria continua de onde parou.

Comentarios antigos NAO viram Pergunta do PubliBot (a fila seria inundada de
perguntas de anos atras): ficam so para o diagnostico.
"""

from __future__ import annotations

import logging
from itertools import pairwise

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.social.models import Destino, Post
from apps.social.redes import rede
from apps.social.redes.base import ErroDaRede, NaoConectado, SemSuporte

logger = logging.getLogger("publibot.social")

MAXIMO = 500
POR_RODADA = 60


def suporta(destino: Destino) -> bool:
    """A rede entrega os posts antigos pela API (Instagram, pagina do LinkedIn,
    Perfil da Empresa no Google). Perfil pessoal do LinkedIn: so pela planilha."""
    r = rede(destino.rede)
    return r.publicador is not None and r.publicador.entrega_historico(destino)


def _primeira_linha(texto: str) -> str:
    return next((linha.strip() for linha in (texto or "").splitlines() if linha.strip()), "")[:200]


def _endereco(url: str) -> str:
    """O link do post sem consulta nem barra final (o "Ja postei" pode vir com ?igsh=)."""
    return (url or "").split("?", 1)[0].rstrip("/").lower()


def importar(destino: Destino, *, http=None, limite: int = MAXIMO) -> dict:
    """Uma rodada: a lista dos posts e o detalhe de um lote. Pode repetir.
    `limite` menor (a rodada diaria) so olha os posts mais novos."""
    from apps.social import comentarios
    from apps.social.estrategia import registrar_seguidores

    r = rede(destino.rede)
    if r.publicador is None or not destino.conectado:
        raise NaoConectado(f"{destino.nome}: conecte a conta para importar o historico.")
    publicador = r.publicador(destino, http=http)
    try:
        lista = publicador.historico(limite=limite)
    except SemSuporte:
        raise
    except ErroDaRede as exc:
        destino.erro_de_sincronia = str(exc)[:2000]
        destino.save(update_fields=["erro_de_sincronia"])
        raise
    # Ja no PubliBot: publicado pela API (id) ou marcado com "Ja postei" (link).
    conhecidos = set(destino.posts.exclude(id_remoto="").values_list("id_remoto", flat=True))
    ja_postados = destino.posts.exclude(url_remota="").values_list("url_remota", flat=True)
    links = {_endereco(u) for u in ja_postados}
    novos = []
    for item in lista:
        if item["id_remoto"] in conhecidos or _endereco(item.get("link", "")) in links:
            continue
        conhecidos.add(item["id_remoto"])
        novos.append(
            Post(
                destino=destino,
                motivo=Post.Motivo.HISTORICO,
                situacao=Post.Situacao.PUBLICADO,
                texto=item.get("legenda", "")[:5000],
                publicado_em=parse_datetime(item.get("publicado_em") or "") or timezone.now(),
                url_remota=item.get("link", "")[:500],
                id_remoto=item["id_remoto"][:300],
                extras={
                    "importado": True,
                    "formato": item.get("formato", ""),
                    "gancho": _primeira_linha(item.get("legenda", "")),
                },
                metricas={
                    k: item[k] for k in ("curtidas", "comentarios") if item.get(k) is not None
                },
            )
        )
    Post.objects.bulk_create(novos)

    # O detalhe (alcance, salvos...) e os comentarios, em lote.
    pendentes = list(
        destino.posts.filter(motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True).order_by(
            "-publicado_em"
        )[:POR_RODADA]
    )
    sem_detalhe = lidos = 0
    for post in pendentes:
        extras = dict(post.extras or {})
        try:
            post.metricas = {**(post.metricas or {}), **publicador.metricas(post)}
        except ErroDaRede as exc:
            # Post de antes da conta virar profissional, ou de formato sem
            # insights: fica com curtidas e comentarios da lista.
            extras["sem_detalhe"] = str(exc)[:200]
            sem_detalhe += 1
        if (post.metricas or {}).get("comentarios"):
            try:
                lidos += comentarios.ler(post, http=http, criar_perguntas=False)
            except ErroDaRede as exc:
                logger.info("Comentarios antigos de %s nao lidos: %s", post.pk, exc)
        extras["detalhado"] = True
        post.extras = extras
        post.save(update_fields=["metricas", "extras", "atualizado_em"])

    try:
        n = publicador.seguidores()
        if n is not None:
            registrar_seguidores(destino, n)
    except ErroDaRede:
        pass
    try:
        desempenho = publicador.desempenho()
        if desempenho:
            destino.desempenho = {**desempenho, "lido_em": timezone.now().isoformat()}
            destino.save(update_fields=["desempenho"])
    except ErroDaRede as exc:
        logger.info("Desempenho da conta %s nao lido: %s", destino, exc)
    faltam = destino.posts.filter(
        motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True
    ).count()
    destino.historico_importado_em = timezone.now()
    destino.sincronizado_em = timezone.now()
    destino.erro_de_sincronia = ""
    destino.save(update_fields=["historico_importado_em", "sincronizado_em", "erro_de_sincronia"])
    return {
        "posts": len(lista),
        "novos": len(novos),
        "detalhados": len(pendentes),
        "sem_detalhe": sem_detalhe,
        "comentarios": lidos,
        "faltam": faltam,
    }


# Na rodada diaria, sem lote pendente: so os posts mais novos (uma chamada),
# para entrar o que a pessoa postou direto na rede, fora do PubliBot.
NOVOS_POR_DIA = 25


def continuar(destino: Destino, *, http=None) -> None:
    """Na medicao diaria: segue a importacao (lote pendente) e traz os posts
    feitos fora do PubliBot desde ontem."""
    if destino.historico_importado_em is None or not suporta(destino):
        return
    pendente = destino.posts.filter(
        motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True
    ).exists()
    try:
        importar(destino, http=http, limite=MAXIMO if pendente else NOVOS_POR_DIA)
    except ErroDaRede as exc:
        logger.info("Historico de %s: %s", destino, exc)


# -- Pela planilha ---------------------------------------------------------------------------
# Para quem a API nao entrega o passado (perfil pessoal do LinkedIn; app ainda
# sem aprovacao) e como reforco: o arquivo que a propria rede exporta.
#
# * LinkedIn, perfil pessoal: Analytics > Impressoes do post > Exportar (.xlsx,
#   aba TOP POSTS: link, data, engajamentos e impressoes dos 50 melhores) e
#   Configuracoes > Privacidade > Obter uma copia dos dados > Shares.csv (o
#   texto de todos os posts, sem numeros);
# * LinkedIn, pagina: Analytics > Publicacoes > Exportar (.xlsx: texto, link,
#   data, impressoes, cliques, reacoes, comentarios, compartilhamentos);
# * Instagram/Facebook: Meta Business Suite > Insights > Conteudo > Exportar.
#
# O mesmo post pode vir em dois arquivos (texto num, numeros no outro): junta
# pelo link e, sem link igual (o LinkedIn usa ids diferentes em cada um), pelo
# dia, quando so ha um post naquele dia.
COLUNAS_DE_POST = {
    "id": ("identificacao da publicacao", "post id", "id da publicacao"),
    "link": (
        "post url",
        "update link",
        "sharelink",
        "permalink",
        "link permanente",
        "link do post",
        "url do post",
        "link",
    ),
    "data": (
        "post publish date",
        "created date",
        "publish time",
        "horario de publicacao",
        "data de publicacao",
        "date",
        "data",
    ),
    "texto": (
        "update title",
        "sharecommentary",
        "post commentary",
        "descricao",
        "description",
        "legenda",
        "texto",
    ),
    "formato": ("content type", "post type", "update type", "tipo de publicacao", "tipo"),
    "alcance": ("alcance", "reach", "unique impressions"),
    "impressoes": ("impressions", "impressoes", "visualizacoes", "views"),
    "engajamentos": ("engagements", "engajamentos"),
    "curtidas": ("likes", "reactions", "curtidas", "reacoes"),
    "comentarios": ("comments", "comentarios"),
    "compartilhamentos": ("reposts", "shares", "compartilhamentos"),
    "salvos": ("saves", "salvamentos", "salvos"),
    "cliques_na_rede": ("clicks", "cliques"),
}
METRICAS_DA_PLANILHA = (
    "alcance",
    "impressoes",
    "engajamentos",
    "curtidas",
    "comentarios",
    "compartilhamentos",
    "salvos",
    "cliques_na_rede",
)


def _formato(texto: str) -> str:
    """O tipo que a planilha diz -> o codigo usado no diagnostico."""
    t = (texto or "").lower()
    for pedacos, codigo in (
        (("carrossel", "carousel", "multi", "album"), "CAROUSEL_ALBUM"),
        (("reel", "video", "vídeo"), "VIDEO"),
        (("document", "documento", "pdf"), "DOCUMENT"),
        (("article", "artigo", "link"), "ARTICLE"),
        (("imag", "photo", "foto"), "IMAGE"),
        (("text", "texto"), "TEXT"),
    ):
        if any(p in t for p in pedacos):
            return codigo
    return ""


def _aceita_post(mapa: dict) -> bool:
    numeros = set(mapa) & set(METRICAS_DA_PLANILHA)
    return (
        ("link" in mapa or "id" in mapa) and ("data" in mapa) and bool(numeros or "texto" in mapa)
    )


def _blocos(cabecalho: list[str]) -> list[tuple[int, int]]:
    """Tabelas lado a lado (TOP POSTS do LinkedIn: duas, cada uma com seu
    'Post URL'): cada bloco vai de um link ate o proximo."""
    from apps.social.planilhas import chave

    nomes = COLUNAS_DE_POST["link"]
    inicios = [i for i, c in enumerate(cabecalho) if chave(c) in nomes]
    if len(inicios) < 2:
        return [(0, len(cabecalho))]
    inicios[0] = 0
    return list(pairwise([*inicios, len(cabecalho)]))


def _linhas_de_post(conteudo: bytes) -> list[dict]:
    from apps.social.planilhas import (
        PlanilhaInvalida,
        campo,
        em_ingles,
        inteiro,
        ler,
        mapear,
        mes_primeiro,
        momento,
        tabelas,
    )

    achadas = []
    for cabecalho, linhas in tabelas(ler(conteudo), COLUNAS_DE_POST, _aceita_post):
        for de, ate in _blocos(cabecalho):
            mapa = mapear(cabecalho[de:ate], COLUNAS_DE_POST)
            if _aceita_post(mapa):
                achadas.append((cabecalho[de:ate], mapa, [x[de:ate] for x in linhas]))
    if not achadas:
        raise PlanilhaInvalida(
            "nao achei as colunas de post (link ou id, data e numeros ou texto). Use o "
            "arquivo exportado como explica a tela."
        )
    saida = []
    for cabecalho, mapa, linhas in achadas:
        ordem = mes_primeiro([campo(x, mapa, "data") for x in linhas], em_ingles(cabecalho))
        for linha in linhas:
            quando = momento(campo(linha, mapa, "data"), mes_primeiro=ordem)
            link = campo(linha, mapa, "link")
            ident = campo(linha, mapa, "id")
            if quando is None or not (link or ident):
                continue  # linha vazia, de total ou de explicacao
            metricas = {
                k: n
                for k in METRICAS_DA_PLANILHA
                if (n := inteiro(campo(linha, mapa, k))) is not None
            }
            saida.append(
                {
                    "id": ident,
                    "link": link if link.startswith("http") else "",
                    "quando": timezone.make_aware(quando) if timezone.is_naive(quando) else quando,
                    "texto": campo(linha, mapa, "texto"),
                    "formato": _formato(campo(linha, mapa, "formato")),
                    "metricas": metricas,
                }
            )
    return saida


def _juntar(item: dict, post: Post) -> bool:
    """Completa o post com o que a linha traz. True se mudou algo."""
    mudou = False
    if item["metricas"]:
        novas = {**(post.metricas or {}), **item["metricas"]}
        if novas != post.metricas:
            post.metricas, mudou = novas, True
    if item["texto"] and not post.texto:
        post.texto, mudou = item["texto"][:5000], True
        post.extras = {**(post.extras or {}), "gancho": _primeira_linha(item["texto"])}
    if item["link"] and not post.url_remota:
        post.url_remota, mudou = item["link"][:500], True
    if item["formato"] and not (post.extras or {}).get("formato"):
        post.extras = {**(post.extras or {}), "formato": item["formato"]}
        mudou = True
    return mudou


def importar_planilha(destino: Destino, conteudo: bytes) -> dict:
    """Os posts de antes do PubliBot pelo arquivo que a rede exporta. Enviar de
    novo (ou outro arquivo do mesmo periodo) atualiza, sem duplicar."""
    from apps.social.anuncios import vincular

    itens = _linhas_de_post(conteudo)
    posts = list(destino.posts.filter(situacao=Post.Situacao.PUBLICADO))
    por_id = {p.id_remoto: p for p in posts if p.id_remoto}
    por_link = {_endereco(p.url_remota): p for p in posts if p.url_remota}
    novos = atualizados = 0
    mudados: dict = {}
    for item in itens:
        id_remoto = item["id"] or (
            f"link:{_endereco(item['link'])}"
            if item["link"]
            else f"planilha:{item['quando']:%Y%m%d%H%M}"
        )
        post = por_id.get(id_remoto)
        post = post or (por_link.get(_endereco(item["link"])) if item["link"] else None)
        if post is None:
            # Texto num arquivo e numeros no outro: o unico post do dia que falta a parte.
            dia = timezone.localdate(item["quando"])
            falta = "metricas" if item["metricas"] else "texto"
            candidatos = [
                p
                for p in posts
                if p.publicado_em
                and timezone.localdate(p.publicado_em) == dia
                and not getattr(p, falta)
            ]
            mesmo_dia = [
                p for p in posts if p.publicado_em and timezone.localdate(p.publicado_em) == dia
            ]
            post = candidatos[0] if len(candidatos) == 1 and len(mesmo_dia) == 1 else None
        if post is not None:
            if _juntar(item, post):
                mudados[post.pk] = post
            continue
        post = Post.objects.create(
            destino=destino,
            motivo=Post.Motivo.HISTORICO,
            situacao=Post.Situacao.PUBLICADO,
            texto=item["texto"][:5000],
            publicado_em=item["quando"],
            url_remota=item["link"][:500],
            id_remoto=id_remoto[:300],
            # Sem detalhe a buscar na API: o que a planilha tem e o que ha.
            extras={
                "importado": True,
                "planilha": True,
                "detalhado": True,
                "formato": item["formato"],
                "gancho": _primeira_linha(item["texto"]),
            },
            metricas=item["metricas"],
        )
        posts.append(post)
        por_id[post.id_remoto] = post
        if post.url_remota:
            por_link[_endereco(post.url_remota)] = post
        novos += 1
    for post in mudados.values():
        post.save(update_fields=["metricas", "texto", "url_remota", "extras", "atualizado_em"])
        atualizados += 1
    destino.historico_planilha_em = timezone.now()
    destino.save(update_fields=["historico_planilha_em"])
    vincular(destino)  # anuncios ja enviados podem achar o post agora
    return {"linhas": len(itens), "novos": novos, "atualizados": atualizados}


def como_exportar(destino: Destino) -> list[str]:
    """O passo a passo do arquivo de posts, para a rede deste destino."""
    if destino.rede == "linkedin" and destino.autor == "organizacao":
        return [
            "Na pagina da empresa no LinkedIn, abra 'Analytics' > 'Publicacoes' (Updates).",
            "Escolha o maior periodo possivel e clique em 'Exportar' (.xls/.xlsx).",
            "Envie o arquivo aqui: texto, link, data, impressoes, cliques, reacoes, "
            "comentarios e compartilhamentos de cada post.",
        ]
    if destino.rede == "linkedin":
        return [
            "Numeros: no seu perfil, 'Analytics' (ou 'Impressoes do post') > escolha 'Ultimos "
            "365 dias' > 'Exportar'. O .xlsx traz os 50 posts com mais engajamento e os 50 com "
            "mais impressoes.",
            "Textos (opcional): 'Configuracoes' > 'Privacidade de dados' > 'Obter uma copia dos "
            "seus dados' > marque 'Compartilhamentos' (Shares). Chega por e-mail em minutos; "
            "envie o Shares.csv de dentro do .zip.",
            "Envie um, outro ou os dois (em qualquer ordem): o PubliBot junta o texto aos "
            "numeros do mesmo post.",
        ]
    if destino.rede == "instagram":
        return [
            "Meta Business Suite (business.facebook.com) > 'Insights' > 'Conteudo'.",
            "Escolha o periodo, filtre por Instagram e clique em 'Exportar dados' (.csv).",
            "Envie o arquivo aqui. Com a conta conectada, a API ja traz isso sozinha: a "
            "planilha so serve para posts que a API nao alcanca.",
        ]
    return [
        "Exporte da rede um arquivo (.csv ou .xlsx) com uma linha por post e as colunas: "
        "link (ou id), data, texto e os numeros (alcance, curtidas, comentarios...).",
    ]
