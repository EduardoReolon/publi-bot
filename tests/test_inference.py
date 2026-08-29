"""Testes da camada de inferencia: reservas, disjuntor e retomada de trabalho."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inference.leases import (
    SemCapacidade,
    adquirir,
    escolher_conexao,
    liberar,
    liberar_expiradas,
    registrar_falha,
    registrar_sucesso,
    reserva,
)
from apps.inference.models import InferenceConnection, InferenceLease
from apps.inference.security import cifrar, decifrar, decifrar_chave, guardar_chave


@pytest.fixture
def conexao_gpu(db):
    """A GPU local: uma vaga, e so uma."""
    return InferenceConnection.objects.create(
        name="GPU local",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://100.64.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        max_concurrency=1,
        lease_seconds=3600,
    )


# ---------------------------------------------------------------------------
# Cifragem de credenciais
# ---------------------------------------------------------------------------


def test_credencial_vai_e_volta():
    original = "sk-uma-chave-de-terceiro"
    assert decifrar(cifrar(original)) == original


def test_texto_cifrado_nao_contem_a_chave():
    """Um dump do banco nao pode entregar a credencial de um cliente."""
    original = "sk-segredo-do-cliente"
    assert original.encode() not in cifrar(original)


@pytest.mark.django_db
def test_guardar_chave_registra_apenas_os_ultimos_quatro(conexao_gpu):
    """Os ultimos 4 permitem conferir QUAL chave esta cadastrada sem exibi-la."""
    guardar_chave(conexao_gpu, "sk-abcdefgh1234")
    conexao_gpu.save()
    conexao_gpu.refresh_from_db()

    assert conexao_gpu.api_key_last4 == "1234"
    assert decifrar_chave(conexao_gpu) == "sk-abcdefgh1234"


# ---------------------------------------------------------------------------
# Reserva de capacidade — o motivo de existir
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_segunda_reserva_e_recusada_quando_a_vaga_acabou(conexao_gpu):
    """Numa placa de 8 GB, duas inferencias simultaneas estouram a VRAM e o
    Ollama cai para CPU SEM erro — 15 vezes mais lento, sem sintoma. Serializar
    e requisito de corretude, nao ajuste de desempenho."""
    primeira = adquirir(conexao_gpu, owner_key="job-1")
    assert primeira.released_at is None

    with pytest.raises(SemCapacidade, match="simultanea"):
        adquirir(conexao_gpu, owner_key="job-2")


@pytest.mark.django_db
def test_liberar_devolve_a_vaga(conexao_gpu):
    primeira = adquirir(conexao_gpu, owner_key="job-1")
    liberar(primeira)

    segunda = adquirir(conexao_gpu, owner_key="job-2")
    assert segunda.pk != primeira.pk


@pytest.mark.django_db
def test_context_manager_libera_mesmo_com_excecao(conexao_gpu):
    """Sem isso, uma falha de rede deixaria a vaga ocupada ate a expiracao —
    horas de GPU parada por causa de um timeout."""
    with pytest.raises(ValueError), reserva(conexao_gpu, owner_key="job-1"):
        raise ValueError("a chamada falhou")

    # A vaga voltou.
    adquirir(conexao_gpu, owner_key="job-2")


@pytest.mark.django_db
def test_reserva_expirada_nao_bloqueia_para_sempre(conexao_gpu):
    """A queda de um worker segurando uma reserva nao pode inutilizar a
    conexao permanentemente."""
    velha = adquirir(conexao_gpu, owner_key="worker-morto")
    InferenceLease.objects.filter(pk=velha.pk).update(
        expires_at=timezone.now() - timedelta(minutes=5)
    )

    nova = adquirir(conexao_gpu, owner_key="worker-vivo")
    assert nova.pk != velha.pk

    velha.refresh_from_db()
    assert velha.released_at is not None


@pytest.mark.django_db
def test_conexao_com_mais_vagas_aceita_mais(db):
    hospedada = InferenceConnection.objects.create(
        name="API hospedada",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="https://api.exemplo.com",
        workloads=[InferenceConnection.Workload.TEXT],
        max_concurrency=3,
    )
    for i in range(3):
        adquirir(hospedada, owner_key=f"job-{i}")

    with pytest.raises(SemCapacidade):
        adquirir(hospedada, owner_key="job-4")


@pytest.mark.django_db
def test_conexao_inativa_recusa(conexao_gpu):
    conexao_gpu.is_active = False
    conexao_gpu.save()

    with pytest.raises(SemCapacidade, match="inativa"):
        adquirir(conexao_gpu, owner_key="job-1")


# ---------------------------------------------------------------------------
# Disjuntor
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_falhas_seguidas_abrem_o_circuito(conexao_gpu):
    """Sem o disjuntor, uma conexao fora do ar consome tentativa apos tentativa
    de todos os trabalhos da fila."""
    for _ in range(5):
        registrar_falha(conexao_gpu, limite=5, minutos=15)

    conexao_gpu.refresh_from_db()
    assert conexao_gpu.circuito_aberto
    assert conexao_gpu.health_status == InferenceConnection.Health.DOWN

    with pytest.raises(SemCapacidade, match="circuito aberto"):
        adquirir(conexao_gpu, owner_key="job-1")


@pytest.mark.django_db
def test_sucesso_fecha_o_circuito(conexao_gpu):
    for _ in range(5):
        registrar_falha(conexao_gpu, limite=5)
    conexao_gpu.refresh_from_db()
    assert conexao_gpu.circuito_aberto

    registrar_sucesso(conexao_gpu)
    conexao_gpu.refresh_from_db()

    assert not conexao_gpu.circuito_aberto
    assert conexao_gpu.consecutive_failures == 0
    assert conexao_gpu.health_status == InferenceConnection.Health.HEALTHY


# ---------------------------------------------------------------------------
# Escolha de conexao
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_conexao_do_tenant_tem_preferencia(conexao_gpu, tenant_factory, public_tenant):
    """Quem traz a propria chave nao deve disputar capacidade com os demais."""
    cliente = tenant_factory("com_api_propria")
    propria = InferenceConnection.objects.create(
        name="API do cliente",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="https://api.cliente.com",
        workloads=[InferenceConnection.Workload.TEXT],
        max_concurrency=5,
        tenant=cliente,
    )

    escolhida = escolher_conexao(workload="text", tenant=cliente)
    assert escolhida.pk == propria.pk

    # Outro tenant nao enxerga a conexao exclusiva.
    outro = escolher_conexao(workload="text", tenant=None)
    assert outro.pk == conexao_gpu.pk


@pytest.mark.django_db
def test_conexao_que_nao_atende_a_carga_e_ignorada(db):
    InferenceConnection.objects.create(
        name="So imagem",
        kind=InferenceConnection.Kind.IMAGE,
        base_url="http://127.0.0.1:7860",
        workloads=[InferenceConnection.Workload.IMAGE],
    )
    assert escolher_conexao(workload="text") is None


@pytest.mark.django_db
def test_sem_vaga_nenhuma_devolve_none(conexao_gpu):
    adquirir(conexao_gpu, owner_key="ocupando")
    assert escolher_conexao(workload="text") is None


@pytest.mark.django_db
def test_prefere_conexao_com_o_modelo_ja_carregado(db):
    """Trocar de modelo na VRAM custa de 10 a 60 segundos. Agrupar trabalho do
    mesmo modelo evita pagar isso a cada tarefa."""
    a = InferenceConnection.objects.create(
        name="A",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://a",
        workloads=["text"],
        max_concurrency=2,
    )
    b = InferenceConnection.objects.create(
        name="B",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://b",
        workloads=["text"],
        max_concurrency=2,
    )
    # B ja esta com o modelo carregado.
    adquirir(b, owner_key="em-curso", model_name="qwen2.5:7b")

    escolhida = escolher_conexao(workload="text", model_name="qwen2.5:7b")
    assert escolhida.pk == b.pk, "deveria reaproveitar o modelo ja carregado"
    assert a.pk != escolhida.pk


@pytest.mark.django_db
def test_liberar_expiradas_conta_quantas(conexao_gpu):
    lease = adquirir(conexao_gpu, owner_key="x")
    InferenceLease.objects.filter(pk=lease.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert liberar_expiradas() == 1
    assert liberar_expiradas() == 0


# ---------------------------------------------------------------------------
# Adaptador de geracao de imagem
# ---------------------------------------------------------------------------


def _resposta_de_imagens(quantas: int) -> dict:
    import base64

    return {
        "data": [
            {"b64_json": base64.b64encode(f"png-{i}".encode()).decode()} for i in range(quantas)
        ]
    }


def _cliente_de_imagem(monkeypatch, respostas: list[dict]):
    """Instala um httpx.Client falso que devolve as respostas em ordem."""
    import httpx

    from apps.inference.providers.openai_compatible import OpenAICompatibleImageClient

    corpos_enviados: list[dict] = []
    restantes = list(respostas)

    class ClienteFalso:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            corpos_enviados.append(json)
            dados = restantes.pop(0) if restantes else {"data": []}
            return httpx.Response(200, json=dados, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client", ClienteFalso)
    return OpenAICompatibleImageClient(base_url="https://gpu.exemplo.org"), corpos_enviados


def test_pede_b64_e_nao_url(monkeypatch):
    """A alternativa devolve um link do provedor que expira em cerca de uma
    hora; guarda-lo levaria a uma imagem quebrada na hora da publicacao."""
    cliente, enviados = _cliente_de_imagem(monkeypatch, [_resposta_de_imagens(3)])

    cliente.generate(model="m", prompt="uma cena", quantidade=3)

    assert enviados[0]["response_format"] == "b64_json"
    assert enviados[0]["n"] == 3


def test_provedor_que_ignora_n_ainda_entrega_o_lote(monkeypatch):
    """Alguns modelos recusam `n > 1` ou devolvem uma imagem so. O ponto e ter
    opcoes para comparar."""
    cliente, enviados = _cliente_de_imagem(
        monkeypatch, [_resposta_de_imagens(1), _resposta_de_imagens(1), _resposta_de_imagens(1)]
    )

    imagens = cliente.generate(model="m", prompt="uma cena", quantidade=3)

    assert len(imagens) == 3
    # Tres chamadas, pedindo o que ainda faltava em cada uma.
    assert [c["n"] for c in enviados] == [3, 2, 1]


def test_o_laco_nao_gira_para_sempre_quando_o_provedor_nao_devolve_nada(monkeypatch):
    from apps.inference.providers.base import ProviderPermanentError

    cliente, _ = _cliente_de_imagem(monkeypatch, [{"data": []}])

    with pytest.raises(ProviderPermanentError, match="nenhuma imagem"):
        cliente.generate(model="m", prompt="uma cena", quantidade=3)


def test_conexao_de_texto_nao_serve_para_imagem(db, conexao_gpu):
    """Este e o unico lugar que sabe qual classe atende qual tipo."""
    from apps.inference.providers.base import ProviderPermanentError, get_image_provider

    with pytest.raises(ProviderPermanentError, match="nao"):
        get_image_provider(conexao_gpu)
