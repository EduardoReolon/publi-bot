"""Configuracao compartilhada dos testes."""

from __future__ import annotations

import hashlib

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection

from apps.accounts.models import Domain, Tenant, User


@pytest.fixture(autouse=True)
def _sem_bases_academicas(request, monkeypatch):
    """Nenhum teste fala com o OpenAlex ou o Unpaywall de verdade.

    A busca de artigos cientificos vem ligada por padrao e entra na busca de
    fontes e na rodada do radar; sem isto, esses testes dependeriam da rede.
    Quem testa as bases pede `bases_academicas` e simula as respostas.
    """
    # O saldo da DataForSEO e lido ao fim de rodada e busca: sem rede no teste.
    from apps.radar import provedores

    monkeypatch.setattr(provedores, "atualizar_saldo_dataforseo", lambda: None)
    # Nem o Internet Archive, consultado para cada link quebrado novo.
    from apps.radar import links_quebrados

    monkeypatch.setattr(links_quebrados, "ultima_copia_boa", lambda url: None)
    # Nem a busca do contato do site, que baixa a home e a pagina de contato.
    monkeypatch.setattr(links_quebrados, "procurar_contato", lambda link: None)
    # A revisao do radar depois de publicar vai para a fila; nos testes, nao.
    from apps.integrations import publishing

    monkeypatch.setattr(publishing, "_rever_o_radar", lambda pk: None)
    # Nem a conferencia das pautas depois de cada curadoria.
    from apps.knowledge import tasks as tarefas_do_acervo

    monkeypatch.setattr(tarefas_do_acervo, "ao_concluir_curadoria", lambda: None)
    # A curadoria vetoriza na fila; nos testes, na hora.
    from django.conf import settings as configuracao

    monkeypatch.setattr(configuracao, "PUBLIBOT_INDEXAR_NA_HORA", True, raising=False)
    if "bases_academicas" in request.fixturenames:
        return
    from apps.knowledge import academicos

    monkeypatch.setattr(academicos, "buscar_openalex", lambda *a, **k: [])

    monkeypatch.setattr(academicos, "consultar_unpaywall", lambda *a, **k: "")
    monkeypatch.setattr(academicos, "por_doi", lambda *a, **k: None)
    if "openalex" not in request.fixturenames:  # test_pesquisa simula o OpenAlex
        from apps.knowledge import pesquisa

        monkeypatch.setattr(pesquisa, "busca_semantica", lambda *a, **k: [])


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


SCHEMA_MODELO = "modelo_de_teste"


@pytest.fixture(scope="session")
def _schema_modelo(django_db_setup, django_db_blocker):
    """Um schema de tenant migrado UMA vez por sessao, para ser copiado.

    Migrar cada tenant do zero leva uns 6 s, e quase todo teste cria um: era a
    maior parte do tempo da suite. Copiar a estrutura pronta leva uma fracao
    disso. O modelo e criado fora da transacao dos testes (fica ate o fim da
    sessao); as copias, dentro (somem com o rollback de cada teste).
    """
    from django_tenants.utils import schema_exists

    with django_db_blocker.unblock():
        # Sempre do zero: num banco de teste reaproveitado (--reuse-db), um
        # modelo antigo ficaria sem as migracoes novas e todas as copias junto.
        if schema_exists(SCHEMA_MODELO):
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA "{SCHEMA_MODELO}" CASCADE')
        Tenant(schema_name=SCHEMA_MODELO, name="Modelo", slug=SCHEMA_MODELO).create_schema(
            verbosity=0
        )
        connection.set_schema_to_public()
    return SCHEMA_MODELO


def _copiar_schema(modelo: str, novo: str) -> None:
    """Copia tabelas, indices, chaves e dados do modelo para um schema novo.

    `LIKE ... INCLUDING ALL` preserva tipo com dimensao (vector(1024)),
    padroes, checks, indices e identidade; as chaves estrangeiras e os dados
    (o registro das migracoes, e o que alguma migracao tenha semeado) vem a
    seguir. A funcao clone_schema do django-tenants perde a dimensao do vetor
    e por isso nao serve aqui.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET search_path TO public, extensions")
        cursor.execute(f'CREATE SCHEMA "{novo}"')
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", [modelo])
        tabelas = [linha[0] for linha in cursor.fetchall()]
        for tabela in tabelas:
            cursor.execute(
                f'CREATE TABLE "{novo}"."{tabela}" (LIKE "{modelo}"."{tabela}" INCLUDING ALL)'
            )
        cursor.execute(
            """SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
               FROM pg_constraint c WHERE c.contype = 'f' AND c.connamespace = %s::regnamespace""",
            [modelo],
        )
        for tabela, nome, definicao in cursor.fetchall():
            tabela = tabela.split(".")[-1].strip('"')
            definicao = definicao.replace(f"{modelo}.", f'"{novo}".')
            cursor.execute(f'ALTER TABLE "{novo}"."{tabela}" ADD CONSTRAINT "{nome}" {definicao}')
        # Nomes vindos do catalogo do proprio Postgres, nao de fora.
        for tabela in tabelas:
            copia = f'INSERT INTO "{novo}"."{tabela}" OVERRIDING SYSTEM VALUE SELECT * '
            cursor.execute(copia + f'FROM "{modelo}"."{tabela}"')
        # Identidade copiada comeca do 1: acerta para depois dos dados copiados.
        cursor.execute(
            """SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema = %s AND is_identity = 'YES'""",
            [novo],
        )
        for tabela, coluna in cursor.fetchall():
            sequencia = f'pg_get_serial_sequence(\'"{novo}"."{tabela}"\', %s)'
            maximo = f'COALESCE((SELECT MAX("{coluna}") FROM "{novo}"."{tabela}"), 0) + 1'  # noqa: S608
            cursor.execute(f"SELECT setval({sequencia}, {maximo}, false)", [coluna])


@pytest.fixture
def tenant_factory(db, public_tenant, _schema_modelo):
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
        _copiar_schema(_schema_modelo, schema_name)
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

    def generate(self, *, model, prompt, quantidade=3, tamanho="1344x704", negativo=""):
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


# Texto com tamanho de artigo de verdade: a aprovacao recusa texto curto demais.
TEXTO_APROVAVEL = "## Titulo\n\n" + " ".join(["Texto do artigo com conteudo."] * 40)


def pronto_para_aprovar(artigo):
    """Completa o artigo de teste com o que a aprovacao exige
    (`services.pendencias_para_aprovar`): capa escolhida, descricao e texto."""
    from django.core.files.base import ContentFile

    if len((artigo.body_markdown or "").split()) < 150:
        artigo.body_markdown = (artigo.body_markdown or "") + "\n\n" + TEXTO_APROVAVEL
    artigo.meta_description = artigo.meta_description or "Descricao para o Google."
    if artigo.status == artigo.Status.DRAFTING:
        artigo.status = artigo.Status.PENDING_REVIEW
    artigo.save()
    if not artigo.images.filter(is_chosen=True).exists():
        artigo.images.create(order=1, is_chosen=True, image=ContentFile(b"webp", name="capa.webp"))
    return artigo
