"""Testes da saude da busca e do limiar por tenant.

O que se protege aqui e a unica coisa que o RAG nao avisa sozinho: um limiar
que deixou de fazer sentido. Ele nao levanta erro em lugar nenhum — so muda a
qualidade do que sai, semanas depois.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import TenantMembership, User
from apps.knowledge.models import (
    Document,
    DocumentCategory,
    RetrievalHit,
    RetrievalQuery,
    RetrievalSettings,
    SuperChunk,
)
from apps.knowledge.saude import alertas_da_busca, montar_resumo_da_busca
from apps.knowledge.services import recuperar, salvar_super_chunk


@pytest.fixture(autouse=True)
def embedding_falso(settings):
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def tenant_de_busca(tenant_factory):
    tenant = tenant_factory("busca")
    with schema_context(tenant.schema_name):
        DocumentCategory.objects.create(name="Artigo", slug="artigo")
        yield tenant


def _documento(sufixo: str = "a") -> Document:
    return Document.objects.create(
        category=DocumentCategory.objects.first(),
        title=f"Estudo {sufixo}",
        authors="Souza, M.",
        year=2024,
        source_url=f"https://revista.exemplo.org/{sufixo}",
        file_sha256=hashlib.sha256(sufixo.encode()).hexdigest(),
        original_file=ContentFile(b"pdf", name=f"{sufixo}.pdf"),
        license="cc_by",
        status=Document.Status.CURATED,
    )


def _consulta(*, distancias: list[float], quando=None, top_k: int = 3, chunk=None):
    """Uma consulta registrada, com os resultados que ela teria trazido."""
    registro = RetrievalQuery.objects.create(
        origin=RetrievalQuery.Origin.ARTICLE,
        query_text="consulta de teste",
        top_k=top_k,
        max_distance=0.16,
        embedding_model="fake",
    )
    if quando is not None:
        RetrievalQuery.objects.filter(pk=registro.pk).update(created_at=quando)
    for posicao, distancia in enumerate(distancias, start=1):
        RetrievalHit.objects.create(
            query=registro, super_chunk=chunk, distance=distancia, rank=posicao
        )
    return registro


# ---------------------------------------------------------------------------
# Limiar por tenant
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cada_tenant_tem_seu_proprio_limiar(tenant_factory, settings):
    """Dois clientes, dois acervos, dois limiares. Sem isso o ajuste de um
    cliente mudaria em silencio o filtro do outro."""
    settings.RAG_MAX_COSINE_DISTANCE = 0.16
    a = tenant_factory("acervo_a")
    b = tenant_factory("acervo_b")

    with schema_context(a.schema_name):
        config = RetrievalSettings.carregar()
        config.max_cosine_distance = 0.08
        config.save()

    with schema_context(b.schema_name):
        assert RetrievalSettings.carregar().max_cosine_distance == pytest.approx(0.16)

    with schema_context(a.schema_name):
        assert RetrievalSettings.carregar().max_cosine_distance == pytest.approx(0.08)


@pytest.mark.django_db
def test_carregar_e_idempotente(tenant_de_busca):
    with schema_context(tenant_de_busca.schema_name):
        primeiro = RetrievalSettings.carregar()
        segundo = RetrievalSettings.carregar()
        assert primeiro.pk == segundo.pk
        assert RetrievalSettings.objects.count() == 1


@pytest.mark.django_db
def test_recuperar_usa_o_limiar_do_tenant_e_nao_o_do_settings(tenant_de_busca, settings):
    """O `settings` vira ponto de partida; quem manda e a linha do schema."""
    settings.RAG_MAX_COSINE_DISTANCE = 2.0
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto qualquer."
        )

        config = RetrievalSettings.carregar()
        config.max_cosine_distance = 0.0
        config.save()

        registro, encontrados = recuperar(consulta="qualquer coisa", origem="article")
        assert encontrados == []
        # O limiar efetivamente aplicado fica gravado na consulta: e o que
        # torna a decisao auditavel depois.
        assert registro.max_distance == pytest.approx(0.0)


@pytest.mark.django_db
def test_calibracao_de_outro_modelo_e_detectada(tenant_de_busca, settings):
    settings.EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
    with schema_context(tenant_de_busca.schema_name):
        config = RetrievalSettings.carregar()
        config.calibrated_at = timezone.now()
        config.calibrated_model = "outro/modelo-antigo"
        config.save()

        assert config.calibracao_e_de_outro_modelo

        config.calibrated_model = "intfloat/multilingual-e5-large"
        assert not config.calibracao_e_de_outro_modelo


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_consulta_sem_resultado_nao_infla_o_total(tenant_de_busca):
    """A contagem total e a de vazias vem de consultas separadas de proposito:
    juntas num `aggregate` o JOIN de hits multiplicaria as linhas."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _consulta(distancias=[0.10, 0.12, 0.14], chunk=chunk)
        _consulta(distancias=[], chunk=chunk)

        resumo = montar_resumo_da_busca()
        assert resumo.atual.consultas == 2
        assert resumo.atual.sem_resultado == 1
        assert resumo.atual.percentual_sem_resultado == 50


@pytest.mark.django_db
def test_mediana_das_distancias_e_a_folga_ate_o_corte(tenant_de_busca):
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _consulta(distancias=[0.10, 0.12, 0.14], chunk=chunk)

        config = RetrievalSettings.carregar()
        config.max_cosine_distance = 0.20
        config.save()

        resumo = montar_resumo_da_busca()
        assert resumo.atual.distancia_mediana == pytest.approx(0.12)
        assert resumo.margem_ate_o_limiar == pytest.approx(0.08)
        assert not resumo.margem_esta_apertada


@pytest.mark.django_db
def test_margem_apertada_quando_a_mediana_encosta_no_limiar(tenant_de_busca):
    """O modo de falhar silencioso: as fontes ainda entram, mas por pouco."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _consulta(distancias=[0.155], chunk=chunk)

        config = RetrievalSettings.carregar()
        config.max_cosine_distance = 0.16
        config.save()

        resumo = montar_resumo_da_busca()
        assert resumo.margem_esta_apertada
        assert any("no limite" in alerta for alerta in alertas_da_busca(resumo))


@pytest.mark.django_db
def test_janela_anterior_e_separada_da_atual(tenant_de_busca):
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        agora = timezone.now()
        _consulta(distancias=[0.10], quando=agora - timedelta(days=1), chunk=chunk)
        _consulta(distancias=[], quando=agora - timedelta(days=40), chunk=chunk)
        _consulta(distancias=[], quando=agora - timedelta(days=45), chunk=chunk)

        resumo = montar_resumo_da_busca()
        assert resumo.atual.consultas == 1
        assert resumo.atual.sem_resultado == 0
        assert resumo.anterior.consultas == 2
        assert resumo.anterior.sem_resultado == 2


@pytest.mark.django_db
def test_documento_curado_e_nunca_citado_aparece(tenant_de_busca):
    """Alguem gastou tempo curando e o documento nunca sustentou nada. E o
    numero que diz se a curadoria esta indo para o lugar certo."""
    with schema_context(tenant_de_busca.schema_name):
        usado = _documento("usado")
        nunca = _documento("nunca")
        chunk = salvar_super_chunk(
            document=usado, kind=SuperChunk.Kind.ABSTRACT, content="Texto A."
        )
        salvar_super_chunk(document=nunca, kind=SuperChunk.Kind.ABSTRACT, content="Texto B.")
        _consulta(distancias=[0.10], chunk=chunk)

        resumo = montar_resumo_da_busca()
        assert resumo.documentos_indexados == 2
        assert resumo.documentos_nunca_citados == 1


@pytest.mark.django_db
def test_indice_com_modelo_misturado_e_denunciado(tenant_de_busca, settings):
    """Vetores de modelos diferentes nao sao comparaveis entre si, e a
    ordenacao vira sorteio sem erro nenhum no caminho."""
    settings.EMBEDDING_MODEL = "modelo-atual"
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        SuperChunk.objects.filter(pk=chunk.pk).update(embedding_model="modelo-de-antes")

        resumo = montar_resumo_da_busca()
        assert resumo.indice_tem_modelo_estranho
        assert any("outro modelo" in alerta for alerta in alertas_da_busca(resumo))


@pytest.mark.django_db
def test_alerta_de_muitas_buscas_vazias_respeita_a_amostra_minima(tenant_de_busca):
    """Duas consultas nao sustentam conclusao, e alerta por ruido ensina a
    ignorar alerta."""
    with schema_context(tenant_de_busca.schema_name):
        for _ in range(2):
            _consulta(distancias=[])
        resumo = montar_resumo_da_busca()
        assert not any("nao acharam" in alerta for alerta in alertas_da_busca(resumo))

        for _ in range(8):
            _consulta(distancias=[])
        resumo = montar_resumo_da_busca()
        assert any("nao acharam" in alerta for alerta in alertas_da_busca(resumo))


@pytest.mark.django_db
def test_histograma_separa_o_que_entra_do_que_fica_de_fora(tenant_de_busca):
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _consulta(distancias=[0.05, 0.30], chunk=chunk)

        config = RetrievalSettings.carregar()
        config.max_cosine_distance = 0.16
        config.save()

        resumo = montar_resumo_da_busca()
        assert resumo.faixas
        assert any(f.aceita and f.total for f in resumo.faixas)
        assert any(not f.aceita and f.total for f in resumo.faixas)
        # A escala nunca passa de 100%: a barra mais alta e a referencia.
        assert max(f.altura for f in resumo.faixas) == 100


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
@pytest.fixture
def navegador(tenant_de_busca, client, settings):
    usuario = User.objects.create_user(
        email="dono@busca.com", password="uma-senha-longa-de-teste", full_name="Dono"
    )
    TenantMembership.objects.create(tenant=tenant_de_busca, user=usuario, is_active=True)
    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant_de_busca.slug}.{settings.ROOT_DOMAIN}"
    return client


def _url(nome: str) -> str:
    return reverse(nome, urlconf="core.urls_tenants")


@pytest.mark.django_db
def test_tela_abre_com_acervo_vazio(navegador):
    resposta = navegador.get(_url("knowledge:busca"))
    assert resposta.status_code == 200
    assert b"Qualidade da busca" in resposta.content


@pytest.mark.django_db
def test_salvar_limiar_registra_modelo_e_autor(navegador, tenant_de_busca, settings):
    settings.EMBEDDING_MODEL = "modelo-atual"
    resposta = navegador.post(
        _url("knowledge:busca"),
        {"max_cosine_distance": "0.09", "top_k": "4", "consulta": "pressao na gravidez"},
    )
    assert resposta.status_code == 302

    with schema_context(tenant_de_busca.schema_name):
        config = RetrievalSettings.carregar()
        assert config.max_cosine_distance == pytest.approx(0.09)
        assert config.top_k == 4
        assert config.foi_calibrado
        assert config.calibrated_model == "modelo-atual"
        assert config.calibration_query == "pressao na gravidez"
        assert config.calibrated_by is not None


@pytest.mark.django_db
def test_limiar_fora_da_faixa_e_recusado(navegador, tenant_de_busca):
    """Distancia de cosseno vai de 0 a 2. Aceitar 5 aqui tornaria o filtro
    decorativo sem que nada indicasse isso na tela."""
    navegador.post(_url("knowledge:busca"), {"max_cosine_distance": "5", "top_k": "3"})
    with schema_context(tenant_de_busca.schema_name):
        assert not RetrievalSettings.carregar().foi_calibrado


@pytest.mark.django_db
def test_teste_de_consulta_nao_registra_nada(navegador, tenant_de_busca):
    """A medicao de calibracao nao pode entrar nas metricas que a propria tela
    mostra — senao calibrar piora o diagnostico."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto do estudo."
        )

    resposta = navegador.get(_url("knowledge:busca"), {"consulta": "efeito observado"})
    assert resposta.status_code == 200
    assert b"Distancia" in resposta.content

    with schema_context(tenant_de_busca.schema_name):
        assert RetrievalQuery.objects.count() == 0


@pytest.mark.django_db
def test_painel_do_tenant_mostra_o_estado_da_busca(navegador):
    resposta = navegador.get(_url("accounts:painel"))
    assert resposta.status_code == 200
    assert b"folga ate o corte" in resposta.content
    # O limiar de fabrica precisa aparecer como tal: um numero sem historia
    # parece conferido.
    assert b"valor de fabrica" in resposta.content


def _periodo(*, total: int, vazias: int, dias: int, chunk):
    for i in range(total):
        _consulta(
            distancias=[] if i < vazias else [0.10],
            quando=timezone.now() - timedelta(days=dias),
            chunk=chunk,
        )


@pytest.mark.django_db
def test_tendencia_denuncia_piora_real(tenant_de_busca):
    """5% -> 25%, com cinco buscas vazias: cresceu, passou do piso e tem
    amostra. Esse e o caso que merece alerta."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _periodo(total=20, vazias=1, dias=40, chunk=chunk)
        _periodo(total=20, vazias=5, dias=2, chunk=chunk)

        resumo = montar_resumo_da_busca()
        assert resumo.anterior.percentual_sem_resultado == 5
        assert resumo.atual.percentual_sem_resultado == 25
        assert resumo.piorou
        assert any("subiram de" in alerta for alerta in alertas_da_busca(resumo))
        # Abaixo de 30%: quem dispara aqui e a tendencia, nao o limiar absoluto.
        assert not any("nao acharam" in alerta for alerta in alertas_da_busca(resumo))


@pytest.mark.django_db
def test_uma_busca_vazia_virando_duas_nao_e_tendencia(tenant_de_busca):
    """Aritmeticamente 5% dobra para 10% e cruza o piso percentual. Duas buscas
    vazias, porem, nao sustentam a leitura — e alerta por ruido ensina a
    ignorar alerta."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _periodo(total=20, vazias=1, dias=40, chunk=chunk)
        _periodo(total=20, vazias=2, dias=2, chunk=chunk)

        resumo = montar_resumo_da_busca()
        assert resumo.atual.percentual_sem_resultado == 10
        assert not resumo.piorou


@pytest.mark.django_db
def test_consulta_abaixo_do_top_k_e_contada(tenant_de_busca):
    """Achou fonte, mas menos do que pediu: o acervo esta raso para o tema."""
    with schema_context(tenant_de_busca.schema_name):
        documento = _documento()
        chunk = salvar_super_chunk(
            document=documento, kind=SuperChunk.Kind.ABSTRACT, content="Texto."
        )
        _consulta(distancias=[0.10], top_k=3, chunk=chunk)
        _consulta(distancias=[0.10, 0.11, 0.12], top_k=3, chunk=chunk)
        _consulta(distancias=[], top_k=3, chunk=chunk)

        resumo = montar_resumo_da_busca()
        # so a primeira: a segunda cumpriu o alvo e a terceira nao achou nada.
        assert resumo.atual.abaixo_do_alvo == 1
