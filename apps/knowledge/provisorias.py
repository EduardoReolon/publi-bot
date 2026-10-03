"""Fontes provisorias: vetorizadas antes da curadoria, curadas quando usadas.

Curar tudo o que a busca acha nao escala: a maior parte nunca sera usada. Aqui
a fonte achada entra no indice logo — pagina com o texto principal, artigo
cientifico com o resumo do OpenAlex — marcada como NAO curada. A curadoria
passa a ser pedida so quando uma pauta for usar a fonte: a geracao para antes
de escrever e lista o que falta curar (`content.services.fontes_da_pauta`).
Recusada na curadoria, a fonte sai do indice e a pauta procura de novo.

So liga com um worker que vetoriza (`embeddings.conexao_de_vetorizacao`): num
servidor de 1 CPU, vetorizar tudo o que chega travaria o resto.
"""

from __future__ import annotations

import hashlib
import logging
import re

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.knowledge.models import CandidatoDeFonte, Document
from apps.knowledge.services import autores_para_citacao

logger = logging.getLogger("publibot.knowledge")

# Blocos que nao dizem nada sobre o assunto: nao vao para o indice provisorio.
BLOCOS_DE_FORA = re.compile(
    r"refer[eê]ncias|bibliografia|references|agradecimentos|acknowledg|notas de rodap",
    re.IGNORECASE,
)
CURADO = (Document.Status.CURATED, Document.Status.EMBEDDED)


def ligado() -> bool:
    from apps.knowledge.embeddings import conexao_de_vetorizacao

    return conexao_de_vetorizacao() is not None


def blocos_provisorios(documento: Document) -> set[int]:
    from apps.knowledge.blocos import preparar_blocos

    return {
        bloco.ordem
        for bloco in preparar_blocos(documento)
        if bloco.paragrafos and not BLOCOS_DE_FORA.search(bloco.titulo or "")
    }


def indexar(documento: Document, *, local: bool = False) -> bool:
    """Poe na fila a vetorizacao provisoria. Devolve se pediu."""
    from apps.knowledge.tasks import pedir_indexacao

    if (
        documento.status != Document.Status.PENDING_CURATION
        or documento.indexacao_pedida
        or documento.chunks.exists()
    ):
        return False
    blocos = blocos_provisorios(documento)
    if not blocos:
        return False
    pedir_indexacao(documento, blocos=blocos, concluir=False, por=None, local=local)
    return True


def documento_do_resumo(candidato: CandidatoDeFonte) -> Document | None:
    """O resumo do artigo cientifico vira documento, enquanto o PDF nao vem."""
    from apps.knowledge.academicos import autoridade
    from apps.knowledge.perfis import categoria_da_natureza

    if candidato.tipo != CandidatoDeFonte.Tipo.ARTIGO or len(candidato.trecho or "") < 200:
        return None
    if candidato.documento_id:
        return candidato.documento
    markdown = f"# {candidato.titulo}\n\n## Resumo\n\n{candidato.trecho}\n"
    bruto = markdown.encode("utf-8")
    sha = hashlib.sha256(bruto).hexdigest()
    documento = Document.objects.filter(file_sha256=sha).first() or Document.objects.create(
        category=categoria_da_natureza("cientifico"),
        original_file=ContentFile(bruto, name=f"resumo-{sha[:12]}.md"),
        file_sha256=sha,
        file_size_bytes=len(bruto),
        title=candidato.titulo[:500],
        authors=autores_para_citacao(candidato.autores)[:300],
        year=candidato.ano,
        source_url=candidato.url[:500],
        authority_score=autoridade(candidato.citacoes or 0),
        fetched_at=timezone.now(),
        origin=Document.Origin.WEB,
        markdown_full=markdown,
        extraction_method=Document.ExtractionMethod.RESUMO,
        status=Document.Status.PENDING_CURATION,
    )
    CandidatoDeFonte.objects.filter(pk=candidato.pk).update(documento=documento)
    candidato.documento = documento
    return documento


def descartar_resumo(candidato: CandidatoDeFonte) -> None:
    """Chegou o PDF (ou o artigo foi recusado): o documento do resumo sai."""
    documento = candidato.documento
    if documento is not None and documento.extraction_method == Document.ExtractionMethod.RESUMO:
        candidato.documento = None
        CandidatoDeFonte.objects.filter(pk=candidato.pk).update(documento=None)
        documento.delete()


def acolher(candidato: CandidatoDeFonte, *, local: bool = False) -> bool:
    """A fonte recem-achada entra no indice como provisoria. Devolve se entrou.

    Pagina: vai para o acervo (baixa, extrai o texto principal) e, convertida,
    e vetorizada (gancho em `flows.passo_converter`). Artigo: o resumo. Video
    nao: a legenda pede o YouTube, e video e buscado na hora da pauta.
    """
    if candidato.situacao != CandidatoDeFonte.Situacao.PENDENTE:
        return False
    if candidato.tipo == CandidatoDeFonte.Tipo.ARTIGO:
        documento = documento_do_resumo(candidato)
        return documento is not None and indexar(documento, local=local)
    if candidato.tipo == CandidatoDeFonte.Tipo.PAGINA:
        from apps.knowledge.fontes_web import aprovar, natureza_sugerida
        from apps.knowledge.perfis import categoria_da_natureza

        aprovado = aprovar(candidato, categoria=categoria_da_natureza(natureza_sugerida(candidato)))
        return aprovado.situacao == CandidatoDeFonte.Situacao.APROVADO
    return False


def acolher_se_ligado(candidato: CandidatoDeFonte) -> None:
    if not ligado():
        return
    try:
        acolher(candidato)
    except Exception:
        logger.exception("Fonte %s nao entrou como provisoria.", candidato.pk)


def por_curar(trechos) -> list[Document]:
    """Os documentos ainda nao curados entre os trechos que a pauta usaria."""
    vistos, saida = set(), []
    for trecho in trechos:
        chunk = trecho.chunk if hasattr(trecho, "chunk") else trecho
        documento = chunk.document
        if documento.pk not in vistos and documento.status not in CURADO:
            vistos.add(documento.pk)
            saida.append(documento)
    return saida


def recusar(documento: Document, *, por=None) -> None:
    """Recusada na curadoria: sai do indice e nao volta a ser sugerida (a URL
    continua conhecida). As pautas que esperavam sao conferidas de novo."""
    from apps.knowledge.tasks import ao_concluir_curadoria

    documento.chunks.all().delete()
    documento.status = Document.Status.REJECTED
    documento.reviewed_by = por
    documento.reviewed_at = timezone.now()
    documento.indexacao_pedida = {}
    documento.save(update_fields=["status", "reviewed_by", "reviewed_at", "indexacao_pedida"])
    CandidatoDeFonte.objects.filter(documento=documento).update(
        situacao=CandidatoDeFonte.Situacao.RECUSADO,
        decidido_por=por,
        decidido_em=timezone.now(),
        motivo="Recusada na curadoria.",
    )
    ao_concluir_curadoria()


def espaco(meses: int | None = None) -> dict:
    """Quanto o acervo ocupa, e quanto disso e nao curado.

    Com `meses`, quanto se ganharia apagando os nao curados criados ha mais que
    isso. O tamanho e estimado: arquivo original, texto convertido, texto dos
    trechos e o vetor de cada um (1024 floats).
    """
    import datetime

    from django.conf import settings
    from django.db.models import Count, Sum
    from django.db.models.functions import Length

    from apps.knowledge.models import SuperChunk

    por_vetor = settings.EMBEDDING_DIM * 4 + 8

    def tamanho(documentos) -> int:
        dados = documentos.aggregate(
            arquivos=Sum("file_size_bytes"), textos=Sum(Length("markdown_full"))
        )
        trechos = SuperChunk.objects.filter(document__in=documentos).aggregate(
            n=Count("id"), textos=Sum(Length("content"))
        )
        return (
            (dados["arquivos"] or 0)
            + (dados["textos"] or 0)
            + (trechos["textos"] or 0)
            + (trechos["n"] or 0) * por_vetor
        )

    todos = Document.objects.all()
    nao_curados = todos.exclude(status__in=CURADO)
    total, soltos = todos.count(), nao_curados.count()
    saida = {
        "total": total,
        "curados": total - soltos,
        "nao_curados": soltos,
        "porcentagem_nao_curada": round(100 * soltos / total) if total else 0,
        "bytes_total": tamanho(todos),
        "bytes_nao_curados": tamanho(nao_curados),
    }
    if meses:
        limite = timezone.now() - datetime.timedelta(days=30 * meses)
        antigos = apagaveis(limite)
        saida.update(meses=meses, antigos=antigos.count(), bytes_antigos=tamanho(antigos))
    return saida


def apagaveis(limite):
    """Nao curados criados antes de `limite`, que nao estao sendo processados."""
    return Document.objects.exclude(status__in=CURADO).filter(
        created_at__lt=limite, indexacao_pedida={}
    )
