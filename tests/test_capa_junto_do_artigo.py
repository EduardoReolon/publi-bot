"""A capa sai junto com o artigo, e a falta dela nao derruba o artigo.

Antes, gerar o artigo deixava a tela de revisao sem nenhuma imagem: era preciso
um clique a mais, num botao que quem revisa so encontra rolando a pagina. O
passo de capa fecha a fila justamente para que a ilustracao esteja pronta
quando a pessoa abrir o texto.

A contrapartida e o risco oposto, e e ele que estes testes guardam: um passo
novo no fim de uma fila de sete nao pode transformar um artigo pronto num
trabalho falho. Sem conexao de imagem cadastrada — o caso de toda instalacao
que ainda nao tem gerador — o artigo tem de sair igual.
"""

from __future__ import annotations

import pytest

from apps.content.models import Article, Topic
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job, obter_fluxo

# `tenant_com_acervo` e `conexao` vem do conftest. Daqui vem so o roteiro do
# modelo falso, que e o mesmo do fluxo e nao deve divergir em duas copias.
from tests.test_flows import ROTEIRO_DO_ARTIGO, ModeloFalso, _rodar_ate_o_fim


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Mesma razao do arquivo do fluxo: o que se olha aqui e a sequencia."""


@pytest.fixture
def modelo_de_texto(monkeypatch):
    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)
    return modelo


def _gerar(titulo="Efeito no metabolismo"):
    topic = Topic.objects.create(title=titulo)
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))
    return Article.objects.get(topic=topic), GenerationJob.objects.get(pk=job.pk)


@pytest.mark.django_db
def test_o_artigo_ja_nasce_com_opcoes_de_capa(
    tenant_com_acervo, conexao, modelo_de_texto, imagem_falsa
):
    artigo, job = _gerar()

    assert job.status == GenerationJob.Status.DONE
    assert artigo.images.count() == 3
    assert {i.batch for i in artigo.images.all()} == {1}


@pytest.mark.django_db
def test_nenhuma_delas_entra_no_artigo_sozinha(
    tenant_com_acervo, conexao, modelo_de_texto, imagem_falsa
):
    """Gerar nao e escolher. Um artigo publicado com uma imagem que ninguem
    olhou e o mesmo problema de um texto que ninguem leu."""
    artigo, _ = _gerar()

    assert not artigo.images.filter(is_chosen=True).exists()


@pytest.mark.django_db
def test_sem_conexao_de_imagem_o_artigo_sai_igual(tenant_com_acervo, conexao, modelo_de_texto):
    """Este e o caso da instalacao real que motivou o passo: Ollama para texto,
    Docling para conversao, e nenhum gerador de imagem.

    O artigo e o produto. Marcar o trabalho como falho depois de ele ja estar
    gravado mandaria alguem investigar uma geracao que deu certo.
    """
    artigo, job = _gerar()

    assert job.status == GenerationJob.Status.DONE
    assert not job.last_error
    assert artigo.status == Article.Status.PENDING_REVIEW
    assert artigo.body_markdown.strip()
    assert artigo.images.count() == 0


@pytest.mark.django_db
def test_o_motivo_de_nao_ter_capa_fica_gravado(tenant_com_acervo, conexao, modelo_de_texto):
    """Sem o motivo no payload, a unica pista seria uma linha de log — e a tela
    diria "nenhuma opcao ainda", que e o texto de quem nunca pediu."""
    _, job = _gerar()

    ultimo = obter_fluxo(GenerationJob.Kind.PILLAR_ARTICLE).total - 1
    payload = job.step_payloads[str(ultimo)]

    assert payload["capas"] == 0
    assert "Configuracao > Inferencia" in payload["motivo"]


@pytest.mark.django_db
def test_maquina_ocupada_adia_em_vez_de_desistir(tenant_com_acervo, monkeypatch):
    """`SemCapacidade` nao e falha: e a placa em uso. Tratada como erro, ela
    gastaria as tentativas de um trabalho que so precisava da vez; tratada como
    "sem capa", devolveria um artigo sem imagem por um motivo passageiro."""
    from apps.content.flows import passo_gerar_capas
    from apps.inference.leases import SemCapacidade
    from apps.ops.orchestrator import PassoAdiado

    def ocupado(*a, **k):
        raise SemCapacidade("a GPU esta com o modelo de texto carregado")

    monkeypatch.setattr("apps.content.capas.gerar_opcoes", ocupado)

    artigo = Article.objects.create(title="X", body_markdown="Texto.")
    job = GenerationJob(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=str(artigo.pk),
        total_steps=8,
    )

    with pytest.raises(PassoAdiado) as erro:
        passo_gerar_capas(job)

    assert "GPU" in str(erro.value)


def test_capa_e_o_ultimo_passo():
    """Depois de montar, e nao antes: a descricao da capa usa o resumo e a meta
    description escritos no passo de metadados, e o artigo precisa ja estar
    gravado para que uma falha aqui custe so a imagem."""
    nomes = [p.nome for p in obter_fluxo(GenerationJob.Kind.PILLAR_ARTICLE).passos]

    assert nomes[-1] == "gerar capas"
    assert nomes.index("montar") < nomes.index("gerar capas")
    assert nomes.index("metadados de busca") < nomes.index("gerar capas")


def test_replanejar_nao_gera_lote_novo():
    """Nada e descartado, e nada e cobrado de novo sem pedido: replanejar mexe
    na estrutura do texto, e o botao da tela existe para quando a capa tambem
    tiver de mudar."""
    passos = [p.nome for p in obter_fluxo(GenerationJob.Kind.ARTICLE_REPLAN).passos]

    assert "gerar capas" not in passos


# ---------------------------------------------------------------------------
# O botao da tela de revisao tambem e um trabalho de fila
# ---------------------------------------------------------------------------
def test_o_botao_e_o_fluxo_do_artigo_executam_o_mesmo_passo():
    """Duas portas para a mesma coisa. Se fossem dois codigos, um deles
    envelheceria — e a divergencia apareceria como "pelo botao sai diferente"."""
    do_artigo = obter_fluxo(GenerationJob.Kind.PILLAR_ARTICLE).passos[-1]
    do_botao = obter_fluxo(GenerationJob.Kind.ARTICLE_COVER).passos[0]

    assert do_botao.executar is do_artigo.executar


def test_o_fluxo_do_botao_tem_um_passo_so():
    """Ele nao replaneja nem remonta: o artigo ja esta pronto."""
    assert obter_fluxo(GenerationJob.Kind.ARTICLE_COVER).total == 1


@pytest.mark.django_db
def test_o_passo_acha_o_artigo_pelo_alvo_do_trabalho(tenant_com_acervo, imagem_falsa):
    """No fluxo do artigo o id vem do payload do consenso; aqui o trabalho ja
    nasce apontando para o artigo. `_artigo_do_job` cobre os dois, e este teste
    e o que garante que o segundo caminho continua existindo."""
    from apps.content.flows import passo_gerar_capas

    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.ARTICLE_COVER,
        target_object_id=str(artigo.pk),
        total_steps=1,
    )

    resultado = passo_gerar_capas(job)

    assert resultado["capas"] == 3
    assert artigo.images.count() == 3


# ---------------------------------------------------------------------------
# O gerador que nao responde
# ---------------------------------------------------------------------------
# O caso real: o worker trocou de modelo de imagem, a carga passou do limite de
# memoria e o kernel freou o processo ate ele quase parar. Vivo, sem responder,
# segurando a placa. O cliente esgotou o tempo — e o botao "gerar mais" terminou
# como CONCLUIDO, sem imagem e sem aviso nenhum na tela.
class GeradorQueNaoResponde:
    def __init__(self):
        self.chamadas = 0

    def generate(self, **kwargs):
        from apps.inference.providers.base import ProviderTransientError

        self.chamadas += 1
        raise ProviderTransientError("nao foi possivel falar com o worker: timed out")


@pytest.fixture
def gerador_travado(monkeypatch, imagem_falsa):
    from apps.inference.models import InferenceConnection

    conexao_de_imagem = InferenceConnection.objects.create(
        name="Imagem",
        kind=InferenceConnection.Kind.IMAGE,
        base_url="http://127.0.0.1:8090",
        workloads=[InferenceConnection.Workload.IMAGE],
        default_model="modelo-de-imagem",
        max_concurrency=1,
        is_active=True,
    )
    gerador = GeradorQueNaoResponde()
    monkeypatch.setattr("apps.content.capas._conexao_de_imagem", lambda: conexao_de_imagem)
    monkeypatch.setattr("apps.inference.providers.base.get_image_provider", lambda *a, **k: gerador)
    return gerador


def _pedir_mais_capas(artigo):
    return GenerationJob.objects.create(
        kind=GenerationJob.Kind.ARTICLE_COVER,
        target_object_id=str(artigo.pk),
        total_steps=1,
    )


@pytest.mark.django_db
def test_gerador_sem_resposta_adia_em_vez_de_concluir(tenant_com_acervo, gerador_travado):
    from apps.ops.orchestrator import avancar

    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    job = _pedir_mais_capas(artigo)

    situacao = avancar(str(job.pk))

    job.refresh_from_db()
    assert situacao == GenerationJob.Status.WAITING_CAPACITY
    assert job.next_attempt_at is not None
    assert "timed out" in job.last_error


@pytest.mark.django_db
def test_botao_termina_falho_quando_as_tentativas_acabam(tenant_com_acervo, gerador_travado):
    """Concluido sem imagem e mentira para quem clicou. FALHO, com o motivo,
    e o que manda uma pessoa olhar o gerador — ou clicar de novo."""
    from apps.content.flows import TENTATIVAS_DE_IMAGEM
    from apps.ops.orchestrator import avancar

    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    job = _pedir_mais_capas(artigo)

    for _ in range(TENTATIVAS_DE_IMAGEM):
        GenerationJob.objects.filter(pk=job.pk).update(next_attempt_at=None)
        situacao = avancar(str(job.pk))

    job.refresh_from_db()
    assert situacao == GenerationJob.Status.FAILED
    assert job.status == GenerationJob.Status.FAILED
    assert "timed out" in job.last_error
    assert gerador_travado.chamadas == TENTATIVAS_DE_IMAGEM
    assert artigo.images.count() == 0


@pytest.mark.django_db
def test_no_fluxo_do_artigo_a_desistencia_nao_derruba_o_artigo(tenant_com_acervo, gerador_travado):
    """Esgotadas as tentativas, o artigo pronto continua pronto: o motivo vai
    para o payload, como ja ia na falta de conexao."""
    from apps.content.flows import TENTATIVAS_DE_IMAGEM, passo_gerar_capas
    from apps.ops.orchestrator import PassoAdiado

    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id=str(artigo.pk),
        total_steps=8,
    )

    for _ in range(TENTATIVAS_DE_IMAGEM - 1):
        with pytest.raises(PassoAdiado):
            passo_gerar_capas(job)
    resultado = passo_gerar_capas(job)

    assert resultado["capas"] == 0
    assert "timed out" in resultado["motivo"]


@pytest.mark.django_db
def test_a_tela_mostra_a_falha_mesmo_com_lotes_anteriores(tenant_com_acervo, imagem_falsa):
    """Com opcoes antigas na tela, o motivo sumia: a pagina ficava igual a de
    antes do clique."""
    from apps.content.capas import gerar_opcoes
    from apps.content.views import _motivo_sem_capa

    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    gerar_opcoes(artigo)
    job = _pedir_mais_capas(artigo)
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.FAILED, last_error="passo 0 (gerar capas): sem resposta"
    )

    assert artigo.images.exists()
    assert "sem resposta" in _motivo_sem_capa(artigo)


class GeradorQueQuebra:
    """O worker respondeu 500 `worker_travado` e se encerrou."""

    def __init__(self):
        self.chamadas = 0

    def generate(self, **kwargs):
        from apps.inference.providers.base import ProviderPermanentError

        self.chamadas += 1
        raise ProviderPermanentError(
            "o worker quebrou (worker_travado): passou de 900s", code="worker_travado"
        )


@pytest.mark.django_db
def test_worker_quebrado_falha_na_hora_sem_repetir(tenant_com_acervo, gerador_travado, monkeypatch):
    """Repetir sozinho so repete a falha e prende a placa de novo."""
    from apps.ops.orchestrator import avancar

    quebrado = GeradorQueQuebra()
    monkeypatch.setattr(
        "apps.inference.providers.base.get_image_provider", lambda *a, **k: quebrado
    )
    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")
    job = _pedir_mais_capas(artigo)

    situacao = avancar(str(job.pk))

    job.refresh_from_db()
    assert situacao == GenerationJob.Status.FAILED
    assert "worker_travado" in job.last_error
    assert quebrado.chamadas == 1


@pytest.mark.django_db
def test_503_do_worker_nao_abre_o_disjuntor(tenant_com_acervo, gerador_travado, monkeypatch):
    """Um 503 com codigo e o worker de pe dizendo "nao e a hora". Contado no
    disjuntor, minutos de disputa tirariam a conexao do ar para a fila toda."""
    from apps.content.capas import _conexao_de_imagem, gerar_opcoes
    from apps.inference.providers.base import ProviderTransientError

    class Ocupado:
        def generate(self, **kwargs):
            raise ProviderTransientError("HTTP 503", retry_after=40, code="gpu_ocupada")

    monkeypatch.setattr(
        "apps.inference.providers.base.get_image_provider", lambda *a, **k: Ocupado()
    )
    artigo = Article.objects.create(title="Pronto", body_markdown="Texto.")

    for _ in range(6):
        with pytest.raises(ProviderTransientError):
            gerar_opcoes(artigo)

    conexao = _conexao_de_imagem()
    conexao.refresh_from_db()
    assert conexao.consecutive_failures == 0
    assert conexao.circuit_open_until is None


def test_o_recuo_respeita_o_retry_after_como_piso():
    from apps.content.flows import RECUO_MAXIMO_SEGUNDOS, _espera_da_imagem

    assert _espera_da_imagem(1, 5) == 60
    assert _espera_da_imagem(1, 300) == 300
    assert _espera_da_imagem(3, None) == 240
    assert _espera_da_imagem(20, None) == RECUO_MAXIMO_SEGUNDOS
