"""Testes do pipeline de geracao — os componentes montados.

Estes testes existem porque a montagem faltava. Ate aqui havia `recuperar`,
`interpretar_tese` e `aplicar_rascunho`, cada um testado, e nenhum caminho que
os ligasse: nenhum `Fluxo` estava registrado, `criar_job` nunca era chamado, e
uma interface com botao "gerar artigo" nao teria o que acionar.

O modelo e falso de proposito. O que se testa aqui e a **sequencia**: que o
passo 2 recebe o que o passo 1 gravou, que o artigo nasce aguardando revisao, e
que as travas de link continuam valendo quando o texto vem por este caminho.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.content.models import Article, Question, Topic
from apps.content.services import garantir_prompts_padrao
from apps.inference.models import InferenceConnection
from apps.inference.providers.base import LLMResponse, ProviderTransientError
from apps.knowledge.models import Document, DocumentCategory, SuperChunk
from apps.knowledge.services import salvar_super_chunk
from apps.ops.models import GenerationJob, InferenceLog
from apps.ops.orchestrator import avancar, criar_job


@pytest.fixture(autouse=True)
def embedding_falso(settings):
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    # O cliente falso gera vetores por hash: deterministicos, mas sem relacao
    # semantica nenhuma entre si. Manter o limiar real aqui faria a recuperacao
    # devolver vazio sempre, e estes testes olham a SEQUENCIA dos passos. O
    # limiar tem teste proprio, medido com o modelo de verdade (ADR-0014).
    settings.RAG_MAX_COSINE_DISTANCE = 2.0
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


class ModeloFalso:
    """Devolve respostas roteirizadas, uma por chamada."""

    def __init__(self, respostas: list[str]):
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []

    def chat(self, *, model, system, user, temperature=0.2, max_tokens=None, json_schema=None):
        self.chamadas.append({"model": model, "system": system, "user": user})
        if not self.respostas:
            raise AssertionError("o fluxo chamou o modelo mais vezes que o esperado")
        return LLMResponse(
            text=self.respostas.pop(0),
            model=model,
            input_tokens=100,
            output_tokens=200,
            latency_ms=1234,
        )

    def health(self) -> bool:
        return True


TESE = json.dumps(
    {
        "tese": "As fontes convergem sobre o efeito observado.",
        "concordancia": "alta",
        "pontos_divergentes": [],
    }
)

ARTIGO = (
    "## O que a literatura mostra\n\n"
    "O efeito aparece de forma consistente nos estudos analisados [[FONTE_1]].\n\n"
    "## Limites\n\nA amostra ainda e pequena."
)

# A redacao acontece em rodadas: plano, uma chamada por secao, abertura e fecho,
# metadados. As respostas abaixo sao o roteiro dessa sequencia.
PLANO = json.dumps(
    {
        "palavra_chave": "efeito no metabolismo",
        "palavras_secundarias": ["taxa metabolica", "gasto energetico"],
        "intencao": "entender",
        "publico": "leitores nao especialistas",
        "secoes": [
            {
                "titulo": "O que a literatura mostra",
                "objetivo": "Resumir o achado principal",
                "palavras_chave": ["taxa metabolica"],
                "fontes": [1],
            },
            {
                "titulo": "Limites do que se sabe",
                "objetivo": "Dizer o que ainda nao esta estabelecido",
                "palavras_chave": ["gasto energetico"],
                "fontes": [1],
            },
        ],
    }
)

SECAO_A = "O efeito aparece de forma consistente nos estudos analisados [[FONTE_1]]."
SECAO_B = "A amostra ainda e pequena e os estudos sao de curta duracao."

MOLDURA = json.dumps(
    {
        "abertura": "Quem acompanha o tema encontra respostas conflitantes.",
        "fecho": "O quadro atual sustenta cautela, nao conclusao.",
    }
)

METADADOS = json.dumps(
    {
        "titulos": [
            "Efeito no metabolismo: o que os estudos mostram",
            "Metabolismo e exercicio: o que se sabe",
            "O que muda no metabolismo, segundo a literatura",
        ],
        "meta_description": "O que os estudos mostram sobre o efeito no metabolismo.",
        "resumo": "Uma leitura do que a literatura sustenta, e do que ainda nao.",
    }
)

# O roteiro completo do fluxo do artigo, na ordem em que o modelo e chamado.
ROTEIRO_DO_ARTIGO = [TESE, PLANO, SECAO_A, SECAO_B, MOLDURA, METADADOS]


@pytest.fixture
def tenant_com_acervo(tenant_factory, settings):
    """Um tenant com um documento indexado e uma conexao de inferencia."""
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
def conexao():
    """Conexao de inferencia — vive no schema public, compartilhada."""
    return InferenceConnection.objects.create(
        name="GPU local",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        default_model="modelo-de-teste",
        max_concurrency=1,
        is_active=True,
    )


def _rodar_ate_o_fim(job_id: str, maximo: int = 30) -> str:
    """Chama `avancar` repetidamente, como a task faz."""
    situacao = ""
    for _ in range(maximo):
        situacao = avancar(job_id)
        if situacao != GenerationJob.Status.PENDING:
            return situacao
    raise AssertionError("o fluxo nao terminou dentro do limite de passos")


# ---------------------------------------------------------------------------
# Artigo pilar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_fluxo_do_artigo_vai_da_pauta_ao_aguardando_revisao(
    tenant_com_acervo, conexao, monkeypatch
):
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo", target_keyword="metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    assert _rodar_ate_o_fim(str(job.pk)) == GenerationJob.Status.DONE

    artigo = Article.objects.get(topic=topic)
    assert artigo.status == Article.Status.PENDING_REVIEW
    assert artigo.consensus == Article.Consensus.HIGH
    assert artigo.word_count > 0

    # O marcador virou link para a URL do documento, e nao para algo escrito
    # pelo modelo.
    assert "https://revista.exemplo.org/estudo" in artigo.body_markdown
    assert "[[FONTE_1]]" not in artigo.body_markdown
    assert artigo.citations.count() == 1

    # A pauta nao pode ser sugerida de novo: ja existe artigo para ela.
    topic.refresh_from_db()
    assert topic.status == Topic.Status.USED


@pytest.mark.django_db
def test_o_segundo_passo_le_o_que_o_primeiro_gravou(tenant_com_acervo, conexao, monkeypatch):
    """O estado atravessa os passos pelo banco, nao pela memoria.

    E o que permite o processo morrer entre dois passos e o trabalho retomar
    de onde parou.
    """
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    avancar(str(job.pk))
    job.refresh_from_db()
    assert job.current_step == 1
    assert job.step_payloads["0"]["chunk_ids"]

    _rodar_ate_o_fim(str(job.pk))

    # O planejamento recebeu a tese produzida pelo passo anterior.
    prompt_do_plano = modelo.chamadas[1]["user"]
    assert "As fontes convergem" in prompt_do_plano


@pytest.mark.django_db
def test_sem_fonte_no_acervo_o_trabalho_falha_antes_de_chamar_o_modelo(
    tenant_factory, conexao, monkeypatch
):
    """Gerar sem fundamentacao gastaria inferencia para produzir o que o
    produto existe para evitar."""
    tenant = tenant_factory("sem_acervo")
    with schema_context(tenant.schema_name):
        garantir_prompts_padrao()

        def nao_deveria_chamar(*a, **k):
            raise AssertionError("o modelo foi chamado sem nenhuma fonte recuperada")

        monkeypatch.setattr("apps.content.inference.get_provider", nao_deveria_chamar)

        topic = Topic.objects.create(title="Tema sem nenhuma fonte no acervo")
        job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

        assert avancar(str(job.pk)) == GenerationJob.Status.FAILED

        job.refresh_from_db()
        assert "acervo" in job.last_error
        assert not Article.objects.filter(topic=topic).exists()


@pytest.mark.django_db
def test_link_escrito_pelo_modelo_derruba_o_passo(tenant_com_acervo, conexao, monkeypatch):
    """A trava contra link alucinado vale tambem quando o texto vem pelo fluxo.

    Nao basta a funcao recusar: o caminho que a chama precisa recusar junto,
    senao a protecao existe e nao e alcancada.
    """
    # A URL entra onde uma secao seria escrita. A trava roda por secao, e nao so
    # na montagem: derrubar a secao que produziu a URL custa uma chamada, e
    # derrubar so na montagem custaria todas as outras.
    modelo = ModeloFalso([TESE, PLANO, "Veja em https://site-inventado.exemplo/artigo o estudo."])
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    avancar(str(job.pk))  # recuperar fontes
    avancar(str(job.pk))  # filtrar consenso
    avancar(str(job.pk))  # planejar
    assert avancar(str(job.pk)) == GenerationJob.Status.FAILED

    job.refresh_from_db()
    assert "url" in job.last_error.lower() or "link" in job.last_error.lower()

    artigo = Article.objects.get(topic=topic)
    assert artigo.status == Article.Status.DRAFTING
    assert artigo.body_html == ""


@pytest.mark.django_db
def test_cada_chamada_ao_modelo_vira_registro(tenant_com_acervo, conexao, monkeypatch):
    """Sem o log nao ha como responder quanto custou um artigo."""
    modelo = ModeloFalso([TESE, ARTIGO])
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    logs = InferenceLog.objects.filter(job=job)
    assert logs.count() == 2
    assert all(log.succeeded for log in logs)
    assert sum(log.output_tokens for log in logs) == 400


@pytest.mark.django_db
def test_provedor_fora_do_ar_adia_em_vez_de_falhar(tenant_com_acervo, conexao, monkeypatch):
    """Indisponivel agora nao significa indisponivel sempre.

    Adiar preserva a tentativa: o trabalho volta a fila em vez de queimar uma
    das poucas que tem.
    """

    class Indisponivel:
        def chat(self, **kwargs):
            raise ProviderTransientError("connection refused")

        def health(self):
            return False

    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: Indisponivel())

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    avancar(str(job.pk))  # recuperacao, sem modelo
    assert avancar(str(job.pk)) == GenerationJob.Status.WAITING_CAPACITY

    job.refresh_from_db()
    assert job.next_attempt_at is not None
    assert job.current_step == 1  # nao avancou


@pytest.mark.django_db
def test_sem_conexao_de_inferencia_o_erro_diz_o_que_configurar(tenant_com_acervo):
    """A mensagem precisa apontar a configuracao que falta, nao um traceback."""
    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    avancar(str(job.pk))
    assert avancar(str(job.pk)) == GenerationJob.Status.FAILED

    job.refresh_from_db()
    assert "conexao de inferencia" in job.last_error


# ---------------------------------------------------------------------------
# Resposta a pergunta
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_fluxo_da_resposta_produz_resposta_aguardando_revisao(
    tenant_com_acervo, conexao, monkeypatch
):
    """Uma resposta publicada tem o mesmo peso de um artigo: mesma revisao."""
    from apps.integrations.models import Site

    site = Site.objects.create(
        name="Site", slug="site", base_url="https://site.exemplo.org", default_author="Dra. Souza"
    )
    resposta_do_modelo = "O que os estudos mostram sobre isso [[FONTE_1]] e consistente."
    modelo = ModeloFalso([resposta_do_modelo])
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    pergunta = Question.objects.create(
        site=site,
        remote_id="42",
        question_text="O efeito acontece mesmo?",
        submitted_at="2026-01-01T00:00:00Z",
        retention_until="2026-12-01T00:00:00Z",
    )
    job = criar_job(kind=GenerationJob.Kind.QA_ANSWER, target_object_id=str(pergunta.pk))

    assert _rodar_ate_o_fim(str(job.pk)) == GenerationJob.Status.DONE

    pergunta.refresh_from_db()
    assert pergunta.status == Question.Status.PENDING_REVIEW

    resposta = pergunta.answer
    assert resposta.status == resposta.Status.PENDING_REVIEW
    assert "https://revista.exemplo.org/estudo" in resposta.body_markdown
    assert resposta.author_name == "Dra. Souza"
    assert resposta.citations.count() == 1


# ---------------------------------------------------------------------------
# Redacao em rodadas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cada_secao_e_uma_chamada_com_contexto_proprio(tenant_com_acervo, conexao, monkeypatch):
    """O ponto do desenho: contexto pequeno por rodada.

    Um modelo pequeno escreve bem 300 palavras com as fontes daquela secao na
    frente, e mal um artigo inteiro. Se as secoes voltassem a ser escritas numa
    chamada so, este teste e o que denuncia.
    """
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    artigo = Article.objects.get(topic=topic)
    assert artigo.sections.count() == 2

    # Seis chamadas: tese, plano, duas secoes, moldura, metadados.
    assert len(modelo.chamadas) == 6

    primeira, segunda = modelo.chamadas[2], modelo.chamadas[3]
    assert "O que a literatura mostra" in primeira["user"]
    assert "Limites do que se sabe" in segunda["user"]
    # Cada secao recebe o esqueleto para nao invadir o assunto da outra.
    assert "Limites do que se sabe" in primeira["user"]


@pytest.mark.django_db
def test_o_trabalho_retoma_na_secao_seguinte(tenant_com_acervo, conexao, monkeypatch):
    """Interrompido no meio, refaz a secao que falta — nao o artigo.

    E o que separa `Continuar` de um laco dentro do passo: o laco perderia
    tudo, porque o progresso so existiria na memoria do processo morto.
    """
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    for _ in range(4):  # fontes, consenso, plano, primeira secao
        avancar(str(job.pk))

    artigo = Article.objects.get(topic=topic)
    escritas = [s for s in artigo.sections.all() if s.escrita]
    assert len(escritas) == 1

    # O passo nao avancou: ainda ha secao para escrever.
    job.refresh_from_db()
    assert job.current_step == 3

    _rodar_ate_o_fim(str(job.pk))
    artigo.refresh_from_db()
    assert all(s.escrita for s in artigo.sections.all())
    # A primeira secao nao foi reescrita: o modelo foi chamado uma vez por secao.
    assert len(modelo.chamadas) == 6


@pytest.mark.django_db
def test_plano_com_fonte_inventada_e_recusado(tenant_com_acervo, conexao, monkeypatch):
    """Numero de fonte que nao existe faria a secao ser escrita sem fonte — e
    o texto sairia mesmo assim, parecendo fundamentado."""
    plano_ruim = json.dumps(
        {
            "palavra_chave": "metabolismo",
            "secoes": [
                {"titulo": "Uma secao", "objetivo": "algo", "fontes": [7, 9]},
                {"titulo": "Outra secao", "objetivo": "algo", "fontes": [1]},
            ],
        }
    )
    modelo = ModeloFalso([TESE, plano_ruim])
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))

    avancar(str(job.pk))
    avancar(str(job.pk))
    assert avancar(str(job.pk)) == GenerationJob.Status.FAILED

    job.refresh_from_db()
    assert "fonte" in job.last_error.lower()


@pytest.mark.django_db
def test_abertura_e_fecho_veem_o_esqueleto_pronto(tenant_com_acervo, conexao, monkeypatch):
    """Escritos por ultimo de proposito: abertura feita antes do corpo promete
    o que o artigo nao cumpre."""
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    prompt_da_moldura = modelo.chamadas[4]["user"]
    assert "O que a literatura mostra" in prompt_da_moldura
    assert "Limites do que se sabe" in prompt_da_moldura

    artigo = Article.objects.get(topic=topic)
    assert artigo.body_markdown.startswith("Quem acompanha o tema")
    assert artigo.body_markdown.rstrip().endswith("nao conclusao.")


@pytest.mark.django_db
def test_a_montagem_passa_pela_trava_de_link(tenant_com_acervo, conexao, monkeypatch):
    """As secoes sao material de trabalho, nao um segundo caminho para o ar."""
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    artigo = Article.objects.get(topic=topic)
    assert "[[FONTE_1]]" not in artigo.body_markdown
    assert "https://revista.exemplo.org/estudo" in artigo.body_markdown
    # Os titulos das secoes viraram H2 no texto publicado.
    assert "## O que a literatura mostra" in artigo.body_markdown
    assert "## Limites do que se sabe" in artigo.body_markdown


@pytest.mark.django_db
def test_palavras_chave_e_metadados_ficam_no_artigo(tenant_com_acervo, conexao, monkeypatch):
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)

    topic = Topic.objects.create(title="Efeito no metabolismo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    artigo = Article.objects.get(topic=topic)
    assert artigo.focus_keyword == "efeito no metabolismo"
    assert "taxa metabolica" in artigo.secondary_keywords
    assert artigo.meta_description.startswith("O que os estudos mostram")
    assert artigo.excerpt
    # As opcoes de titulo ficam guardadas para a revisao escolher; o titulo do
    # artigo nao muda sozinho, porque a pauta ja o nomeou.
    assert len(artigo.thesis_json["titulos_sugeridos"]) == 3
    assert artigo.title == "Efeito no metabolismo"
