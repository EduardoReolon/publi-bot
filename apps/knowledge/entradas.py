"""Formas de entrada no acervo alem do arquivo enviado.

* **Pagina por URL** — alguem cola o endereco; o sistema busca, guarda a copia
  e manda para a mesma conversao e a mesma curadoria de um arquivo.
* **Nota do especialista** — a pessoa escreve o que sabe. Nao e "gerar sem
  fonte": a fonte e a experiencia dela, atribuida a ela no texto publicado. E
  a unica entrada que ja nasce curada, porque quem escreveu e quem curaria.
"""

from __future__ import annotations

import hashlib
import logging
import re

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.knowledge.models import Document
from apps.knowledge.services import ResultadoDeIngestao
from apps.knowledge.web import baixar

logger = logging.getLogger("publibot.knowledge")


def _nome_do_arquivo(url: str, tipo: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", url.lower().split("//", 1)[-1])[:60].strip("-") or "pagina"
    if "pdf" in tipo:
        return f"{base}.pdf"
    return f"{base}.html"


def ingerir_url(
    url: str,
    *,
    category,
    uploaded_by=None,
    origin: str = Document.Origin.URL,
    iniciar: bool = True,
) -> ResultadoDeIngestao:
    """Busca a pagina e a registra como documento, deduplicando.

    Dedup em duas camadas: a mesma URL ja no acervo devolve o documento
    existente sem nem buscar; o mesmo conteudo em outra URL (republicacao,
    endereco com rastreador) e pego pelo hash do arquivo, como num envio.
    """
    from apps.knowledge.tasks import iniciar_ingestao

    existente = Document.objects.filter(source_url=url).first()
    if existente is not None:
        return ResultadoDeIngestao(document=existente, ja_existia=True)

    conteudo, url_final, tipo = baixar(url)
    sha = hashlib.sha256(conteudo).hexdigest()

    existente = Document.objects.filter(file_sha256=sha).first()
    if existente is not None:
        return ResultadoDeIngestao(document=existente, ja_existia=True)

    documento = Document.objects.create(
        category=category,
        original_file=ContentFile(conteudo, name=_nome_do_arquivo(url_final, tipo)),
        file_sha256=sha,
        file_size_bytes=len(conteudo),
        source_url=url_final[:500],
        origin=origin,
        fetched_at=timezone.now(),
        uploaded_by=uploaded_by,
        status=Document.Status.UPLOADED,
    )
    if iniciar:
        iniciar_ingestao(documento)
    return ResultadoDeIngestao(document=documento, ja_existia=False)


def registrar_nota(
    *, titulo: str, autor: str, credencial: str, texto: str, escrita_por
) -> Document:
    """Grava a nota, indexa todos os blocos e conclui a curadoria.

    Todos os blocos, e nao uma selecao: a nota foi escrita para servir de
    fonte, entao nao ha parte dela que a pessoa nao queira no indice.
    """
    from apps.knowledge.blocos import preparar_blocos
    from apps.knowledge.perfis import categoria_da_natureza
    from apps.knowledge.services import indexar_blocos, marcar_curado

    corpo = texto.strip()
    markdown = f"# {titulo.strip()}\n\n{corpo}"
    bruto = markdown.encode("utf-8")
    rotulo = f"{autor.strip()}, {credencial.strip()}".strip(", ")
    sha = hashlib.sha256(bruto).hexdigest()

    existente = Document.objects.filter(file_sha256=sha).first()
    if existente is not None:
        return existente

    documento = Document.objects.create(
        category=categoria_da_natureza("especialista"),
        original_file=ContentFile(bruto, name="nota.md"),
        file_sha256=sha,
        file_size_bytes=len(bruto),
        title=titulo.strip()[:500],
        authors=autor.strip()[:300],
        year=timezone.localdate().year,
        source_label=rotulo[:300],
        origin=Document.Origin.NOTE,
        license=Document.License.OWN,
        markdown_full=markdown,
        extraction_method=Document.ExtractionMethod.TEXT,
        metadata_confidence=Document.MetadataConfidence.MANUAL,
        uploaded_by=escrita_por,
        status=Document.Status.PENDING_CURATION,
    )

    blocos = {bloco.ordem for bloco in preparar_blocos(documento)}
    indexar_blocos(document=documento, blocos_marcados=blocos)
    marcar_curado(document=documento, revisado_por=escrita_por)
    logger.info("Nota do especialista %s registrada com %s bloco(s).", documento.pk, len(blocos))
    return documento
