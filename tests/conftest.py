"""Configuracao compartilhada dos testes."""

from __future__ import annotations

import hashlib

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection

from apps.accounts.models import Domain, Tenant, User


@pytest.fixture
def public_tenant(db) -> Tenant:
    tenant, _ = Tenant.objects.get_or_create(
        schema_name="public",
        defaults={"name": "PubliBot", "slug": "public", "status": Tenant.Status.ACTIVE},
    )
    # Derivado do settings, nunca uma constante repetida: o dominio de
    # desenvolvimento precisa ter dois rotulos para o cookie de sessao
    # atravessar subdominios, e fixar "localhost" aqui faria os testes
    # divergirem silenciosamente da configuracao real.
    Domain.objects.get_or_create(
        domain=settings.ROOT_DOMAIN, tenant=tenant, defaults={"is_primary": True}
    )
    return tenant


@pytest.fixture
def tenant_factory(db, public_tenant):
    """Cria um tenant com schema real no Postgres.

    Cria o schema de verdade (nao um mock) porque tudo o que estes testes
    protegem depende do comportamento real do search_path.
    """
    created: list[Tenant] = []

    def _make(schema_name: str) -> Tenant:
        tenant = Tenant.objects.create(
            schema_name=schema_name,
            name=schema_name.replace("_", " ").title(),
            slug=schema_name.replace("_", "-"),
            status=Tenant.Status.ACTIVE,
        )
        tenant.create_schema(check_if_exists=True, verbosity=0)
        Domain.objects.create(
            domain=f"{tenant.slug}.{settings.ROOT_DOMAIN}", tenant=tenant, is_primary=True
        )
        created.append(tenant)
        return tenant

    yield _make

    # Sem DROP SCHEMA explicito: `CREATE SCHEMA` e transacional no PostgreSQL,
    # e o pytest-django reverte a transacao de cada teste. Um DROP aqui roda
    # ainda dentro dessa transacao e falha com
    # "cannot DROP TABLE ... because it has pending trigger events" assim que
    # as tabelas passam a ter constraints deferidas.
    connection.set_schema_to_public()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        email="revisor@exemplo.com",
        password="uma-senha-longa-de-teste",
        full_name="Revisor de Teste",
    )


@pytest.fixture(scope="session")
def _exige_pgvector(django_db_setup, django_db_blocker):
    """Falha cedo e com mensagem util se o banco de teste nao tiver o pgvector.

    O banco de teste e criado do zero pelo pytest-django e herda do
    `template1`. Como `vector` nao e uma extensao "trusted", cria-la exige
    superusuario — e o usuario da aplicacao nao e (nem deveria ser). Por isso
    `scripts/setup-db.sh` instala a extensao no `template1`.

    Sem esta verificacao, a ausencia se manifestaria como
    "current transaction is aborted" em cascata, varios testes adiante, sem
    apontar para a causa.
    """
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.nspname
              FROM pg_extension e
              JOIN pg_namespace n ON n.oid = e.extnamespace
             WHERE e.extname = 'vector'
            """
        )
        row = cursor.fetchone()

    if row is None:
        pytest.fail(
            "A extensao 'vector' nao existe no banco de teste.\n"
            "Rode: ./scripts/setup-db.sh  (ele instala no template1, de onde "
            "os bancos de teste herdam)",
            pytrace=False,
        )
    if row[0] != "extensions":
        pytest.fail(
            f"A extensao 'vector' esta no schema '{row[0]}', deveria estar em "
            f"'extensions'. No public ela nao fica alcancavel para o segundo "
            f"tenant.",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Um tenant pronto para rodar o fluxo de geracao
# ---------------------------------------------------------------------------
# Moram aqui, e nao no arquivo que os usa mais, porque dois arquivos de teste
# precisam do mesmo cenario. Importar fixture de um modulo de teste para outro
# funciona por acidente e o lint acusa como redefinicao; conftest e o lugar
# que o pytest oferece para isto.


@pytest.fixture
def embedding_falso(settings):
    """Vetores por hash: deterministicos, sem relacao semantica entre si.

    Nao e autouse: os testes que medem a busca de verdade precisam do modelo
    real (ADR-0014), e liga-lo para a suite inteira apagaria justamente o que
    eles verificam. Quem quer o falso pede.

    O limiar sobe junto porque, com vetores sem semantica, o corte real faria
    a recuperacao devolver vazio sempre — e o que estes testes olham e a
    SEQUENCIA dos passos, nao a qualidade da busca.
    """
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    settings.RAG_MAX_COSINE_DISTANCE = 2.0
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def tenant_com_acervo(tenant_factory, embedding_falso):
    """Um tenant com um documento indexado, dentro do schema dele."""
    from django_tenants.utils import schema_context

    from apps.content.services import garantir_prompts_padrao
    from apps.knowledge.models import Document, DocumentCategory, SuperChunk
    from apps.knowledge.services import salvar_super_chunk

    tenant = tenant_factory("fluxos")
    with schema_context(tenant.schema_name):
        garantir_prompts_padrao()

        categoria = DocumentCategory.objects.create(name="Artigo", slug="artigo")
        documento = Document.objects.create(
            category=categoria,
            title="Estudo sobre o efeito",
            authors="Souza, M.",
            year=2024,
            source_url="https://revista.exemplo.org/estudo",
            file_sha256=hashlib.sha256(b"estudo").hexdigest(),
            original_file=ContentFile(b"pdf", name="estudo.pdf"),
            license="cc-by",
            status=Document.Status.CURATED,
        )
        salvar_super_chunk(
            document=documento,
            kind=SuperChunk.Kind.ABSTRACT,
            content="O efeito observado no experimento sobre metabolismo.",
        )
        yield tenant


@pytest.fixture
def conexao(db):
    """Conexao de inferencia — vive no schema public, compartilhada."""
    from apps.inference.models import InferenceConnection

    return InferenceConnection.objects.create(
        name="GPU local",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        default_model="modelo-de-teste",
        max_concurrency=1,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Um gerador de imagem que responde como o worker, sem placa nenhuma
# ---------------------------------------------------------------------------
# Mora aqui, e nao num arquivo de teste, porque dois arquivos ja precisam
# dele. Duas copias do mesmo duble divergem no dia em que so uma e ajustada —
# e um duble desatualizado passa sem reclamar, que e o pior jeito de um teste
# falhar.
def _png_de_teste() -> bytes:
    import io

    from PIL import Image

    memoria = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 90, 160)).save(memoria, format="PNG")
    return memoria.getvalue()


class GeradorDeImagemFalso:
    """Devolve `revised_prompt` igual ao prompt recebido, como o worker faz."""

    def generate(self, *, model, prompt, quantidade=3, tamanho="1344x704"):
        from apps.inference.providers.base import ImagemGerada

        return [
            ImagemGerada(conteudo=_png_de_teste(), prompt_revisado=prompt)
            for _ in range(quantidade)
        ]


@pytest.fixture
def imagem_falsa(monkeypatch):
    """Uma conexao de imagem que funciona, sem chamar ninguem de verdade."""
    import contextlib
    from types import SimpleNamespace

    conexao_de_imagem = SimpleNamespace(name="Imagem", default_model="modelo-de-imagem", pk=1)
    monkeypatch.setattr("apps.content.capas._conexao_de_imagem", lambda: conexao_de_imagem)
    monkeypatch.setattr("apps.content.capas._registrar_uso", lambda *a, **k: None)
    monkeypatch.setattr(
        "apps.content.capas.descrever_capa",
        lambda article, site=None, job=None: ("a monitor on a wooden table", None),
    )
    monkeypatch.setattr(
        "apps.inference.providers.base.get_image_provider",
        lambda *a, **k: GeradorDeImagemFalso(),
    )
    monkeypatch.setattr("apps.inference.leases.reserva", lambda *a, **k: contextlib.nullcontext())
