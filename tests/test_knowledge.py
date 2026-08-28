"""Testes da base de conhecimento.

Usam o `FakeEmbeddingClient` por padrao: carregar 2 GB de modelo a cada
execucao da suite tornaria o ciclo de desenvolvimento inviavel. O modelo real
e exercitado nos testes marcados como `integration`.
"""

from __future__ import annotations

import hashlib

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.knowledge.embeddings import FakeEmbeddingClient
from apps.knowledge.models import Document, DocumentCategory, SuperChunk
from apps.knowledge.services import (
    ChunkGrandeDemais,
    calcular_impressao_do_conteudo,
    calcular_sha256,
    extrair_doi,
    formatar_autores,
    ingerir_documento,
    marcar_curado,
    normalizar_para_impressao,
    possiveis_duplicatas,
    recuperar,
    salvar_super_chunk,
)


@pytest.fixture(autouse=True)
def cliente_de_embedding_falso(settings):
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def tenant_com_conhecimento(tenant_factory):
    tenant = tenant_factory("conhecimento")
    with schema_context(tenant.schema_name):
        yield tenant


@pytest.fixture
def categoria(tenant_com_conhecimento):
    return DocumentCategory.objects.create(name="Artigo cientifico", slug="artigo")


def _documento(categoria, sufixo: str, **extra) -> Document:
    return Document.objects.create(
        category=categoria,
        file_sha256=hashlib.sha256(sufixo.encode()).hexdigest(),
        original_file=ContentFile(b"conteudo", name=f"{sufixo}.pdf"),
        **extra,
    )


# ---------------------------------------------------------------------------
# Idempotencia da ingestao
# ---------------------------------------------------------------------------


def test_sha256_e_calculado_e_o_ponteiro_volta_ao_inicio():
    """O ponteiro precisa voltar: se ficasse no fim, o arquivo seria salvo
    vazio — uma falha que so apareceria ao tentar ler o PDF depois."""
    arquivo = ContentFile(b"conteudo do pdf", name="a.pdf")
    sha = calcular_sha256(arquivo)
    assert sha == hashlib.sha256(b"conteudo do pdf").hexdigest()
    assert arquivo.read() == b"conteudo do pdf"


@pytest.mark.django_db
def test_arquivo_identico_nao_duplica(categoria, user):
    a = ContentFile(b"mesmo pdf", name="x.pdf")
    primeiro = ingerir_documento(arquivo=a, category=categoria, uploaded_by=user)
    assert primeiro.ja_existia is False

    b = ContentFile(b"mesmo pdf", name="y.pdf")
    segundo = ingerir_documento(arquivo=b, category=categoria, uploaded_by=user)
    assert segundo.ja_existia is True
    assert segundo.document.pk == primeiro.document.pk
    assert Document.objects.count() == 1


# ---------------------------------------------------------------------------
# Metadados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("doi: 10.1590/1980-220X-REEUSP-2021-0234", "10.1590/1980-220X-REEUSP-2021-0234"),
        (
            "Disponivel em https://doi.org/10.1016/j.jclepro.2020.123456.",
            "10.1016/j.jclepro.2020.123456",
        ),
        ("sem doi nenhum aqui", None),
    ],
)
def test_extracao_de_doi(texto, esperado):
    assert extrair_doi(texto) == esperado


@pytest.mark.parametrize(
    "autores,esperado",
    [
        (["Silva, J."], "Silva, J."),
        (["Silva, J.", "Souza, M."], "Silva, J. e Souza, M."),
        (["Silva, J.", "Souza, M.", "Lima, A."], "Silva, J. et al."),
        ([], ""),
    ],
)
def test_formatacao_de_autores(autores, esperado):
    """A regra estava em aberto na especificacao. Fixada: 3 ou mais viram
    'et al.', 2 viram 'A e B', 1 fica integral."""
    assert formatar_autores(autores) == esperado


def test_impressao_ignora_acento_caixa_e_espaco():
    """Serve para AVISAR de possivel duplicata, nunca para bloquear: as
    variacoes de citacao sao numerosas demais para uma regra dura ser justa."""
    a = calcular_impressao_do_conteudo("Atenção ao Pré-Natal", "Silva, J.", 2024)
    b = calcular_impressao_do_conteudo("atencao  ao pre-natal", "silva, j.", 2024)
    assert a == b
    assert normalizar_para_impressao("Ação") == "acao"


@pytest.mark.django_db
def test_possivel_duplicata_e_apenas_informativa(categoria, user):
    dados = {"title": "Estudo X", "authors": "Silva, J.", "year": 2024}
    d1 = _documento(categoria, "um", **dados)
    d2 = _documento(categoria, "dois", **dados)

    marcar_curado(document=d1, revisado_por=user)
    marcar_curado(document=d2, revisado_por=user)

    # Nada impediu a criacao do segundo — o aviso e para o curador decidir.
    assert Document.objects.count() == 2
    assert d1 in possiveis_duplicatas(d2)


# ---------------------------------------------------------------------------
# Super Chunks e o limite de tokens
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_chunk_grande_demais_e_recusado(categoria, settings):
    """O modelo trunca em 512 tokens SEM erro. Sem esta checagem, metade de uma
    conclusao longa seria descartada em silencio."""
    settings.EMBEDDING_MAX_TOKENS = 10
    documento = _documento(categoria, "grande", title="T")

    with pytest.raises(ChunkGrandeDemais, match="truncaria"):
        salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="palavra " * 200
        )

    assert SuperChunk.objects.count() == 0


@pytest.mark.django_db
def test_chunk_guarda_metadados_da_fonte_no_momento_da_indexacao(categoria):
    """Copiar os metadados congela a citacao: se o documento for editado depois,
    o que ja foi citado continua correto."""
    documento = _documento(
        categoria,
        "meta",
        title="Titulo Original",
        authors="Silva, J.",
        year=2024,
        source_url="https://exemplo.org/a",
        authority_score=80,
    )
    chunk = salvar_super_chunk(
        document=documento, kind=SuperChunk.Kind.ABSTRACT, content="resumo curto"
    )

    documento.title = "Titulo Alterado Depois"
    documento.save()
    chunk.refresh_from_db()

    assert chunk.source_title == "Titulo Original"
    assert chunk.source_authority == 80
    assert chunk.embedding_dim == FakeEmbeddingClient().dimensions


@pytest.mark.django_db
def test_resumo_e_conclusao_sao_chunks_separados(categoria):
    """Concatenar os dois num vetor unico produziria um centroide que
    representa mal ambos, alem de estourar o limite de tokens."""
    documento = _documento(categoria, "dois-chunks", title="T")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="o resumo")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.CONCLUSION, content="a conclusao")

    assert documento.chunks.count() == 2
    assert {c.kind for c in documento.chunks.all()} == {"abstract", "conclusion"}


@pytest.mark.django_db
def test_salvar_o_mesmo_tipo_duas_vezes_atualiza_em_vez_de_duplicar(categoria):
    documento = _documento(categoria, "update", title="T")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="v1")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="v2")

    assert documento.chunks.count() == 1
    assert documento.chunks.first().content == "v2"


# ---------------------------------------------------------------------------
# Retencao de texto integral e direitos
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "licenca",
    [
        Document.License.OPEN_ACCESS,
        Document.License.CC_BY,
        Document.License.OWN,
        Document.License.PROPRIETARY,
        Document.License.UNKNOWN,
    ],
)
def test_por_padrao_o_texto_integral_e_sempre_guardado(categoria, user, licenca, settings):
    """Descartar e politica de quem opera o acervo, nao regra do software.

    O sistema nao tem como saber que acordo existe com cada editora, e o
    descarte e irreversivel: sem o texto integral nao ha como remarcar blocos
    sem reenviar o arquivo.
    """
    settings.LICENCAS_QUE_DESCARTAM_TEXTO_INTEGRAL = []
    documento = _documento(
        categoria,
        f"lic-{licenca}",
        title="T",
        license=licenca,
        markdown_full="# Documento inteiro convertido",
    )
    marcar_curado(document=documento, revisado_por=user, segundos=300)
    documento.refresh_from_db()

    assert documento.markdown_full
    assert documento.curation_seconds == 300
    assert documento.status == Document.Status.CURATED


@pytest.mark.django_db
def test_licenca_listada_perde_o_texto_integral_ao_concluir(categoria, user, settings):
    """Quem quiser a politica antiga — o Brasil nao tem fair use, e a citacao de
    pequeno trecho do Art. 46 VIII nao cobre guardar a obra inteira — lista as
    licencas e o descarte volta a acontecer."""
    settings.LICENCAS_QUE_DESCARTAM_TEXTO_INTEGRAL = [
        Document.License.PROPRIETARY,
        Document.License.UNKNOWN,
    ]

    descartado = _documento(
        categoria,
        "lic-descarta",
        title="T",
        license=Document.License.PROPRIETARY,
        markdown_full="# Documento inteiro convertido",
    )
    guardado = _documento(
        categoria,
        "lic-guarda",
        title="T",
        license=Document.License.CC_BY,
        markdown_full="# Documento inteiro convertido",
    )

    marcar_curado(document=descartado, revisado_por=user)
    marcar_curado(document=guardado, revisado_por=user)
    descartado.refresh_from_db()
    guardado.refresh_from_db()

    assert descartado.markdown_full == ""
    assert guardado.markdown_full


# ---------------------------------------------------------------------------
# Recuperacao
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recuperacao_registra_consulta_e_resultados(categoria, settings):
    settings.RAG_MAX_COSINE_DISTANCE = 2.0  # aceita tudo, para testar o registro
    documento = _documento(categoria, "rec", title="T")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="assunto")

    consulta, achados = recuperar(consulta="assunto", origem="article")

    assert len(achados) == 1
    assert consulta.hits.count() == 1
    assert consulta.hits.first().rank == 1
    assert consulta.max_distance == 2.0


@pytest.mark.django_db
def test_limiar_barra_trechos_distantes(categoria, settings):
    settings.RAG_MAX_COSINE_DISTANCE = 0.0001
    documento = _documento(categoria, "longe", title="T")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="algo")

    _, achados = recuperar(consulta="completamente diferente", origem="article")
    assert achados == []


@pytest.mark.django_db
def test_deduplicacao_por_documento(categoria, settings):
    """Dois trechos do mesmo artigo NAO sao duas fontes independentes. Sem a
    deduplicacao, o filtro de consenso trataria o mesmo estudo como
    confirmacao de si mesmo."""
    settings.RAG_MAX_COSINE_DISTANCE = 2.0
    documento = _documento(categoria, "dedup", title="T")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="parte um")
    salvar_super_chunk(document=documento, kind=SuperChunk.Kind.CONCLUSION, content="parte dois")

    _, com = recuperar(consulta="parte", origem="article", deduplicar_por_documento=True)
    _, sem = recuperar(consulta="parte", origem="article", deduplicar_por_documento=False)

    assert len(com) == 1
    assert len(sem) == 2


@pytest.mark.django_db
def test_chunk_inativo_nao_aparece(categoria, settings):
    settings.RAG_MAX_COSINE_DISTANCE = 2.0
    documento = _documento(categoria, "inativo", title="T")
    chunk = salvar_super_chunk(document=documento, kind=SuperChunk.Kind.ABSTRACT, content="texto")
    chunk.is_active = False
    chunk.save()

    _, achados = recuperar(consulta="texto", origem="article")
    assert achados == []


# ---------------------------------------------------------------------------
# Cliente de embedding
# ---------------------------------------------------------------------------


def test_nao_existe_metodo_embed_cru():
    """O modelo exige prefixo `query:` ou `passage:`. Esquecer NAO da erro:
    derruba a revocacao em silencio. Um `embed()` generico seria o lugar exato
    onde esse esquecimento aconteceria."""
    from apps.knowledge.embeddings import EmbeddingClient, FastEmbedClient

    for classe in (EmbeddingClient, FastEmbedClient, FakeEmbeddingClient):
        assert not hasattr(classe, "embed"), f"{classe.__name__} expoe embed() cru"
        assert hasattr(classe, "embed_query")
        assert hasattr(classe, "embed_passage")


def test_vetores_saem_normalizados():
    cliente = FakeEmbeddingClient()
    import numpy as np

    v = cliente.embed_query("qualquer coisa")
    assert pytest.approx(1.0, abs=1e-5) == float(np.linalg.norm(v))
