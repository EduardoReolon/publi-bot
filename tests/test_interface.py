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
from django.core.files.uploadedfile import SimpleUploadedFile
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
    "content:autores",
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
def autora(ambiente):
    """Quem assina. O cadastro e a fonte da verdade da assinatura."""
    from apps.content.models import Author

    return Author.objects.create(name="Dra. Souza", credentials="CRM 1")


@pytest.fixture
def artigo_para_revisar(ambiente, autora):
    documento = _documento_curado()
    artigo = Article.objects.create(
        title="Artigo em revisao",
        body_markdown="## Titulo\n\nTexto do artigo.",
        status=Article.Status.PENDING_REVIEW,
        consensus=Article.Consensus.HIGH,
        author=autora,
        author_name=autora.name,
        author_credentials=autora.credentials,
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
        "quando": "",
    }
    dados.update(extra)
    return dados


@pytest.mark.django_db
def test_aprovar_sem_autor_e_recusado(ambiente, artigo_para_revisar):
    """Conteudo sem autor identificado nao pode ser publicado."""
    _, _, client = ambiente
    Article.objects.filter(pk=artigo_para_revisar.pk).update(author=None, author_name="")

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(author=""),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_escolher_o_autor_copia_a_assinatura_para_o_artigo(ambiente, artigo_para_revisar, autora):
    """O cadastro pode ser renomeado depois; renomear alguem nao pode reescrever
    a assinatura de um artigo que ja saiu."""
    _, _, client = ambiente

    client.post(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        _dados_de_aprovacao(acao="salvar", author=str(autora.pk)),
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.author_id == autora.pk
    assert artigo_para_revisar.author_name == "Dra. Souza"
    assert artigo_para_revisar.author_credentials == "CRM 1"


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


# ---------------------------------------------------------------------------
# Excluir documento
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_exclusao_pede_confirmacao_antes(ambiente):
    """GET nao apaga. Um link seguido de clique certo nao pode ser suficiente
    para uma acao irreversivel."""
    _, _, client = ambiente
    documento = _documento_curado()

    resposta = client.get(
        reverse("knowledge:excluir", args=[documento.pk], urlconf="core.urls_tenants")
    )

    assert resposta.status_code == 200
    assert Document.objects.filter(pk=documento.pk).exists()


@pytest.mark.django_db
def test_exclusao_leva_os_trechos_junto(ambiente):
    """Deixar trecho orfao no indice faria a busca devolver conteudo de um
    documento que nao existe mais — e a citacao apontaria para o vazio."""
    _, _, client = ambiente
    documento = _documento_curado()
    assert SuperChunk.objects.filter(document=documento).exists()

    client.post(reverse("knowledge:excluir", args=[documento.pk], urlconf="core.urls_tenants"))

    assert not Document.objects.filter(pk=documento.pk).exists()
    assert not SuperChunk.objects.filter(document_id=documento.pk).exists()


@pytest.mark.django_db
def test_exclusao_nao_quebra_artigo_publicado(ambiente):
    """A citacao guarda titulo e URL copiados no momento em que foi feita.

    Sem isso, limpar o acervo estragaria texto ja publicado — e ninguem
    limparia o acervo.
    """
    from apps.content.models import ArticleCitation

    _, _, client = ambiente
    documento = _documento_curado()
    trecho = SuperChunk.objects.filter(document=documento).first()
    artigo = Article.objects.create(
        title="Artigo publicado",
        slug="artigo-publicado",
        status=Article.Status.PUBLISHED,
        body_markdown="corpo",
    )
    citacao = ArticleCitation.objects.create(
        article=artigo,
        super_chunk=trecho,
        rank=1,
        distance=0.1,
        source_title="Estudo sobre o efeito",
        source_url="https://revista.exemplo.org/estudo",
    )

    client.post(reverse("knowledge:excluir", args=[documento.pk], urlconf="core.urls_tenants"))

    citacao.refresh_from_db()
    assert citacao.super_chunk_id is None
    assert citacao.source_title == "Estudo sobre o efeito"
    assert citacao.source_url == "https://revista.exemplo.org/estudo"


# ---------------------------------------------------------------------------
# Revisao por secao
# ---------------------------------------------------------------------------
def _artigo_com_secoes() -> Article:
    from apps.content.models import ArticleSection

    artigo = Article.objects.create(
        title="Artigo em secoes",
        slug="artigo-em-secoes",
        status=Article.Status.PENDING_REVIEW,
        body_markdown="## Primeira\n\nUm.\n\n## Segunda\n\nDois.",
        focus_keyword="metabolismo",
        secondary_keywords=["taxa metabolica"],
    )
    ArticleSection.objects.create(
        article=artigo, order=1, heading="Primeira", intent="abrir", body_markdown="Um."
    )
    ArticleSection.objects.create(
        article=artigo, order=2, heading="Segunda", intent="fechar", body_markdown="Dois."
    )
    return artigo


@pytest.mark.django_db
def test_a_tela_separa_refazer_parte_de_refazer_tudo(ambiente):
    """As duas acoes precisam ficar visivelmente distantes.

    Alguem que queria consertar uma secao nao pode perder cinco boas por
    clicar no botao errado.
    """
    _, _, client = ambiente
    artigo = _artigo_com_secoes()

    corpo = client.get(
        reverse("content:revisar", args=[artigo.pk], urlconf="core.urls_tenants")
    ).content.decode()

    # Refazer parte: acao direta, com o escopo dito no proprio aviso.
    assert "Refazer as secoes marcadas" in corpo
    assert "SO as secoes marcadas" in corpo
    # Replanejar: nao e botao, e link para uma tela que explica o que se perde.
    assert "Replanejar tudo mesmo assim" not in corpo
    assert "Ver o que se perde ao replanejar" in corpo


@pytest.mark.django_db
def test_replanejar_pede_confirmacao_listando_o_que_se_perde(ambiente):
    _, _, client = ambiente
    artigo = _artigo_com_secoes()

    resposta = client.get(
        reverse("content:replanejar", args=[artigo.pk], urlconf="core.urls_tenants")
    )
    corpo = resposta.content.decode()

    assert resposta.status_code == 200
    assert "Primeira" in corpo and "Segunda" in corpo
    assert "Voltar e refazer so uma parte" in corpo
    # GET nao destroi nada.
    assert artigo.sections.count() == 2


@pytest.mark.django_db
def test_refazer_sem_marcar_nada_nao_faz_nada(ambiente):
    """Formulario vazio nao pode virar "refaz tudo" por omissao."""
    from apps.ops.models import GenerationJob

    _, _, client = ambiente
    artigo = _artigo_com_secoes()

    client.post(reverse("content:refazer_secoes", args=[artigo.pk], urlconf="core.urls_tenants"))

    assert not GenerationJob.objects.filter(kind=GenerationJob.Kind.ARTICLE_REDRAFT).exists()
    assert all(s.body_markdown for s in artigo.sections.all())


@pytest.mark.django_db
def test_refazer_esvazia_so_a_secao_marcada(ambiente):
    _, _, client = ambiente
    artigo = _artigo_com_secoes()

    client.post(
        reverse("content:refazer_secoes", args=[artigo.pk], urlconf="core.urls_tenants"),
        {"refazer": ["1"], "palavra_chave": "metabolismo basal"},
    )

    primeira, segunda = list(artigo.sections.all())
    assert primeira.body_markdown == ""
    assert segunda.body_markdown == "Dois."

    # Os parametros ajustados valem para a nova redacao: refazer com os mesmos
    # devolveria a mesma coisa.
    artigo.refresh_from_db()
    assert artigo.focus_keyword == "metabolismo basal"


@pytest.mark.django_db
def test_nao_dispara_dois_trabalhos_para_o_mesmo_artigo(ambiente):
    """Dois trabalhos escrevendo as mesmas secoes disputariam a mesma linha."""
    from apps.ops.models import GenerationJob

    _, _, client = ambiente
    artigo = _artigo_com_secoes()
    GenerationJob.objects.create(
        kind=GenerationJob.Kind.ARTICLE_REDRAFT,
        target_object_id=artigo.pk,
        status=GenerationJob.Status.PENDING,
        total_steps=2,
    )

    client.post(
        reverse("content:refazer_secoes", args=[artigo.pk], urlconf="core.urls_tenants"),
        {"refazer": ["1"]},
    )

    assert GenerationJob.objects.filter(kind=GenerationJob.Kind.ARTICLE_REDRAFT).count() == 1
    # A secao nao foi esvaziada: o pedido foi recusado antes.
    assert artigo.sections.get(order=1).body_markdown == "Um."


@pytest.mark.django_db
def test_editar_secao_a_mao_remonta_o_artigo(ambiente):
    """O corpo do artigo e derivado das secoes, e nao o contrario."""
    _, _, client = ambiente
    artigo = _artigo_com_secoes()

    client.post(
        reverse("content:salvar_secoes", args=[artigo.pk], urlconf="core.urls_tenants"),
        {
            "titulo_1": "Primeira, revisada",
            "secao_1": "Um, agora com mais fundamento.",
            "titulo_2": "Segunda",
            "secao_2": "Dois.",
        },
    )

    artigo.refresh_from_db()
    primeira = artigo.sections.get(order=1)
    assert primeira.heading == "Primeira, revisada"
    assert primeira.status == "edited"
    assert "## Primeira, revisada" in artigo.body_markdown
    assert "mais fundamento" in artigo.body_markdown


# ---------------------------------------------------------------------------
# Cadastro de autor
# ---------------------------------------------------------------------------


def _foto(formato: str = "PNG", tamanho=(60, 60)) -> SimpleUploadedFile:
    import io

    from PIL import Image

    memoria = io.BytesIO()
    Image.new("RGB", tamanho, (30, 90, 160)).save(memoria, format=formato)
    extensao = formato.lower()
    return SimpleUploadedFile(f"foto.{extensao}", memoria.getvalue(), f"image/{extensao}")


@pytest.mark.django_db
def test_cadastrar_autor_com_nome_apenas(ambiente):
    """So o nome e obrigatorio. Foto, contato e redes enriquecem a assinatura e
    podem faltar sem impedir a publicacao."""
    from apps.content.models import Author

    _, _, client = ambiente

    client.post(
        reverse("content:novo_autor", urlconf="core.urls_tenants"),
        {"name": "Marina Fisioterapeuta", "is_active": "on"},
    )

    autor = Author.objects.get()
    assert autor.name == "Marina Fisioterapeuta"
    assert not autor.photo


@pytest.mark.django_db
def test_foto_enviada_e_gravada_em_webp(ambiente):
    """A conversao acontece na entrada. Guardar o original e converter a cada
    publicacao deixaria dois formatos no disco."""
    from apps.content.models import Author

    _, _, client = ambiente

    client.post(
        reverse("content:novo_autor", urlconf="core.urls_tenants"),
        {"name": "Marina", "is_active": "on", "photo": _foto("PNG")},
    )

    autor = Author.objects.get()
    assert autor.photo.name.endswith(".webp")
    assert autor.photo.read()[:4] == b"RIFF"


@pytest.mark.django_db
def test_arquivo_que_nao_e_imagem_mostra_erro(ambiente):
    from apps.content.models import Author

    _, _, client = ambiente

    resposta = client.post(
        reverse("content:novo_autor", urlconf="core.urls_tenants"),
        {
            "name": "Marina",
            "photo": SimpleUploadedFile("curriculo.pdf", b"%PDF-1.4 nem de longe", "image/png"),
        },
    )

    assert resposta.status_code == 400
    assert not Author.objects.exists()


@pytest.mark.django_db
def test_links_sociais_ignoram_linhas_vazias(ambiente):
    """O formulario oferece linhas em branco; guardar as vazias encheria o
    payload de entradas sem endereco."""
    from apps.content.models import Author

    _, _, client = ambiente

    client.post(
        reverse("content:novo_autor", urlconf="core.urls_tenants"),
        {
            "name": "Marina",
            "is_active": "on",
            "link_label": ["Instagram", "", "LinkedIn"],
            "link_url": ["https://instagram.com/marina", "", ""],
        },
    )

    assert Author.objects.get().social_links == [
        {"label": "Instagram", "url": "https://instagram.com/marina"}
    ]


@pytest.mark.django_db
def test_excluir_autor_nao_apaga_a_assinatura_publicada(ambiente, artigo_para_revisar, autora):
    """`SET_NULL` no artigo: o nome foi copiado no momento da publicacao."""
    from apps.content.models import Author

    _, _, client = ambiente

    client.post(reverse("content:excluir_autor", args=[autora.pk], urlconf="core.urls_tenants"))

    artigo_para_revisar.refresh_from_db()
    assert not Author.objects.exists()
    assert artigo_para_revisar.author_id is None
    assert artigo_para_revisar.author_name == "Dra. Souza"


# ---------------------------------------------------------------------------
# Posicao dos links e ideia central
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_posicao_dos_links_e_escolhida_no_painel_de_refazer(ambiente, artigo_para_revisar):
    """E parametro de geracao, nao formatacao de tela: vale na proxima montagem
    do texto."""
    _, _, client = ambiente

    client.post(
        reverse(
            "content:refazer_secoes", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"
        ),
        {"posicao_dos_links": Article.LinkPlacement.END, "refazer": []},
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.link_placement == Article.LinkPlacement.END


@pytest.mark.django_db
def test_valor_invalido_de_posicao_nao_muda_nada(ambiente, artigo_para_revisar):
    _, _, client = ambiente

    client.post(
        reverse(
            "content:refazer_secoes", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"
        ),
        {"posicao_dos_links": "no-rodape-piscando", "refazer": []},
    )

    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.link_placement == Article.LinkPlacement.INLINE


@pytest.mark.django_db
def test_revisao_mostra_a_ideia_central_e_o_que_ela_exige(ambiente, artigo_para_revisar):
    """Quem revisa precisa saber qual frase e a que tem de estar embasada."""
    _, _, client = ambiente
    Article.objects.filter(pk=artigo_para_revisar.pk).update(
        central_idea="A medida domiciliar reduz o efeito do jaleco branco."
    )

    resposta = client.get(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants")
    )
    corpo = resposta.content.decode()

    assert "A medida domiciliar reduz o efeito do jaleco branco." in corpo
    assert "conhecimento geral" in corpo


# ---------------------------------------------------------------------------
# Resposta escrita a mao
# ---------------------------------------------------------------------------


@pytest.fixture
def pergunta(ambiente):
    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    return Question.objects.create(
        site=site,
        remote_id="42",
        question_text="Da para medir a pressao em casa?",
        submitted_at="2026-01-01T00:00:00Z",
        retention_until="2026-12-01T00:00:00Z",
    )


@pytest.mark.django_db
def test_responder_a_mao_abre_uma_resposta_em_branco(ambiente, pergunta):
    """Sem esta porta, uma pergunta que o acervo nao sustenta ficaria parada
    para sempre."""
    _, _, client = ambiente

    resposta_http = client.post(
        reverse("content:responder_a_mao", args=[pergunta.pk], urlconf="core.urls_tenants")
    )

    resposta = Answer.objects.get(question=pergunta)
    assert resposta.origin == Answer.Origin.MANUAL
    assert resposta.status == Answer.Status.PENDING_REVIEW
    assert resposta.body_markdown == ""
    # Leva direto para a tela de escrever.
    assert str(resposta.pk) in resposta_http["Location"]
    # E nao gasta inferencia nenhuma.
    assert not GenerationJob.objects.filter(kind=GenerationJob.Kind.QA_ANSWER).exists()


@pytest.mark.django_db
def test_resposta_a_mao_passa_pela_mesma_aprovacao(ambiente, pergunta, autora):
    """Um caminho mais curto para o texto humano seria uma segunda porta para o
    site do cliente."""
    _, _, client = ambiente
    resposta = Answer.objects.create(
        question=pergunta, origin=Answer.Origin.MANUAL, status=Answer.Status.PENDING_REVIEW
    )

    client.post(
        reverse("content:revisar_resposta", args=[resposta.pk], urlconf="core.urls_tenants"),
        {
            "acao": "aprovar",
            "body_markdown": "A medida em casa e possivel e util no acompanhamento.",
            "author": str(autora.pk),
            "quando": "",
        },
    )

    resposta.refresh_from_db()
    assert resposta.status == Answer.Status.APPROVED_SCHEDULED
    assert resposta.author_name == "Dra. Souza"
    assert resposta.reviewed_by is not None
    assert "<p>" in resposta.body_html


@pytest.mark.django_db
def test_resposta_sem_autor_nao_e_aprovada(ambiente, pergunta):
    _, _, client = ambiente
    resposta = Answer.objects.create(
        question=pergunta, origin=Answer.Origin.MANUAL, status=Answer.Status.PENDING_REVIEW
    )

    client.post(
        reverse("content:revisar_resposta", args=[resposta.pk], urlconf="core.urls_tenants"),
        {"acao": "aprovar", "body_markdown": "Texto qualquer.", "author": "", "quando": ""},
    )

    resposta.refresh_from_db()
    assert resposta.status == Answer.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_responder_a_mao_duas_vezes_e_recusado(ambiente, pergunta):
    _, _, client = ambiente
    Answer.objects.create(question=pergunta)

    client.post(reverse("content:responder_a_mao", args=[pergunta.pk], urlconf="core.urls_tenants"))

    assert Answer.objects.filter(question=pergunta).count() == 1


@pytest.mark.django_db
def test_pergunta_sem_fonte_oferece_a_escrita_a_mao(ambiente, pergunta):
    """A tela precisa oferecer o que de fato resolve. Gerar de novo contra o
    mesmo acervo daria o mesmo resultado."""
    _, _, client = ambiente
    Question.objects.filter(pk=pergunta.pk).update(status=Question.Status.NEEDS_MORE_SOURCES)

    corpo = client.get(reverse("content:perguntas", urlconf="core.urls_tenants")).content.decode()

    assert "responder-a-mao" in corpo
    assert "Gerar do acervo" not in corpo


# ---------------------------------------------------------------------------
# Capa: a escolha pela tela
# ---------------------------------------------------------------------------


@pytest.fixture
def opcoes_de_capa(artigo_para_revisar):
    """Tres opcoes ja geradas, como o lote as deixaria."""
    import io

    from PIL import Image

    from apps.content.models import ArticleImage

    criadas = []
    for posicao in range(1, 4):
        memoria = io.BytesIO()
        Image.new("RGB", (32, 32), (20 * posicao, 90, 160)).save(memoria, format="WEBP")
        imagem = ArticleImage(
            article=artigo_para_revisar,
            batch=1,
            order=posicao,
            prompt=f"cena {posicao}",
            alt_text="Uma capa",
        )
        imagem.image.save(f"capa-{posicao}.webp", ContentFile(memoria.getvalue()), save=True)
        criadas.append(imagem)
    return criadas


@pytest.mark.django_db
def test_a_tela_mostra_todas_as_opcoes_e_nenhuma_escolhida(
    ambiente, artigo_para_revisar, opcoes_de_capa
):
    """Nenhuma capa entra sozinha: o artigo so leva imagem se alguem escolher."""
    _, _, client = ambiente

    corpo = client.get(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants")
    ).content.decode()

    for opcao in opcoes_de_capa:
        assert str(opcao.pk) in corpo
    assert "esta e a capa" not in corpo


@pytest.mark.django_db
def test_escolher_pela_tela_marca_a_capa(ambiente, artigo_para_revisar, opcoes_de_capa):
    _, _, client = ambiente
    escolhida = opcoes_de_capa[1]

    client.post(
        reverse(
            "content:escolher_capa", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"
        ),
        {"imagem": str(escolhida.pk)},
    )

    escolhida.refresh_from_db()
    assert escolhida.is_chosen is True
    assert artigo_para_revisar.images.filter(is_chosen=True).count() == 1


@pytest.mark.django_db
def test_nao_da_para_escolher_a_capa_de_outro_artigo(ambiente, artigo_para_revisar, opcoes_de_capa):
    """A imagem chega pelo POST; sem o vinculo conferido, o id de outro artigo
    passaria."""
    _, _, client = ambiente
    outro = Article.objects.create(title="Outro")

    resposta = client.post(
        reverse("content:escolher_capa", args=[outro.pk], urlconf="core.urls_tenants"),
        {"imagem": str(opcoes_de_capa[0].pk)},
    )

    assert resposta.status_code == 404
    assert not outro.images.exists()


@pytest.mark.django_db
def test_sem_conexao_de_imagem_a_tela_explica(ambiente, artigo_para_revisar):
    """Erro de configuracao vira mensagem na tela, nao 500."""
    _, _, client = ambiente

    resposta = client.post(
        reverse("content:gerar_capas", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants"),
        follow=True,
    )

    assert resposta.status_code == 200
    assert "Inferencia" in resposta.content.decode()
