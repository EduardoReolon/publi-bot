"""O catalogo: achar a serie que combina com a pauta, e o valor dela.

* `sugerir`: proximidade (embedding) entre o texto da pauta e as series
  aprovadas — sem modelo de texto;
* `valor_atual`: pede ao adaptador da instituicao (se pronto) e grava; sem
  adaptador, usa o valor ja gravado (digitado a mao);
* `fato`: o dado pronto para o artigo, com a forma de citar e o link.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from urllib.parse import urlsplit

from django.utils import timezone

from apps.dados.adaptadores import AdaptadorPendente, adaptador_de
from apps.dados.models import Instituicao, Serie, Valor

logger = logging.getLogger("publibot.dados")

# Distancia do cosseno acima da qual a serie nao e sugerida (0 = igual).
DISTANCIA_MAXIMA = 0.35
# Mais exigente: abaixo disto a serie entra sozinha na pauta (ate MAXIMO_AUTOMATICOS).
DISTANCIA_AUTOMATICA = 0.2
MAXIMO_AUTOMATICOS = 3


def vetorizar_pendentes(limite: int = 200) -> int:
    """Vetoriza as series aprovadas que ainda nao tem vetor."""
    from apps.knowledge.embeddings import get_embedding_client

    series = list(
        Serie.objects.filter(situacao=Serie.Situacao.APROVADA, vetor__isnull=True).select_related(
            "instituicao"
        )[:limite]
    )
    if not series:
        return 0
    vetores = get_embedding_client().embed_passage([s.texto_para_busca for s in series])
    for serie, vetor in zip(series, vetores, strict=True):
        serie.vetor = vetor
        serie.save(update_fields=["vetor"])
    return len(series)


def sugerir(texto: str, *, limite: int = 5, excluir=()) -> list[Serie]:
    """As series aprovadas mais proximas do texto (titulo e orientacao da pauta)."""
    from pgvector.django import CosineDistance

    from apps.knowledge.embeddings import get_embedding_client

    if not texto.strip() or not Serie.objects.filter(situacao=Serie.Situacao.APROVADA).exists():
        return []
    try:
        vetorizar_pendentes()
        alvo = get_embedding_client().embed_query(texto)
    except Exception as exc:
        logger.warning("Sugestao de dados indisponivel: %s", exc)
        return []
    candidatas = (
        Serie.objects.filter(situacao=Serie.Situacao.APROVADA, vetor__isnull=False)
        .exclude(pk__in=list(excluir))
        .select_related("instituicao")
        .annotate(distancia=CosineDistance("vetor", alvo))
        .filter(distancia__lte=DISTANCIA_MAXIMA)
        .order_by("distancia")[:limite]
    )
    return list(candidatas)


def valor_atual(serie: Serie, local: str = "Brasil") -> Valor | None:
    """O valor mais recente: do adaptador (e grava), ou o ja gravado.
    Instituicao sem o recorte pedido (estado) devolve None: quem chama usa o Brasil."""
    from apps.dados.locais import normalizar

    local = normalizar(local)
    adaptador = adaptador_de(serie.instituicao)
    if adaptador is not None and adaptador.pronto:
        try:
            for obs in adaptador.valores(serie, local=local, ultimos=1):
                Valor.objects.update_or_create(
                    serie=serie,
                    local=obs.local,
                    periodo=obs.periodo,
                    defaults={"valor": obs.valor, "nota": obs.nota, "buscado_em": timezone.now()},
                )
        except AdaptadorPendente:
            pass
        except Exception as exc:
            logger.warning("Valor de %s nao atualizado: %s", serie, exc)
    return serie.valores.filter(local=local).order_by("-periodo").first()


def formatar(valor: Decimal) -> str:
    """Numero no jeito brasileiro, sem zeros sobrando: 25,9 · 1.234.567 · 0,05."""
    texto = format(Decimal(valor).normalize(), "f")
    inteiro, _, decimais = texto.partition(".")
    sinal = "-" if inteiro.startswith("-") else ""
    inteiro = inteiro.lstrip("-")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return sinal + ".".join(grupos) + ("," + decimais if decimais else "")


def formas_do_numero(valor: Decimal) -> set[str]:
    """Como o numero pode aparecer no texto: 1.234,5 · 1234,5 · 1234.5."""
    br = formatar(valor)
    return {br, br.replace(".", ""), br.replace(".", "").replace(",", ".")}


def fato(serie: Serie, local: str = "Brasil") -> dict | None:
    """O dado pronto para o artigo, ou None (sem valor nesse recorte)."""
    valor = valor_atual(serie, local)
    if valor is None:
        return None
    return {
        "serie_id": str(serie.pk),
        "instituicao": serie.instituicao.sigla,
        "titulo": serie.titulo,
        "unidade": serie.unidade,
        "local": valor.local,
        "periodo": valor.periodo,
        "valor": formatar(valor.valor),
        "valor_bruto": str(valor.valor),
        "url": serie.url or serie.instituicao.site,
        "buscado_em": valor.buscado_em.date().isoformat(),
    }


def instituicao_do_link(url: str) -> Instituicao | None:
    """A instituicao dona do link (por dominio, ou dominio/caminho)."""
    partes = urlsplit(url if "//" in url else f"//{url}")
    host = (partes.hostname or "").removeprefix("www.")
    caminho = host + (partes.path or "")
    for instituicao in Instituicao.objects.all():
        for dominio in instituicao.dominios:
            dominio = dominio.removeprefix("www.")
            if "/" in dominio:
                if caminho.startswith(dominio):
                    return instituicao
            elif host == dominio or host.endswith("." + dominio):
                return instituicao
    return None


def link_confiavel(url: str) -> bool:
    """Link de instituicao marcada como confiavel: dispensa a curadoria."""
    instituicao = instituicao_do_link(url)
    return bool(instituicao and instituicao.confiavel)
