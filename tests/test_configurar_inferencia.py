"""O comando que cria a conexao de inferencia numa instalacao nova.

Sem ele, o unico caminho e um formulario no admin — e esquecer esse passo faz a
aplicacao subir inteira, as telas abrirem e o erro aparecer so dentro do
primeiro job, longe da causa.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import decifrar_chave


@pytest.fixture
def semente(settings):
    settings.INFERENCIA_NOME = "LLM principal"
    settings.INFERENCIA_BASE_URL = "http://127.0.0.1:11434"
    settings.INFERENCIA_MODELO = "qwen2.5:7b-instruct"
    settings.INFERENCIA_API_KEY = ""
    settings.INFERENCIA_CONCORRENCIA = 1
    settings.INFERENCIA_RESERVA_SEGUNDOS = 3600
    return settings


@pytest.mark.django_db
def test_cria_a_conexao_compartilhada_com_a_carga_de_texto(semente):
    call_command("configurar_inferencia")

    conexao = InferenceConnection.objects.get(name="LLM principal")
    assert conexao.tenant_id is None, "a conexao e do sistema, nao de um tenant"
    assert conexao.kind == InferenceConnection.Kind.OPENAI_COMPATIBLE
    assert conexao.base_url == "http://127.0.0.1:11434"
    assert conexao.default_model == "qwen2.5:7b-instruct"
    assert InferenceConnection.Workload.TEXT in conexao.workloads
    assert conexao.is_active is True


@pytest.mark.django_db
def test_rodar_de_novo_nao_desfaz_ajuste_feito_na_tela(semente):
    """O deploy roda este comando toda vez. Se ele sobrescrevesse, trocar de
    modelo pelo admin duraria ate a proxima implantacao."""
    call_command("configurar_inferencia")

    InferenceConnection.objects.filter(name="LLM principal").update(
        default_model="modelo-escolhido-na-tela"
    )
    semente.INFERENCIA_MODELO = "modelo-do-env"

    call_command("configurar_inferencia")

    conexao = InferenceConnection.objects.get(name="LLM principal")
    assert conexao.default_model == "modelo-escolhido-na-tela"
    assert InferenceConnection.objects.count() == 1


@pytest.mark.django_db
def test_atualizar_sobrescreve_quando_e_essa_a_intencao(semente):
    call_command("configurar_inferencia")
    semente.INFERENCIA_MODELO = "llama3.1:8b"

    call_command("configurar_inferencia", "--atualizar")

    assert InferenceConnection.objects.get().default_model == "llama3.1:8b"


@pytest.mark.django_db
def test_reconfigurar_reabre_o_disjuntor(semente):
    """Uma conexao que passou a tarde fora do ar fica com o circuito aberto por
    15 minutos. Sem reabrir aqui, a pessoa reconfigura, nada acontece, e ela
    conclui que o comando nao funcionou."""
    from datetime import timedelta

    from django.utils import timezone

    call_command("configurar_inferencia")
    InferenceConnection.objects.update(
        consecutive_failures=5,
        circuit_open_until=timezone.now() + timedelta(minutes=15),
        health_status=InferenceConnection.Health.DOWN,
    )

    call_command("configurar_inferencia", "--atualizar")

    conexao = InferenceConnection.objects.get()
    assert conexao.circuito_aberto is False
    assert conexao.consecutive_failures == 0


@pytest.mark.django_db
def test_a_chave_e_guardada_cifrada(semente):
    """O Ollama nao exige chave; as demais APIs compativeis exigem."""
    semente.INFERENCIA_API_KEY = "chave-secreta-1234"

    call_command("configurar_inferencia")

    conexao = InferenceConnection.objects.get()
    assert decifrar_chave(conexao) == "chave-secreta-1234"
    assert conexao.api_key_last4 == "1234"
    assert b"chave-secreta" not in bytes(conexao.api_key_ciphertext)


@pytest.mark.django_db
def test_sem_endereco_o_comando_diz_o_que_preencher(semente):
    semente.INFERENCIA_BASE_URL = ""

    with pytest.raises(CommandError, match="INFERENCIA_BASE_URL"):
        call_command("configurar_inferencia")


@pytest.mark.django_db
def test_sem_modelo_o_comando_diz_onde_achar_o_nome(semente):
    """Um nome que nao existe no servidor devolve 404 em toda chamada, e a
    mensagem do Ollama nao diz que o problema e o nome."""
    semente.INFERENCIA_MODELO = ""

    with pytest.raises(CommandError, match="ollama list"):
        call_command("configurar_inferencia")


@pytest.mark.django_db
def test_endpoint_inacessivel_aponta_para_rede(semente):
    """Distinguir os dois motivos e o ponto do `--testar`: rede e endereco
    errado mandam a pessoa para lugares diferentes."""
    semente.INFERENCIA_BASE_URL = "http://127.0.0.1:59999"

    with pytest.raises(CommandError, match="nao foi possivel chegar"):
        call_command("configurar_inferencia", "--testar")
