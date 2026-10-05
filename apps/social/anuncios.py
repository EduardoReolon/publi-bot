"""O gasto com anuncios: pela API da Meta (sozinho) ou pela planilha (a mao).

Dois caminhos para o mesmo lugar (`Anuncio`), porque a permissao de ler
anuncios depende de uma revisao da Meta que pode nao sair (ou ser retirada):

* **API** — com "Conectar anuncios" feito, uma vez por dia o PubliBot le o
  gasto de cada anuncio (periodo maximo) e o post que ele impulsiona;
* **planilha** — o relatorio que o Gerenciador de Anuncios exporta (.csv ou
  .xlsx salvo como .csv), em portugues ou ingles. O passo a passo esta na
  tela (`COMO_EXPORTAR`).

Depois de qualquer um, `vincular` liga cada anuncio ao post: pelo id do post
do Instagram (API) ou, na planilha, pelo comeco da legenda — o nome que a
Meta da ao impulso e "Publicacao do Instagram: <comeco da legenda>". O post
ligado fica como impulsionado, com o valor somado.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Min, Sum
from django.utils import timezone

from apps.social.models import Anuncio, Destino, Post

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


# -- Numeros e textos ---------------------------------------------------------------
def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )


def _chave(texto: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", _sem_acento(texto).lower()).split())


def numero(texto) -> Decimal | None:
    """1.234,56 · 1,234.56 · 1234.56 · R$ 12,00 · 12 -> Decimal."""
    texto = re.sub(r"[^\d,.\-]", "", str(texto or ""))
    if not texto or texto in {"-", ".", ","}:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _inteiro(texto) -> int | None:
    n = numero(texto)
    return int(n) if n is not None else None


def _data(texto) -> date | None:
    texto = str(texto or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


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
                "alcance": _inteiro(linha.get("reach")),
                "impressoes": _inteiro(linha.get("impressions")),
                "cliques": _inteiro(linha.get("inline_link_clicks")),
                "resultados": _resultados(linha.get("actions")),
                "inicio": _data(linha.get("date_start")),
                "fim": _data(linha.get("date_stop")),
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
# Colunas do relatorio da Meta, em portugues e ingles (sem acento, minusculo, comeco).
COLUNAS = {
    "id": ("identificacao do anuncio", "id do anuncio", "ad id"),
    "nome": ("nome do anuncio", "ad name"),
    "campanha": ("nome da campanha", "campaign name"),
    "gasto": ("valor usado", "valor gasto", "amount spent"),
    "alcance": ("alcance", "reach"),
    "impressoes": ("impressoes", "impressions"),
    "cliques": ("cliques no link", "link clicks"),
    "resultados": ("resultados", "results"),
    "inicio": ("inicio dos relatorios", "inicio", "reporting starts", "starts"),
    "fim": ("termino dos relatorios", "termino", "reporting ends", "ends"),
}


def _mapear(cabecalho: list[str]) -> dict[str, int]:
    chaves = [_chave(c) for c in cabecalho]
    saida = {}
    for campo, nomes in COLUNAS.items():
        for i, chave in enumerate(chaves):
            if any(chave.startswith(nome) for nome in nomes):
                saida.setdefault(campo, i)
                break
    return saida


class PlanilhaInvalida(ValueError):
    pass


def importar_planilha(destino: Destino, conteudo: bytes) -> dict:
    """Le o .csv exportado do Gerenciador de Anuncios. Devolve as contagens."""
    for codificacao in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PlanilhaInvalida("nao consegui ler o arquivo (use o .csv do Gerenciador).")
    try:
        dialeto = csv.Sniffer().sniff(texto[:4000], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    linhas = list(csv.reader(io.StringIO(texto), dialeto))
    if not linhas:
        raise PlanilhaInvalida("o arquivo esta vazio.")
    colunas = _mapear(linhas[0])
    if "gasto" not in colunas or not ({"nome", "id"} & set(colunas)):
        raise PlanilhaInvalida(
            "nao achei as colunas 'Nome do anuncio' e 'Valor usado'. Exporte pela aba "
            "'Anuncios' do Gerenciador."
        )

    def campo(linha, nome):
        i = colunas.get(nome)
        return linha[i].strip() if i is not None and i < len(linha) else ""

    lidos = 0
    for linha in linhas[1:]:
        nome = campo(linha, "nome")
        gasto = numero(campo(linha, "gasto"))
        if gasto is None or not (nome or campo(linha, "id")):
            continue  # linha de total ou vazia
        inicio = _data(campo(linha, "inicio"))
        id_remoto = campo(linha, "id") or f"planilha:{_chave(nome)[:120]}:{inicio or ''}"
        Anuncio.objects.update_or_create(
            destino=destino,
            id_remoto=id_remoto[:200],
            defaults={
                "nome": nome[:300],
                "campanha": campo(linha, "campanha")[:300],
                "gasto": gasto,
                "alcance": _inteiro(campo(linha, "alcance")),
                "impressoes": _inteiro(campo(linha, "impressoes")),
                "cliques": _inteiro(campo(linha, "cliques")),
                "resultados": _inteiro(campo(linha, "resultados")),
                "inicio": inicio,
                "fim": _data(campo(linha, "fim")),
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
    chave = _chave(nome)
    return _PREFIXO_DO_IMPULSO.sub("", chave).strip()


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
    legendas = [(p, _chave(p.texto)) for p in posts]
    ligados = 0
    for anuncio in destino.anuncios.filter(post__isnull=True):
        post = por_id.get(anuncio.media_id) if anuncio.media_id else None
        if post is None:
            alvo = _chave(anuncio.texto) or _legenda_do_nome(anuncio.nome)
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
