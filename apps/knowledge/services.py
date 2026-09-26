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

    chunk, _criado = SuperChunk.objects.update_or_create(
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
            **campos_da_fonte(document),
            "is_active": True,
        },
    )
    atualizar_indice_textual([chunk])
    return chunk


def campos_da_fonte(document: Document) -> dict:
    """O que cada trecho copia do documento e do perfil da categoria.

    Copiado, e nao lido por JOIN na hora de citar, pelo mesmo motivo dos
    outros metadados: a citacao de um artigo que ja foi ao ar precisa
    sobreviver a uma mudanca posterior do documento ou da categoria.
    """
    categoria = document.category
    return {
        "source_title": document.title,
        "source_authors": document.authors,
        "source_year": document.year,
        "source_url": document.source_url,
        "source_authority": document.authority_score,
        "source_label": document.rotulo[:300],
        "citation_mode": categoria.modo_de_citacao_efetivo,
        "supports_central_idea": categoria.supports_central_idea,
    }


@dataclass(frozen=True)
class TrechoRecuperado:
    chunk: SuperChunk
    distancia: float
    posicao: int


# Termos curtos demais ou vazios de sentido na busca textual. Poucos, e so os
# que mais aparecem: a busca textual e o complemento da vetorial para termo
# exato, nao uma busca completa de linguagem natural.
_PALAVRAS_VAZIAS = frozenset(
    "a o as os de da do das dos e em no na nos nas um uma para por com que como "
    "the of and to in for on with is are what how".split()
)

# Constante do Reciprocal Rank Fusion. 60 e o valor do artigo original e o
# padrao de fato; o resultado e pouco sensivel a ele.
_RRF_K = 60


def texto_pesquisavel(chunk) -> str:
    """O que a busca textual enxerga: trecho, titulo do bloco e da fonte."""
    partes = [chunk.content, chunk.heading, chunk.source_title, chunk.source_label]
    return normalizar_para_impressao(" ".join(p for p in partes if p))


def atualizar_indice_textual(chunks) -> None:
    """Grava o `search_vector` dos trechos. Chamado ao indexar."""
    from django.contrib.postgres.search import SearchVector
    from django.db.models import Value

    for chunk in chunks:
        SuperChunk.objects.filter(pk=chunk.pk).update(
            search_vector=SearchVector(Value(texto_pesquisavel(chunk)), config="simple")
        )


def _consulta_textual(consulta: str):
    """Os termos da consulta, em OU. Vazio quando nao sobra termo nenhum.

    OU, e nao E: uma pauta tem dez palavras, e exigir todas num paragrafo so
    nao casaria nada. Quem ordena e o rank, e quem decide se o trecho serve
    continua sendo a distancia vetorial.
    """
    from django.contrib.postgres.search import SearchQuery

    termos = [
        t
        for t in re.findall(r"[a-z0-9]+", normalizar_para_impressao(consulta))
        if len(t) > 1 and t not in _PALAVRAS_VAZIAS
    ]
    if not termos:
        return None
    return SearchQuery(" | ".join(dict.fromkeys(termos)), search_type="raw", config="simple")


def recuperar(
    *,
    consulta: str,
    origem: str,
    top_k: int | None = None,
    distancia_maxima: float | None = None,
    deduplicar_por_documento: bool = True,
) -> tuple[RetrievalQuery, list[TrechoRecuperado]]:
    """Busca trechos relevantes e registra a consulta.

    Hibrida: a busca vetorial e a textual correm lado a lado e os resultados
    se juntam por Reciprocal Rank Fusion. A vetorial acha o que DIZ a mesma
    coisa com outras palavras; a textual acha o termo exato que o vetor
    aproxima mal (sigla, nome de tabela, nome proprio).

    O limiar continua sendo a trava: nenhum trecho passa so por ter a palavra.
    O que casa por texto ganha uma folga pequena (`RAG_FOLGA_TEXTUAL`) — o
    termo exato e evidencia de relevancia que a distancia nao capta —, mas
    fica sujeito a ela.

    Com `RAG_RERANKER_MODEL` configurado, um cross-encoder reordena os
    candidatos que passaram, antes do corte por `top_k`.

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

    # Busca mais que top_k porque a deduplicacao por documento e o limiar
    # descartam varios candidatos.
    limite_bruto = max(top_k * 8, 20)

    base = (
        SuperChunk.objects.filter(is_active=True, embedding__isnull=False)
        # Fonte vencida (tabela de preco do mes passado, norma revisada) sai da
        # busca ate alguem atualiza-la. Citar um valor que ja mudou e pior que
        # nao citar nada.
        .exclude(document__valid_until__lt=timezone.localdate())
        .annotate(distancia=CosineDistance("embedding", vetor))
    )

    por_vetor = list(
        base.filter(distancia__lte=distancia_maxima).order_by("distancia")[:limite_bruto]
    )

    por_texto = []
    folga = float(getattr(settings, "RAG_FOLGA_TEXTUAL", 0.0))
    consulta_textual = _consulta_textual(consulta) if settings.RAG_BUSCA_HIBRIDA else None
    if consulta_textual is not None:
        from django.contrib.postgres.search import SearchRank
        from django.db.models import F

        por_texto = list(
            base.filter(search_vector=consulta_textual, distancia__lte=distancia_maxima + folga)
            .annotate(rank_textual=SearchRank(F("search_vector"), consulta_textual))
            .order_by("-rank_textual")[:limite_bruto]
        )

    candidatos = _fundir([por_vetor, por_texto])
    candidatos = _reordenar(consulta, candidatos)

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


def _fundir(listas: list[list]) -> list:
    """Reciprocal Rank Fusion: a posicao em cada lista vale 1/(k + posicao).

    Funde por POSICAO, e nao por nota, porque a distancia de cosseno e o rank
    textual estao em escalas que nao se comparam. Um trecho bem colocado nas
    duas listas sobe; um que so aparece numa fica atras dele.
    """
    notas: dict = {}
    por_id: dict = {}
    for lista in listas:
        for posicao, chunk in enumerate(lista, start=1):
            por_id.setdefault(chunk.pk, chunk)
            notas[chunk.pk] = notas.get(chunk.pk, 0.0) + 1.0 / (_RRF_K + posicao)
    ordem = sorted(notas, key=lambda pk: (-notas[pk], float(por_id[pk].distancia)))
    return [por_id[pk] for pk in ordem]


def _reordenar(consulta: str, candidatos: list) -> list:
    """Aplica o cross-encoder, quando configurado. Sem ele, mantem a ordem.

    Falha do reordenador nao derruba a busca: ele melhora a ordem, e uma ordem
    um pouco pior e melhor que nenhum resultado.
    """
    from apps.knowledge.reranker import get_reordenador

    reordenador = get_reordenador()
    if reordenador is None or len(candidatos) < 2:
        return candidatos
    try:
        notas = reordenador.notas(consulta, [c.content for c in candidatos])
    except Exception:
        logger.exception("Reordenador falhou; mantendo a ordem da busca.")
        return candidatos
    pares = sorted(zip(notas, range(len(candidatos)), strict=True), key=lambda p: (-p[0], p[1]))
    return [candidatos[i] for _nota, i in pares]


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

    document.valid_until = calcular_validade(document)
    document.save()
    return document


def calcular_validade(document: Document):
    """Ate quando a fonte vale, pelo perfil da categoria.

    Conta da data de publicacao; sem ela, de quando foi buscada; sem nenhuma
    das duas, de hoje. Categoria sem validade: nao vence.
    """
    from datetime import timedelta

    dias = document.category.validity_days
    if not dias:
        return None
    base = document.published_on or (
        timezone.localdate(document.fetched_at) if document.fetched_at else timezone.localdate()
    )
    return base + timedelta(days=dias)


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
    novos = []
    for bloco in blocos:
        if bloco.ordem not in blocos_marcados:
            continue

        for posicao, paragrafo in enumerate(bloco.paragrafos):
            texto = montar_texto_vetorizavel(
                paragrafo.texto,
                titulo_do_documento=document.title or "",
                titulo_do_bloco=bloco.titulo,
            )
            novo = SuperChunk.objects.create(
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
                **campos_da_fonte(document),
                is_active=True,
            )
            novos.append(novo)
            criados += 1

    atualizar_indice_textual(novos)
    logger.info("Documento %s: %s trecho(s) indexados.", document.pk, criados)
    return criados


def blocos_marcados(document: Document) -> set[int]:
    """Quais blocos ja estao no indice, para a tela voltar marcada."""
    return set(document.chunks.values_list("block_index", flat=True))
