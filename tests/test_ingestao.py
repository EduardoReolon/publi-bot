"""Testes da ingestao de documentos.

A ingestao para de proposito em `pending_curation`. Essa parada e o coracao da
regra de curadoria: o Markdown convertido nao entra no indice vetorial sem um
humano confirmar titulo, autores, ano e URL — os campos que viram a citacao
publicada no site do cliente — e escolher qual trecho representa o documento.
"""

from __future__ import annotations

import hashlib

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave
from apps.knowledge.extraction import (
    ConversorOcupado,
    ExtracaoIndisponivel,
    extrair_markdown,
)
from apps.knowledge.flows import sugerir_metadados
from apps.knowledge.models import Document, DocumentCategory, SuperChunk
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import avancar, criar_job

MARKDOWN_DE_ARTIGO = """# Efeitos do exercicio sobre o metabolismo

Souza, M., Lima, R., Pereira, A.

Revista Brasileira de Fisiologia, 2023. doi:10.1000/exemplo.2023.42

## Resumo

O estudo acompanhou 120 participantes durante doze meses.
"""


@pytest.fixture(autouse=True)
def embedding_falso(settings):
    """Indexar um trecho carrega 2 GB de modelo; aqui nada disso importa."""
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def tenant_com_categoria(tenant_factory):
    tenant = tenant_factory("ingestao")
    with schema_context(tenant.schema_name):
        DocumentCategory.objects.create(name="Artigo cientifico", slug="artigo")
        yield tenant


def _documento(nome: str, conteudo: bytes) -> Document:
    return Document.objects.create(
        category=DocumentCategory.objects.first(),
        original_file=ContentFile(conteudo, name=nome),
        file_sha256=hashlib.sha256(conteudo).hexdigest(),
        file_size_bytes=len(conteudo),
        status=Document.Status.UPLOADED,
    )


# ---------------------------------------------------------------------------
# Extracao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_arquivo_de_texto_e_convertido_sem_gpu(tenant_com_categoria):
    """O caminho de emergencia existe para o sistema ser testavel sem GPU."""
    documento = _documento("estudo.md", MARKDOWN_DE_ARTIGO.encode())

    resultado = extrair_markdown(documento)

    assert resultado.metodo == "texto"
    assert "120 participantes" in resultado.markdown


@pytest.mark.django_db
def test_extensao_desconhecida_diz_o_que_fazer(tenant_com_categoria):
    documento = _documento("planilha.xlsx", b"nao importa")

    with pytest.raises(ExtracaoIndisponivel, match="Docling"):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_pdf_sem_camada_de_texto_nao_devolve_vazio(tenant_com_categoria, monkeypatch):
    """Um PDF digitalizado deixaria a curadoria com uma tela em branco.

    Dizer que falta OCR e mais util que devolver string vazia e deixar a
    pessoa procurando o que ela fez de errado.
    """

    class PaginaVazia:
        def extract_text(self):
            return ""

    class LeitorFalso:
        def __init__(self, *a, **k):
            self.pages = [PaginaVazia(), PaginaVazia()]

    monkeypatch.setattr("pypdf.PdfReader", LeitorFalso)
    documento = _documento("digitalizado.pdf", b"%PDF-1.4 conteudo")

    with pytest.raises(ExtracaoIndisponivel, match="camada de texto"):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_worker_ocupado_nao_e_falha(tenant_com_categoria, monkeypatch):
    """A maquina converte um documento por vez de proposito.

    Esperar a vez e o comportamento correto; falhar gastaria uma tentativa por
    algo que nao deu errado.
    """
    import httpx

    conexao = InferenceConnection.objects.create(
        name="Worker de conversao",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://127.0.0.1:8100",
        workloads=[InferenceConnection.Workload.VISION_PARSE],
        is_active=True,
    )
    guardar_chave(conexao, "um-segredo-compartilhado")
    conexao.save()

    def responder_ocupado(*a, **k):
        return httpx.Response(503, json={"error": {"code": "busy"}})

    monkeypatch.setattr("httpx.post", responder_ocupado)
    documento = _documento("estudo.pdf", b"%PDF-1.4")

    with pytest.raises(ConversorOcupado):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_sha256_divergente_e_recusado(tenant_com_categoria, monkeypatch):
    """O worker converteu outro arquivo. Repetir nao adianta.

    Sem esta recusa, o Markdown de um documento entraria no acervo como se
    fosse de outro, e a citacao publicada apontaria para a fonte errada.
    """
    import httpx

    conexao = InferenceConnection.objects.create(
        name="Worker",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://127.0.0.1:8100",
        workloads=[InferenceConnection.Workload.VISION_PARSE],
        is_active=True,
    )
    guardar_chave(conexao, "segredo")
    conexao.save()

    monkeypatch.setattr("httpx.post", lambda *a, **k: httpx.Response(422, text="sha nao confere"))
    documento = _documento("estudo.pdf", b"%PDF-1.4")

    with pytest.raises(ExtracaoIndisponivel, match="nao confere"):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_o_arquivo_vai_com_o_sha256_esperado(tenant_com_categoria, monkeypatch):
    """O cabecalho `X-Expected-Sha256` e o que amarra arquivo e documento."""
    import httpx

    conexao = InferenceConnection.objects.create(
        name="Worker",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://127.0.0.1:8100",
        workloads=[InferenceConnection.Workload.VISION_PARSE],
        is_active=True,
    )
    guardar_chave(conexao, "segredo")
    conexao.save()

    enviados = {}

    def capturar(url, **kwargs):
        enviados.update(kwargs)
        return httpx.Response(200, json={"markdown": "# Titulo", "duration_ms": 900})

    monkeypatch.setattr("httpx.post", capturar)
    documento = _documento("estudo.pdf", b"%PDF-1.4")

    resultado = extrair_markdown(documento)

    assert resultado.metodo == "docling"
    assert enviados["headers"]["X-Expected-Sha256"] == documento.file_sha256
    assert enviados["headers"]["X-Worker-Secret"] == "segredo"


# ---------------------------------------------------------------------------
# Metadados sugeridos
# ---------------------------------------------------------------------------
def test_sugestao_le_titulo_autores_ano_e_doi():
    sugestoes = sugerir_metadados(MARKDOWN_DE_ARTIGO)

    assert sugestoes["title"] == "Efeitos do exercicio sobre o metabolismo"
    assert "Souza" in sugestoes["authors"]
    assert sugestoes["year"] == 2023
    assert sugestoes["doi"] == "10.1000/exemplo.2023.42"
    assert sugestoes["campos_encontrados"] == 4


def test_sugestao_nao_confunde_numero_com_ano():
    """Sem a faixa de anos plausiveis, qualquer numero de quatro digitos passa."""
    sugestoes = sugerir_metadados("# Estudo\n\nAmostra de 3500 individuos, n=1234.\n")

    assert sugestoes["year"] is None


def test_sugestao_com_pouco_a_oferecer_diz_isso():
    """`campos_encontrados` baixo e o sinal para a tela insistir na conferencia."""
    sugestoes = sugerir_metadados("texto solto sem estrutura nenhuma")

    assert sugestoes["campos_encontrados"] <= 1


# ---------------------------------------------------------------------------
# Fluxo
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_fluxo_para_em_aguardando_curadoria(tenant_com_categoria):
    """A parada e a regra, nao uma etapa faltando.

    Indexar automaticamente aqui dispensaria a conferencia humana dos campos
    que viram a citacao publicada.
    """
    documento = _documento("estudo.md", MARKDOWN_DE_ARTIGO.encode())
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(documento.pk))

    assert avancar(str(job.pk)) == GenerationJob.Status.DONE

    documento.refresh_from_db()
    assert documento.status == Document.Status.PENDING_CURATION
    assert documento.title == "Efeitos do exercicio sobre o metabolismo"
    assert documento.year == 2023
    assert "120 participantes" in documento.markdown_full
    # Nada foi indexado ainda: isso e trabalho da curadoria.
    assert documento.chunks.count() == 0


@pytest.mark.django_db
def test_metadados_ja_preenchidos_nao_sao_sobrescritos(tenant_com_categoria):
    """Correcao humana vence heuristica, sempre."""
    documento = _documento("estudo.md", MARKDOWN_DE_ARTIGO.encode())
    Document.objects.filter(pk=documento.pk).update(title="Titulo conferido a mao", year=1999)

    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(documento.pk))
    avancar(str(job.pk))

    documento.refresh_from_db()
    assert documento.title == "Titulo conferido a mao"
    assert documento.year == 1999


@pytest.mark.django_db
def test_falha_de_conversao_fica_registrada_no_documento(tenant_com_categoria):
    """O motivo tem de estar onde quem enviou o arquivo vai procurar."""
    documento = _documento("planilha.xlsx", b"conteudo")
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(documento.pk))

    assert avancar(str(job.pk)) == GenerationJob.Status.FAILED

    documento.refresh_from_db()
    assert documento.status == Document.Status.FAILED
    assert "Docling" in documento.failure_reason


# ---------------------------------------------------------------------------
# O metodo de extracao precisa ser visivel
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_metodo_fica_gravado_no_documento(tenant_com_categoria):
    """No trabalho nao basta: quem cura olha o documento, nao o job."""
    documento = _documento("estudo.md", MARKDOWN_DE_ARTIGO.encode())
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(documento.pk))
    avancar(str(job.pk))

    documento.refresh_from_db()
    assert documento.extraction_method == Document.ExtractionMethod.TEXT
    assert documento.extracao_e_confiavel is True


@pytest.mark.django_db
def test_pdf_lido_sem_docling_e_marcado_como_nao_confiavel(tenant_com_categoria, monkeypatch):
    """O texto parece normal; o que nao aparece e a ordem trocada das colunas.

    Sem esta marca, alguem curaria texto embaralhado sem saber e a citacao
    publicada apontaria para uma fonte cujo conteudo foi lido errado.
    """

    class Pagina:
        def extract_text(self):
            return "Texto de uma coluna qualquer."

    class LeitorFalso:
        def __init__(self, *a, **k):
            self.pages = [Pagina()]

    monkeypatch.setattr("pypdf.PdfReader", LeitorFalso)
    documento = _documento("artigo.pdf", b"%PDF-1.4")
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(documento.pk))
    avancar(str(job.pk))

    documento.refresh_from_db()
    assert documento.extraction_method == Document.ExtractionMethod.PYPDF
    assert documento.extracao_e_confiavel is False


@pytest.mark.django_db
def test_extracao_local_desligada_recusa_pdf(tenant_com_categoria, settings):
    """Em producao so o Docling converte PDF.

    Aceitar o caminho local la significaria indexar coluna dupla embaralhada e
    publicar citando uma fonte cujo conteudo foi lido errado.
    """
    settings.PERMITIR_EXTRACAO_LOCAL = False
    documento = _documento("artigo.pdf", b"%PDF-1.4")

    with pytest.raises(ExtracaoIndisponivel, match="Docling"):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_extracao_local_desligada_ainda_aceita_texto(tenant_com_categoria, settings):
    """A trava e sobre PDF: `.md` nao passa por extracao nenhuma."""
    settings.PERMITIR_EXTRACAO_LOCAL = False
    documento = _documento("notas.md", b"# Titulo\n\nCorpo.")

    assert extrair_markdown(documento).metodo == "texto"


@pytest.mark.django_db
def test_pypdf_ausente_diz_o_que_fazer(tenant_com_categoria, monkeypatch):
    """`No module named 'pypdf'` nao diz que e preciso reiniciar o worker."""
    import builtins

    original = builtins.__import__

    def sem_pypdf(nome, *args, **kwargs):
        if nome == "pypdf":
            raise ImportError("No module named 'pypdf'")
        return original(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_pypdf)
    documento = _documento("artigo.pdf", b"%PDF-1.4")

    with pytest.raises(ExtracaoIndisponivel, match="reinicie o worker"):
        extrair_markdown(documento)


@pytest.mark.django_db
def test_reconverter_tira_os_trechos_antigos_do_indice(tenant_com_categoria):
    """O recorte veio do texto anterior, que a reconversao substitui.

    Deixa-los ativos manteria no indice um trecho que nao corresponde mais ao
    documento — e o motivo mais comum de reconverter e o texto anterior estar
    errado.
    """
    from apps.knowledge.services import salvar_super_chunk
    from apps.knowledge.tasks import iniciar_ingestao

    documento = _documento("estudo.md", MARKDOWN_DE_ARTIGO.encode())
    chunk = salvar_super_chunk(
        document=documento,
        kind=SuperChunk.Kind.ABSTRACT,
        content="Recorte feito a partir do texto antigo.",
    )
    assert chunk.is_active is True

    iniciar_ingestao(documento)

    chunk.refresh_from_db()
    assert chunk.is_active is False
    # Desativado, nao apagado: o texto continua visivel para comparacao.
    assert chunk.content == "Recorte feito a partir do texto antigo."
