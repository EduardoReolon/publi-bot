"""Regras de ingestao, curadoria e recuperacao."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.knowledge.embeddings import get_embedding_client
from apps.knowledge.models import (
    Document,
    RetrievalHit,
    RetrievalQuery,
    RetrievalSettings,
    SuperChunk,
)

logger = logging.getLogger("publibot.knowledge")

# Um DOI comeca sempre por "10." seguido do prefixo do registrante.
PADRAO_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)\b")

# A extracao sem analise de layout troca a barra por variantes tipograficas e
# as vezes deixa um espaco depois dela. Num artigo do JAWRA o DOI saiu como
# "10.1111\u2044 j.1752-1688.2007.00027.x" e simplesmente nao era encontrado —
# o documento ficava sem o unico identificador estavel que ele tem.
BARRAS_EQUIVALENTES = str.maketrans({"\u2044": "/", "\u2215": "/", "\uff0f": "/"})
PADRAO_DE_ESPACO_APOS_BARRA = re.compile(r"(?<=\b10\.\d{4})(/)\s+")


def calcular_sha256(arquivo) -> str:
    """Hash do arquivo, lido em blocos.

    Calculado NO UPLOAD, antes de enfileirar qualquer processamento: e a
    primeira e mais barata camada de idempotencia. Um arquivo identico ja
    ingerido nunca chega a consumir tempo de conversao.
    """
    digest = hashlib.sha256()
    for bloco in iter(lambda: arquivo.read(65536), b""):
        digest.update(bloco)
    arquivo.seek(0)
    return digest.hexdigest()


def normalizar_para_impressao(texto: str) -> str:
    """Normaliza texto para comparacao aproximada.

    Remove acentos (NFKD), colapsa espacos e passa para minusculas. Serve para
    detectar "possivel duplicata", nunca para bloquear: as variacoes de citacao
    ("Silva, J." contra "SILVA, Joao") sao numerosas demais para uma regra
    dura ser justa.
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", sem_acento).strip().casefold()


def calcular_impressao_do_conteudo(titulo: str, autores: str, ano: int | None) -> str:
    base = normalizar_para_impressao(f"{titulo}|{autores}|{ano or ''}")
    return hashlib.sha256(base.encode()).hexdigest()


def extrair_doi(texto: str) -> str | None:
    """Primeiro DOI encontrado no texto, se houver.

    Normaliza antes de procurar: a barra pode ter virado uma variante
    tipografica e pode haver espaco depois dela, e nos dois casos o DOI existe
    no documento mas nao seria achado.
    """
    normalizado = (texto or "").translate(BARRAS_EQUIVALENTES)
    normalizado = PADRAO_DE_ESPACO_APOS_BARRA.sub(r"\1", normalizado)
    achado = PADRAO_DOI.search(normalizado)
    if not achado:
        return None
    # DOIs costumam vir grudados a pontuacao final da frase.
    return achado.group(1).rstrip(".,;)")


def formatar_autores(autores: list[str]) -> str:
    """Formata a lista para citacao.

    Regra fixada explicitamente, porque a especificacao original a deixava em
    aberto: 3 ou mais autores viram "Sobrenome et al."; 2 viram "A e B"; 1 fica
    integral.
    """
    limpos = [a.strip() for a in autores if a and a.strip()]
    if not limpos:
        return ""
    if len(limpos) == 1:
        return limpos[0]
    if len(limpos) == 2:
        return f"{limpos[0]} e {limpos[1]}"
    return f"{limpos[0]} et al."


@dataclass(frozen=True)
class ResultadoDeIngestao:
    document: Document
    ja_existia: bool


def ingerir_documento(*, arquivo, category, uploaded_by, title: str = "") -> ResultadoDeIngestao:
    """Registra um documento novo, deduplicando por hash do arquivo."""
    sha = calcular_sha256(arquivo)

    existente = Document.objects.filter(file_sha256=sha).first()
    if existente is not None:
        return ResultadoDeIngestao(document=existente, ja_existia=True)

    documento = Document.objects.create(
        category=category,
        original_file=arquivo,
        file_sha256=sha,
        file_size_bytes=getattr(arquivo, "size", 0) or 0,
        title=title,
        uploaded_by=uploaded_by,
        status=Document.Status.UPLOADED,
    )
    return ResultadoDeIngestao(document=documento, ja_existia=False)


class ChunkGrandeDemais(ValueError):
    """O trecho excede o limite de tokens do modelo de embedding."""


@transaction.atomic
def salvar_super_chunk(
    *, document: Document, kind: str, content: str, char_start: int = 0, char_end: int = 0
) -> SuperChunk:
    """Cria ou atualiza um trecho curado e o vetoriza.

    Valida o tamanho com o tokenizador REAL. O modelo trunca em 512 tokens sem
    emitir erro: sem esta checagem, metade de uma conclusao longa seria
    descartada em silencio e ninguem saberia.
    """
    cliente = get_embedding_client()
    tokens = cliente.contar_tokens(content)

    if tokens > settings.EMBEDDING_MAX_TOKENS:
        raise ChunkGrandeDemais(
            f"O trecho tem {tokens} tokens e o limite e {settings.EMBEDDING_MAX_TOKENS}. "
            f"O modelo truncaria o excedente sem avisar. Divida o trecho."
        )

    vetor = cliente.embed_passage([content])[0]

    chunk, _ = SuperChunk.objects.update_or_create(
        document=document,
        kind=kind,
        defaults={
            "content": content,
            "char_start": char_start,
            "char_end": char_end,
            "embedding": vetor,
            "embedding_model": cliente.model_name,
            "embedding_dim": cliente.dimensions,
            "token_count": tokens,
            # Copiados agora para que a citacao sobreviva a edicoes posteriores
            # do documento.
            "source_title": document.title,
            "source_authors": document.authors,
            "source_year": document.year,
            "source_url": document.source_url,
            "source_authority": document.authority_score,
            "is_active": True,
        },
    )
    return chunk


@dataclass(frozen=True)
class TrechoRecuperado:
    chunk: SuperChunk
    distancia: float
    posicao: int


def recuperar(
    *,
    consulta: str,
    origem: str,
    top_k: int | None = None,
    distancia_maxima: float | None = None,
    deduplicar_por_documento: bool = True,
) -> tuple[RetrievalQuery, list[TrechoRecuperado]]:
    """Busca trechos relevantes e registra a consulta.

    `deduplicar_por_documento` importa mais do que parece: dois trechos do
    mesmo artigo nao sao duas fontes independentes. Sem a deduplicacao, o
    filtro de consenso trataria o mesmo estudo como confirmacao de si mesmo.
    """
    # O limiar vem do tenant, e nao do `settings`: a distancia que separa
    # "sustenta o texto" de "so fala do mesmo assunto" e propriedade do acervo.
    # Num corpus de tema unico todas as distancias encolhem, e o valor que
    # filtra bem num cliente aceita tudo no outro.
    config = RetrievalSettings.carregar()
    top_k = top_k if top_k is not None else config.top_k
    distancia_maxima = (
        distancia_maxima if distancia_maxima is not None else config.max_cosine_distance
    )

    cliente = get_embedding_client()
    vetor = cliente.embed_query(consulta)

    registro = RetrievalQuery.objects.create(
        origin=origem,
        query_text=consulta,
        top_k=top_k,
        max_distance=distancia_maxima,
        embedding_model=cliente.model_name,
    )

    # Busca mais que top_k porque a deduplicacao por documento pode descartar
    # varios candidatos.
    limite_bruto = top_k * 4 if deduplicar_por_documento else top_k

    candidatos = (
        SuperChunk.objects.filter(is_active=True, embedding__isnull=False)
        .annotate(distancia=CosineDistance("embedding", vetor))
        .filter(distancia__lte=distancia_maxima)
        .order_by("distancia")[:limite_bruto]
    )

    selecionados: list[TrechoRecuperado] = []
    documentos_vistos: set = set()

    for chunk in candidatos:
        if deduplicar_por_documento and chunk.document_id in documentos_vistos:
            continue
        documentos_vistos.add(chunk.document_id)
        selecionados.append(
            TrechoRecuperado(
                chunk=chunk, distancia=float(chunk.distancia), posicao=len(selecionados) + 1
            )
        )
        if len(selecionados) >= top_k:
            break

    RetrievalHit.objects.bulk_create(
        [
            RetrievalHit(query=registro, super_chunk=t.chunk, distance=t.distancia, rank=t.posicao)
            for t in selecionados
        ]
    )

    return registro, selecionados


def marcar_curado(*, document: Document, revisado_por, segundos: int = 0) -> Document:
    """Conclui a curadoria e aplica a regra de retencao de texto integral.

    Documentos proprietarios ou de licenca desconhecida perdem o Markdown
    completo: o trecho curado permanece (citacao de pequeno trecho), mas a
    copia integral armazenada nao se sustenta sem fair use, que o Brasil nao
    possui.
    """
    document.status = Document.Status.CURATED
    document.reviewed_by = revisado_por
    document.reviewed_at = timezone.now()
    document.curation_seconds = segundos
    document.content_fingerprint = calcular_impressao_do_conteudo(
        document.title, document.authors, document.year
    )

    if not document.pode_guardar_texto_integral:
        document.markdown_full = ""

    document.save()
    return document


def possiveis_duplicatas(document: Document):
    """Documentos com a mesma impressao de conteudo, para aviso na curadoria."""
    impressao = calcular_impressao_do_conteudo(document.title, document.authors, document.year)
    if not document.title:
        return Document.objects.none()
    return Document.objects.filter(content_fingerprint=impressao).exclude(pk=document.pk)


@transaction.atomic
def indexar_blocos(*, document: Document, blocos_marcados: set[int]) -> int:
    """Refaz o indice do documento a partir dos blocos marcados.

    Substitui tudo, em vez de acrescentar: o conjunto marcado na tela e a
    verdade sobre o documento. Acrescentar deixaria no indice trecho de bloco
    que a pessoa acabou de desmarcar, e ela nao teria como saber.

    Substituir e seguro porque a citacao de um artigo ja publicado aponta para
    o chunk com `SET_NULL` e guarda titulo e URL copiados — apagar o chunk nao
    apaga a referencia do que foi ao ar.

    Cada paragrafo vira um vetor, e nao o bloco inteiro. Ver `blocos.py` para o
    porque.
    """
    from apps.knowledge.blocos import montar_texto_vetorizavel, preparar_blocos

    cliente = get_embedding_client()
    blocos = preparar_blocos(document)

    document.chunks.all().delete()

    criados = 0
    for bloco in blocos:
        if bloco.ordem not in blocos_marcados:
            continue

        for posicao, paragrafo in enumerate(bloco.paragrafos):
            texto = montar_texto_vetorizavel(
                paragrafo.texto,
                titulo_do_documento=document.title or "",
                titulo_do_bloco=bloco.titulo,
            )
            SuperChunk.objects.create(
                document=document,
                kind=SuperChunk.Kind.CUSTOM,
                content=paragrafo.texto,
                heading=bloco.titulo[:300],
                block_index=bloco.ordem,
                paragraph_index=posicao,
                # O vetor cobre o texto COM o prefixo de contexto; o `content`
                # guarda so o paragrafo, que e o que o revisor precisa ler.
                embedding=cliente.embed_passage([texto])[0],
                embedding_model=cliente.model_name,
                embedding_dim=cliente.dimensions,
                token_count=paragrafo.tokens,
                source_title=document.title,
                source_authors=document.authors,
                source_year=document.year,
                source_url=document.source_url,
                source_authority=document.authority_score,
                is_active=True,
            )
            criados += 1

    logger.info("Documento %s: %s trecho(s) indexados.", document.pk, criados)
    return criados


def blocos_marcados(document: Document) -> set[int]:
    """Quais blocos ja estao no indice, para a tela voltar marcada."""
    return set(document.chunks.values_list("block_index", flat=True))
