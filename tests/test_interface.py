"""Testes da interface de operacao do tenant.

O que se testa aqui nao e "a pagina abre": e que as travas do produto continuam
valendo quando o caminho e um clique. Uma protecao que existe no servico e nao
e alcancada pela tela e uma protecao que nao existe na pratica.
"""

from __future__ import annotations

import hashlib

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.urls import reverse
from django_tenants.utils import schema_context

from apps.accounts.models import TenantMembership, User
from apps.content.models import Answer, Article, Question, Topic
from apps.content.services import garantir_prompts_padrao
from apps.integrations.models import Site
from apps.knowledge.models import Document, DocumentCategory, SuperChunk
from apps.knowledge.services import salvar_super_chunk
from apps.ops.models import GenerationJob

ROTAS_DO_MENU = [
    "accounts:painel",
    "knowledge:documentos",
    "knowledge:enviar",
    "knowledge:categorias",
    "knowledge:busca",
    "content:pautas",
    "content:artigos",
    "content:perguntas",
    "integrations:site",
    "operacao:trabalhos",
]


@pytest.fixture(autouse=True)
def embedding_falso(settings):
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def ambiente(tenant_factory, client):
    """Um tenant com dono autenticado, dentro do schema dele."""
    tenant = tenant_factory("interface")
    usuario = User.objects.create_user(
        email="dono@interface.com", password="uma-senha-longa-de-teste", full_name="Dono"
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"

    with schema_context(tenant.schema_name):
        garantir_prompts_padrao()
        DocumentCategory.objects.create(name="Artigo", slug="artigo")
        yield tenant, usuario, client


def _documento_curado(url="https://revista.exemplo.org/estudo") -> Document:
    documento = Document.objects.create(
        category=DocumentCategory.objects.first(),
        title="Estudo sobre o efeito",
        authors="Souza, M.",
        year=2024,
        source_url=url,
        file_sha256=hashlib.sha256(url.encode()).hexdigest(),
        original_file=ContentFile(b"pdf", name="estudo.pdf"),
        license="cc_by",
        status=Document.Status.CURATED,
    )
    salvar_super_chunk(
        document=documento,
        kind=SuperChunk.Kind.ABSTRACT,
        content="O efeito observado no experimento.",
    )
    return documento


# ---------------------------------------------------------------------------
# Navegacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_todas_as_telas_do_menu_respondem(ambiente):
    _, _, client = ambiente
    for nome in ROTAS_DO_MENU:
        resposta = client.get(reverse(nome, urlconf="core.urls_tenants"))
        # `enviar` redireciona para categorias quando nao ha nenhuma; aqui ha.
        assert resposta.status_code == 200, f"{nome} devolveu {resposta.status_code}"


@pytest.mark.django_db
def test_telas_do_tenant_exigem_login(tenant_factory, client):
    """Sem sessao, nenhuma delas pode responder com conteudo."""
    tenant = tenant_factory("sem_login")
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{settings.ROOT_DOMAIN}"

    with schema_context(tenant.schema_name):
        for nome in ROTAS_DO_MENU:
            resposta = client.get(reverse(nome, urlconf="core.urls_tenants"))
            assert resposta.status_code == 302, f"{nome} nao exigiu login"
            assert "/login/" in resposta["Location"]


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_envio_recusa_formato_que_ninguem_sabe_converter(ambiente):
    """Recusar aqui e melhor que aceitar e falhar no worker minutos depois."""
    _, _, client = ambiente

    resposta = client.post(
        reverse("knowledge:enviar", urlconf="core.urls_tenants"),
        {
            "arquivo": ContentFile(b"dados", name="planilha.xlsx"),
            "category": str(DocumentCategory.objects.first().pk),
        },
    )

    assert resposta.status_code == 400
    assert not Document.objects.exists()


@pytest.mark.django_db
def test_curadoria_exige_url_de_origem(ambiente):
    """Sem URL o documento nunca vira fonte primaria — e o link e o produto."""
    _, _, client = ambiente
    documento = _documento_curado()

    resposta = client.post(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants"),
        {
            "acao": "salvar",
            "title": "Estudo",
            "authors": "Souza, M.",
            "year": "2024",
            "source_url": "",
            "language": "pt",
            "license": "cc_by",
            "authority_score": "50",
        },
    )

    assert resposta.status_code == 400
    assert "URL de origem" in resposta.content.decode()


@pytest.mark.django_db
def test_nao_da_para_concluir_curadoria_sem_trecho(ambiente):
    """Concluir sem trecho colocaria no acervo um documento que nada recupera."""
    _, _, client = ambiente
    documento = _documento_curado()
    documento.chunks.all().delete()
    # A fixture ja nasce CURATED; sem voltar o estado, a assercao passaria por
    # acidente em vez de provar que a view recusou.
    Document.objects.filter(pk=documento.pk).update(status=Document.Status.PENDING_CURATION)

    client.post(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants"),
        {
            "acao": "concluir",
            "title": "Estudo",
            "authors": "Souza, M.",
            "year": "2024",
            "source_url": "https://revista.exemplo.org/estudo",
            "language": "pt",
            "license": "cc_by",
            "authority_score": "50",
        },
    )

    documento.refresh_from_db()
    assert documento.status != Document.Status.CURATED


@pytest.mark.django_db
def test_curadoria_marca_metadados_como_conferidos(ambiente):
    """Corrigir os metadados tem de alcancar o que esta no indice.

    Os trechos carregam titulo, autores, ano e URL copiados, porque e deles que
    sai a citacao publicada. Antes, a correcao ficava so no documento e os
    trechos guardavam a versao errada.
    """
    _, _, client = ambiente
    documento = _documento_curado()
    Document.objects.filter(pk=documento.pk).update(
        markdown_full="## Conclusao\n\n" + ("O achado principal do estudo. " * 12)
    )
    documento.refresh_from_db()

    client.post(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants"),
        {
            "acao": "concluir",
            "bloco": "0",
            "title": "Estudo revisado",
            "authors": "Souza, M. et al.",
            "year": "2024",
            "source_url": "https://revista.exemplo.org/estudo",
            "language": "pt",
            "license": "cc_by",
            "authority_score": "70",
        },
    )

    documento.refresh_from_db()
    assert documento.status == Document.Status.CURATED
    assert documento.metadata_confidence == Document.MetadataConfidence.MANUAL

    chunk = documento.chunks.first()
    assert chunk.heading == "Conclusao"
    assert chunk.source_authors == "Souza, M. et al."
    assert chunk.source_authority == 70


@pytest.mark.django_db
def test_bloco_nao_marcado_sai_do_indice(ambiente):
    """Salvar substitui o indice pelo que estiver marcado.

    Acrescentar deixaria no indice trecho de bloco que a pessoa acabou de
    desmarcar, sem nada na tela indicando isso.
    """
    _, _, client = ambiente
    documento = _documento_curado()
    Document.objects.filter(pk=documento.pk).update(
        markdown_full="## Resumo\n\n" + ("Primeiro bloco com corpo. " * 12)
    )
    documento.refresh_from_db()

    dados = {
        "title": "Estudo",
        "authors": "Souza, M.",
        "year": "2024",
        "source_url": "https://revista.exemplo.org/estudo",
        "language": "pt",
        "license": "cc_by",
        "authority_score": "50",
    }

    client.post(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants"),
        {**dados, "acao": "salvar", "bloco": "0"},
    )
    assert documento.chunks.exists()

    client.post(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants"),
        {**dados, "acao": "salvar"},
    )
    assert not documento.chunks.exists()


# ---------------------------------------------------------------------------
# Pautas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_gerar_duas_vezes_a_mesma_pauta_e_recusado(ambiente):
    """Dois artigos sobre o mesmo tema competem entre si no buscador."""
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Um tema", status=Topic.Status.APPROVED)
    Article.objects.create(topic=pauta, title="Ja existe")

    client.post(reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants"))

    assert not GenerationJob.objects.exists()


@pytest.mark.django_db
def test_gerar_cria_o_trabalho(ambiente):
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Um tema", status=Topic.Status.APPROVED)

    client.post(reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants"))

    job = GenerationJob.objects.get()
    assert job.kind == GenerationJob.Kind.PILLAR_ARTICLE
    assert str(job.target_object_id) == str(pauta.pk)


# ---------------------------------------------------------------------------
# Revisao — as travas do produto pela tela
# ---------------------------------------------------------------------------
@pytest.fixture
def artigo_para_revisar(ambiente):
    documento = _documento_curado()
    artigo = Article.objects.create(
        title="Artigo em revisao",
        body_markdown="## Titulo\n\nTexto do artigo.",
        status=Article.Status.PENDING_REVIEW,
        consensus=Article.Consensus.HIGH,
    )
    artigo.citations.create(
        super_chunk=documento.chunks.first(),
        rank=1,
        distance=0.05,
        used_as_primary=True,
        source_title=documento.title,
        source_url=documento.source_url,
    )
    return artigo


def _dados_de_aprovacao(**extra):
    dados = {
        "acao": "aprovar",
        "title": "Artigo em revisao",
        "meta_description": "",
        "body_markdown": "## Titulo\n\nTexto do artigo.",
        "author_name": "Dra. Souza",
        "author_credentials": "CRM 1",
        "quando": "",
    }
    dados.update(extra)
    return dados


@pytest.mark.django_db
def test_aprovar_sem_autor_e_recusado(ambiente, artigo_para_revisar):
    """Conteudo sem autor identificado nao pode ser publicado."""
    _, _, client = ambiente

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(author_name=""),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_divergencia_nao_confirmada_bloqueia_a_aprovacao(ambiente, artigo_para_revisar):
    """Fontes que se contradizem nao viram afirmacao pacifica por um clique.

    Sem esta trava alcancavel pela tela, a protecao existiria no servico e
    nunca seria exercida.
    """
    _, _, client = ambiente
    Article.objects.filter(pk=artigo_para_revisar.pk).update(consensus=Article.Consensus.CONFLICT)

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_divergencia_confirmada_libera(ambiente, artigo_para_revisar):
    _, _, client = ambiente
    Article.objects.filter(pk=artigo_para_revisar.pk).update(consensus=Article.Consensus.CONFLICT)

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(confirmar_divergencia="on"),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.APPROVED_SCHEDULED
    assert artigo_para_revisar.thesis_json["divergencia_confirmada"] is True


@pytest.mark.django_db
def test_site_sensivel_exige_revisor_tecnico(ambiente, artigo_para_revisar):
    """Saude, financas e direito: a credencial e a trava."""
    _, usuario, client = ambiente
    Site.objects.create(
        name="Site", slug="site", base_url="https://site.exemplo.org", is_sensitive=True
    )
    assert usuario.is_technical_reviewer is False

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_edicao_humana_vira_versao_e_e_medida(ambiente, artigo_para_revisar):
    """A proporcao editada e o numero que distingue revisao de carimbo."""
    _, _, client = ambiente
    artigo_para_revisar.revisions.create(
        version=1, body_markdown="## Titulo\n\nTexto do artigo.", source="llm"
    )

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(
            acao="salvar", body_markdown="## Titulo\n\nTexto reescrito por inteiro pelo revisor."
        ),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.human_edit_ratio > 0
    assert artigo_para_revisar.revisions.filter(source="human").exists()


@pytest.mark.django_db
def test_aprovar_agenda_e_sai_da_fila_de_revisao(ambiente, artigo_para_revisar):
    _, _, client = ambiente

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.APPROVED_SCHEDULED
    assert artigo_para_revisar.scheduled_for is not None
    assert artigo_para_revisar.reviewed_by is not None


# ---------------------------------------------------------------------------
# Perguntas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_responder_duas_vezes_e_recusado(ambiente):
    _, _, client = ambiente
    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    pergunta = Question.objects.create(
        site=site,
        remote_id="1",
        question_text="Uma duvida?",
        submitted_at="2026-01-01T00:00:00Z",
        retention_until="2026-12-01T00:00:00Z",
    )
    Answer.objects.create(question=pergunta)

    client.post(reverse("content:responder", args=[pergunta.pk], urlconf="core.urls_tenants"))

    assert not GenerationJob.objects.filter(kind=GenerationJob.Kind.QA_ANSWER).exists()


# ---------------------------------------------------------------------------
# Operacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_redespachar_retoma_do_passo_em_que_parou(ambiente):
    """Reiniciar do zero pagaria de novo a inferencia ja feita."""
    _, _, client = ambiente
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        status=GenerationJob.Status.FAILED,
        current_step=2,
        total_steps=3,
        last_error="algo quebrou",
        step_payloads={"0": {"chunk_ids": []}, "1": {"article_id": "x"}},
    )

    client.post(reverse("operacao:redespachar", args=[job.pk], urlconf="core.urls_tenants"))

    job.refresh_from_db()
    assert job.status == GenerationJob.Status.PENDING
    assert job.current_step == 2
    assert job.step_payloads["1"]["article_id"] == "x"
    assert job.last_error == ""


@pytest.mark.django_db
def test_trabalho_concluido_nao_e_redespachado(ambiente):
    _, _, client = ambiente
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        status=GenerationJob.Status.DONE,
        current_step=3,
        total_steps=3,
    )

    client.post(reverse("operacao:redespachar", args=[job.pk], urlconf="core.urls_tenants"))

    job.refresh_from_db()
    assert job.status == GenerationJob.Status.DONE


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_painel_avisa_quando_nao_ha_site(ambiente):
    """Sem site nada pode ser publicado, e o sintoma seria silencio."""
    _, _, client = ambiente

    corpo = client.get(reverse("accounts:painel", urlconf="core.urls_tenants")).content.decode()

    assert "Nenhum site cadastrado" in corpo


@pytest.mark.django_db
def test_painel_avisa_publicacao_pausada(ambiente):
    _, _, client = ambiente
    Site.objects.create(
        name="S", slug="s", base_url="https://s.exemplo.org", publishing_paused=True
    )

    corpo = client.get(reverse("accounts:painel", urlconf="core.urls_tenants")).content.decode()

    assert "pausada" in corpo


@pytest.mark.django_db
def test_painel_separa_pendencia_de_defeito(ambiente):
    """ "Ha artigo para revisar" e trabalho; "um trabalho falhou" e defeito."""
    _, _, client = ambiente
    Article.objects.create(title="Esperando", status=Article.Status.PENDING_REVIEW)
    GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE, status=GenerationJob.Status.FAILED
    )

    corpo = client.get(reverse("accounts:painel", urlconf="core.urls_tenants")).content.decode()

    assert "Precisa de atencao" in corpo
    assert "Esperando voce" in corpo
