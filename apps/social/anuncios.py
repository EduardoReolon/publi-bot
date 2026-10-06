"""O gasto com anuncios: pela API da Meta (sozinho) ou pela planilha (a mao).

Dois caminhos para o mesmo lugar (`Anuncio`), porque a permissao de ler
anuncios depende de uma revisao da Meta que pode nao sair (ou ser retirada):

* **API** — com "Conectar anuncios" feito, uma vez por dia o PubliBot le o
  gasto de cada anuncio (periodo maximo) e o post que ele impulsiona;
* **planilha** — o relatorio exportado (.csv ou .xlsx), em portugues ou
  ingles: do Gerenciador da Meta, do Campaign Manager do LinkedIn ou do
  Google Ads. O passo a passo de cada um esta na tela (`como_exportar`).
  LinkedIn e Google Ads so pela planilha: as APIs de anuncio deles pedem
  aprovacao a parte (Advertising API; token de desenvolvedor do Google Ads)
  que nao compensa para ler o gasto de uma conta pequena.

Depois de qualquer um, `vincular` liga cada anuncio ao post: pelo id do post
do Instagram (API) ou, na planilha, pelo comeco da legenda — o nome que a
Meta da ao impulso e "Publicacao do Instagram: <comeco da legenda>"; no
LinkedIn, o texto do anuncio patrocinado e o do post. O post ligado fica
como impulsionado, com o valor somado. Campanha do Google Ads nao e de um
post (anuncio de busca ou do mapa): entra no gasto da conta, sem post.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal

from django.db.models import Min, Sum
from django.utils import timezone

from apps.social.models import Anuncio, Destino, Post
from apps.social.planilhas import PlanilhaInvalida, campo, chave, data, inteiro, numero

logger = logging.getLogger("publibot.social")

COMO_EXPORTAR = [
    "Abra o Gerenciador de Anuncios da Meta (adsmanager.facebook.com), na conta de anuncios "
    "que impulsiona este Instagram.",
    "Va na aba 'Anuncios' (nao 'Campanhas'): o PubliBot liga cada ANUNCIO ao post.",
    "No seletor de datas, escolha 'Maximo' (todo o periodo).",
    "Clique em 'Relatorios' > 'Exportar dados da tabela' > formato .csv.",
    "Envie o arquivo aqui. Colunas que o PubliBot usa (se existirem): nome do anuncio, valor "
    "usado, alcance, impressoes, cliques no link, resultados, inicio e termino.",
    "Quando: uma vez por mes basta, ou quando o aviso de 'gasto sem atualizar' aparecer. "
    "Enviar de novo o mesmo periodo nao duplica: o anuncio e atualizado.",
]
_DE_NOVO = (
    "Quando: uma vez por mes basta. Enviar de novo o mesmo periodo nao duplica: o anuncio "
    "e atualizado."
)
COMO_EXPORTAR_POR_REDE = {
    "instagram": COMO_EXPORTAR,
    "linkedin": [
        "Abra o Campaign Manager do LinkedIn (linkedin.com/campaignmanager), na conta de "
        "anuncios da empresa.",
        "Va na aba 'Anuncios' (Ads), para o PubliBot ligar cada anuncio ao post pelo texto.",
        "No periodo, escolha o maior possivel (desde o primeiro anuncio).",
        "Clique em 'Exportar' > 'Desempenho do anuncio' (Ad performance) > .csv ou .xlsx.",
        "Envie o arquivo aqui. Colunas usadas: nome ou id do anuncio, campanha, texto de "
        "introducao, valor gasto (Total Spent), impressoes, cliques e data de inicio.",
        _DE_NOVO,
    ],
    "gmn": [
        "Abra o Google Ads (ads.google.com), na conta que anuncia a empresa.",
        "Va em 'Campanhas' e, no periodo, escolha 'Todo o periodo'.",
        "Clique no icone de download (Fazer download) > .csv.",
        "Envie o arquivo aqui. Colunas usadas: campanha, custo, impressoes, cliques e "
        "conversoes. Campanha do Google nao e de um post: entra como gasto da conta.",
        _DE_NOVO,
    ],
}


def como_exportar(rede: str) -> list[str]:
    return COMO_EXPORTAR_POR_REDE.get(rede, COMO_EXPORTAR)


# -- API ---------------------------------------------------------------------------------
def _resultados(acoes) -> int | None:
    """Soma das acoes que a Meta conta como resultado de engajamento/trafego."""
    if not acoes:
        return None
    contam = {"link_click", "post_engagement", "onsite_conversion.post_save", "like", "comment"}
    return int(sum(float(a.get("value") or 0) for a in acoes if a.get("action_type") in contam))


def sincronizar(destino: Destino, *, http=None) -> int:
    """Le o gasto pela API. Erro fica gravado no destino (o painel avisa)."""
    from apps.social.redes.base import ErroDaRede
    from apps.social.redes.meta_anuncios import AnunciosMeta

    if not destino.anuncios_conta_id:
        return 0
    api = AnunciosMeta(destino, http=http)
    try:
        linhas = api.gastos(destino.anuncios_conta_id)
        criativos = api.criativos([str(linha["ad_id"]) for linha in linhas if linha.get("ad_id")])
    except ErroDaRede as exc:
        destino.anuncios_erro = str(exc)[:2000]
        destino.save(update_fields=["anuncios_erro"])
        logger.info("Anuncios de %s nao lidos: %s", destino, exc)
        return 0
    for linha in linhas:
        ad_id = str(linha.get("ad_id") or "")
        if not ad_id:
            continue
        criativo = criativos.get(ad_id, {})
        Anuncio.objects.update_or_create(
            destino=destino,
            id_remoto=ad_id,
            defaults={
                "nome": str(linha.get("ad_name") or "")[:300],
                "campanha": str(linha.get("campaign_name") or "")[:300],
                "objetivo": str(linha.get("objective") or "")[:60],
                "gasto": numero(linha.get("spend")) or Decimal("0"),
                "alcance": inteiro(linha.get("reach")),
                "impressoes": inteiro(linha.get("impressions")),
                "cliques": inteiro(linha.get("inline_link_clicks")),
                "resultados": _resultados(linha.get("actions")),
                "inicio": data(linha.get("date_start")),
                "fim": data(linha.get("date_stop")),
                "media_id": criativo.get("media_id", "")[:120],
                "link": criativo.get("link", "")[:500],
                "imagem": criativo.get("imagem", "")[:1000],
                "texto": criativo.get("texto", "")[:3000],
                "origem": Anuncio.Origem.API,
            },
        )
    destino.anuncios_sincronizados_em = timezone.now()
    destino.anuncios_erro = ""
    destino.save(update_fields=["anuncios_sincronizados_em", "anuncios_erro"])
    vincular(destino)
    return len(linhas)


# -- Planilha ------------------------------------------------------------------------------
# Colunas dos relatorios (Meta, LinkedIn Campaign Manager, Google Ads), em
# portugues e ingles, sem acento e minusculas: nome igual, ou que comeca igual.
COLUNAS = {
    "id": ("identificacao do anuncio", "id do anuncio", "ad id", "creative id"),
    "nome": ("nome do anuncio", "ad name", "creative name", "anuncio"),
    "campanha": ("nome da campanha", "campaign name", "campanha", "campaign"),
    "texto": ("ad introduction text", "texto principal", "primary text", "texto do anuncio"),
    "gasto": ("valor usado", "valor gasto", "amount spent", "total spent", "custo", "cost"),
    "alcance": ("alcance", "reach"),
    "impressoes": ("impressoes", "impressions", "impr"),
    "cliques": ("cliques no link", "link clicks", "cliques", "clicks"),
    "resultados": ("resultados", "results", "conversoes", "conversions"),
    "inicio": (
        "inicio dos relatorios",
        "reporting starts",
        "start date in utc",
        "start date",
        "inicio",
        "starts",
        "dia",
        "day",
    ),
    "fim": ("termino dos relatorios", "reporting ends", "end date", "termino", "ends"),
}


def _aceita(mapa: dict) -> bool:
    return "gasto" in mapa and bool({"nome", "id", "campanha"} & set(mapa))


def importar_planilha(destino: Destino, conteudo: bytes) -> dict:
    """Le o relatorio exportado (.csv ou .xlsx) do Gerenciador da Meta, do
    Campaign Manager do LinkedIn ou do Google Ads. Devolve as contagens."""
    from apps.social.planilhas import em_ingles, ler, mapear, mes_primeiro, tabelas

    achadas = tabelas(ler(conteudo), COLUNAS, _aceita)
    if not achadas:
        raise PlanilhaInvalida(
            "nao achei as colunas do anuncio (ex.: 'Nome do anuncio' e 'Valor usado'; "
            "'Campaign Name' e 'Total Spent'; 'Campanha' e 'Custo'). Siga o passo a passo "
            "da tela."
        )
    lidos = 0
    for cabecalho, linhas in achadas:
        mapa = mapear(cabecalho, COLUNAS)
        ordem = mes_primeiro([campo(x, mapa, "inicio") for x in linhas], em_ingles(cabecalho))
        for linha in linhas:
            nome = campo(linha, mapa, "nome") or campo(linha, mapa, "campanha")
            gasto = numero(campo(linha, mapa, "gasto"))
            if gasto is None or not (nome or campo(linha, mapa, "id")):
                continue  # linha vazia
            if chave(nome).startswith("total"):
                continue  # linha de total (Google Ads: "Total: conta")
            inicio = data(campo(linha, mapa, "inicio"), mes_primeiro=ordem)
            id_remoto = campo(linha, mapa, "id") or f"planilha:{chave(nome)[:120]}:{inicio or ''}"
            Anuncio.objects.update_or_create(
                destino=destino,
                id_remoto=id_remoto[:200],
                defaults={
                    "nome": nome[:300],
                    "campanha": campo(linha, mapa, "campanha")[:300],
                    "texto": campo(linha, mapa, "texto")[:3000],
                    "gasto": gasto,
                    "alcance": inteiro(campo(linha, mapa, "alcance")),
                    "impressoes": inteiro(campo(linha, mapa, "impressoes")),
                    "cliques": inteiro(campo(linha, mapa, "cliques")),
                    "resultados": inteiro(campo(linha, mapa, "resultados")),
                    "inicio": inicio,
                    "fim": data(campo(linha, mapa, "fim"), mes_primeiro=ordem),
                    "origem": Anuncio.Origem.PLANILHA,
                },
            )
            lidos += 1
    destino.anuncios_planilha_em = timezone.now()
    destino.save(update_fields=["anuncios_planilha_em"])
    ligados = vincular(destino)
    return {"anuncios": lidos, "ligados": ligados}


# -- Ligar anuncio ao post -------------------------------------------------------------------
_PREFIXO_DO_IMPULSO = re.compile(
    r"^(publicacao do instagram|instagram post|post do instagram|impulsionamento)\s*", re.I
)


def _legenda_do_nome(nome: str) -> str:
    """'Publicacao do Instagram: Voce sente dor...' -> 'voce sente dor ...'."""
    return _PREFIXO_DO_IMPULSO.sub("", chave(nome)).strip()


def _parecidos(a: str, b: str) -> bool:
    if not a or not b:
        return False
    curto = min(len(a), len(b), 40)
    if curto >= 15 and a[:curto] == b[:curto]:
        return True
    pa, pb = set(a.split()), set(b.split())
    return len(pa) >= 4 and len(pa & pb) / len(pa | pb) >= 0.6


def vincular(destino: Destino) -> int:
    """Liga anuncios sem post ao post certo e recalcula o gasto de cada post."""
    posts = list(destino.posts.filter(situacao=Post.Situacao.PUBLICADO))
    por_id = {p.id_remoto: p for p in posts if p.id_remoto}
    legendas = [(p, chave(p.texto)) for p in posts]
    ligados = 0
    for anuncio in destino.anuncios.filter(post__isnull=True):
        post = por_id.get(anuncio.media_id) if anuncio.media_id else None
        if post is None:
            alvo = chave(anuncio.texto) or _legenda_do_nome(anuncio.nome)
            candidatos = [p for p, legenda in legendas if _parecidos(alvo, legenda)]
            post = candidatos[0] if len(candidatos) == 1 else None
        if post is not None:
            anuncio.post = post
            anuncio.save(update_fields=["post"])
            ligados += 1
    for post_id in set(destino.anuncios.exclude(post=None).values_list("post_id", flat=True)):
        soma = Anuncio.objects.filter(post_id=post_id).aggregate(
            gasto=Sum("gasto"), inicio=Min("inicio")
        )
        quando = (
            timezone.make_aware(datetime.combine(soma["inicio"], datetime.min.time()))
            if soma["inicio"]
            else timezone.now()
        )
        Post.objects.filter(pk=post_id).update(
            impulsionado=True, custo_impulso=soma["gasto"], impulso_em=quando
        )
    return ligados
