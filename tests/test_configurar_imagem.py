"""O comando que cadastra a conexao de geracao de imagem.

Terceiro irmao de `configurar_inferencia` e `configurar_conversao`. A
diferenca que moldou o comando: **nao ter esta conexao e um estado legitimo.**
O texto e o produto, a ilustracao nao — o ultimo passo da geracao registra o
motivo e nao derruba o trabalho.

Isso muda o que se testa. Nos irmaos, o perigo e a conexao faltar sem ninguem
notar; aqui, o perigo e o contrario: uma conexao cadastrada que responde, gera
imagem, e esta em CPU — dezenas de vezes mais lenta, sem erro nenhum. Por isso
metade destes testes olha o que o `--testar` DIZ, e nao o que ele grava.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.inference.models import InferenceConnection
from apps.inference.security import decifrar_chave

pytestmark = pytest.mark.django_db

CONFIG = {
    "IMAGEM_NOME": "Geracao de imagem",
    "IMAGEM_BASE_URL": "http://127.0.0.1:8101",
    "IMAGEM_SEGREDO": "segredo-do-worker",
    "IMAGEM_MODELO": "stabilityai/stable-diffusion-xl-base-1.0",
}


def test_cria_a_conexao():
    with override_settings(**CONFIG):
        call_command("configurar_imagem")

    conexao = InferenceConnection.objects.get(name="Geracao de imagem")
    assert conexao.kind == InferenceConnection.Kind.IMAGE
    assert conexao.workloads == [InferenceConnection.Workload.IMAGE]
    assert conexao.base_url == "http://127.0.0.1:8101"
    assert conexao.default_model == "stabilityai/stable-diffusion-xl-base-1.0"
    # Uma geracao por vez: a placa e a mesma do Ollama e do Docling.
    assert conexao.max_concurrency == 1
    assert decifrar_chave(conexao) == "segredo-do-worker"


def test_a_conexao_criada_e_a_que_a_geracao_de_capa_encontra():
    """O teste que importa: nao basta gravar a linha, ela precisa ser a que
    `_conexao_de_imagem` acha — senao o artigo continua saindo sem capa, com a
    conexao cadastrada e tudo."""
    from apps.content.capas import _conexao_de_imagem

    with override_settings(**CONFIG):
        call_command("configurar_imagem")

    assert _conexao_de_imagem().base_url == "http://127.0.0.1:8101"


def test_e_idempotente_e_preserva_o_que_esta_no_banco(capsys):
    """Trocar o modelo pela tela nao pode ser desfeito pelo proximo deploy."""
    with override_settings(**CONFIG):
        call_command("configurar_imagem")

        InferenceConnection.objects.filter(name="Geracao de imagem").update(
            default_model="stabilityai/sdxl-turbo"
        )
        call_command("configurar_imagem")

    conexao = InferenceConnection.objects.get(name="Geracao de imagem")
    assert conexao.default_model == "stabilityai/sdxl-turbo"
    assert "ja existe e foi preservada" in capsys.readouterr().out


def test_atualizar_sobrescreve():
    with override_settings(**CONFIG):
        call_command("configurar_imagem")

    with override_settings(**{**CONFIG, "IMAGEM_MODELO": "stabilityai/sdxl-turbo"}):
        call_command("configurar_imagem", atualizar=True)

    conexao = InferenceConnection.objects.get(name="Geracao de imagem")
    assert conexao.default_model == "stabilityai/sdxl-turbo"
    assert InferenceConnection.objects.filter(kind=InferenceConnection.Kind.IMAGE).count() == 1


def test_atualizar_reabre_o_disjuntor():
    """Reconfigurar uma conexao que passou a tarde fora do ar nao pode deixar o
    circuito fechado por mais 15 minutos."""
    from datetime import timedelta

    from django.utils import timezone

    with override_settings(**CONFIG):
        call_command("configurar_imagem")

        InferenceConnection.objects.filter(name="Geracao de imagem").update(
            consecutive_failures=5, circuit_open_until=timezone.now() + timedelta(minutes=15)
        )
        call_command("configurar_imagem", atualizar=True)

    conexao = InferenceConnection.objects.get(name="Geracao de imagem")
    assert conexao.consecutive_failures == 0
    assert conexao.circuit_open_until is None


def test_sem_endereco_o_erro_lembra_que_o_ollama_nao_serve():
    """Este e o engano natural de quem ja tem o Ollama de pe: ele responde,
    tem modelos, e nao gera imagem nenhuma."""
    with override_settings(**{**CONFIG, "IMAGEM_BASE_URL": ""}):
        with pytest.raises(CommandError, match="Ollama nao serve"):
            call_command("configurar_imagem")


def test_sem_segredo_o_erro_explica_de_onde_ele_vem():
    with override_settings(**{**CONFIG, "IMAGEM_SEGREDO": ""}):
        with pytest.raises(CommandError, match="WORKER_SHARED_SECRET"):
            call_command("configurar_imagem")


def test_opcional_sem_endereco_nao_derruba_a_implantacao(capsys):
    """E assim que o release.sh chama. Uma instalacao sem gerador de imagem e
    um estado comum, nao um erro."""
    with override_settings(**{**CONFIG, "IMAGEM_BASE_URL": ""}):
        call_command("configurar_imagem", opcional=True)

    assert not InferenceConnection.objects.filter(kind=InferenceConnection.Kind.IMAGE).exists()
    assert "sem capa" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Dividir a placa
# ---------------------------------------------------------------------------
def test_avisa_com_quem_vai_dividir_a_maquina(capsys):
    """A partir do cadastro, gerar texto e gerar imagem passam a se revezar.
    E o que se quer numa placa so — mas o efeito visivel e "ficou mais lento
    para gerar artigo", e isso merece ser dito na hora, nao descoberto."""
    InferenceConnection.objects.create(
        name="LLM principal",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        default_model="qwen2.5:7b-instruct",
        is_active=True,
    )

    with override_settings(**CONFIG):
        call_command("configurar_imagem")

    saida = capsys.readouterr().out
    assert "'LLM principal'" in saida
    assert "se revezar" in saida


def test_nao_avisa_quando_a_placa_e_so_dela(capsys):
    """Um provedor pago, ou uma segunda maquina, nao disputa nada."""
    InferenceConnection.objects.create(
        name="LLM principal",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        default_model="qwen2.5:7b-instruct",
        is_active=True,
    )

    with override_settings(**{**CONFIG, "IMAGEM_BASE_URL": "https://api.exemplo.com"}):
        call_command("configurar_imagem")

    assert "se revezar" not in capsys.readouterr().out


def test_a_reserva_de_imagem_e_a_de_texto_disputam_a_mesma_placa():
    """O que de fato protege a VRAM. As duas conexoes tem `max_concurrency=1`
    cada, e sem a contagem por MAQUINA as duas ficariam ativas ao mesmo tempo
    — uma gerando texto, outra gerando imagem, na mesma placa de 8 GB."""
    from apps.inference.leases import SemCapacidade, adquirir

    texto = InferenceConnection.objects.create(
        name="LLM principal",
        kind=InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434",
        workloads=[InferenceConnection.Workload.TEXT],
        default_model="qwen2.5:7b-instruct",
        max_concurrency=1,
        is_active=True,
    )

    with override_settings(**CONFIG):
        call_command("configurar_imagem")
    imagem = InferenceConnection.objects.get(kind=InferenceConnection.Kind.IMAGE)

    adquirir(texto, owner_key="gerando-artigo")

    with pytest.raises(SemCapacidade):
        adquirir(imagem, owner_key="gerando-capa")


# ---------------------------------------------------------------------------
# --testar, contra um worker de mentira que responde como o de verdade
# ---------------------------------------------------------------------------
# "Como o de verdade" e a parte que da trabalho manter. O worker publica tres
# rotas no mesmo `/health/`, entao o estado da imagem vem ANINHADO em
# `imagem`. Estes testes ja fingiram a forma antiga, plana, e por isso
# continuaram verdes enquanto o comando lia `None` em tudo contra o worker
# real. A forma canonica esta em `tests/contrato_do_worker/saude-resposta.json`.
def _saude(**imagem) -> dict:
    """Uma resposta do `/health/` com a secao de imagem que o teste quiser."""
    return {
        "status": "ok",
        "service": "worker-gpu",
        "ocupada": False,
        "rotas": {"texto": True, "imagem": True, "conversao": True},
        "imagem": {"modelo": "sdxl", "dispositivo": "cuda", **imagem},
    }


def test_testar_relata_modelo_e_dispositivo(capsys, monkeypatch):
    _fingir_health(monkeypatch, _saude(ultimo_dispositivo="cuda"))

    with override_settings(**CONFIG):
        call_command("configurar_imagem", testar=True)

    saida = capsys.readouterr().out
    assert "modelo=sdxl" in saida
    assert "dispositivo=cuda" in saida


def test_testar_em_cpu_avisa_que_leva_minutos(capsys, monkeypatch):
    _fingir_health(monkeypatch, _saude(dispositivo="cpu"))

    with override_settings(**CONFIG):
        call_command("configurar_imagem", testar=True)

    saida = capsys.readouterr().out
    assert "MINUTOS" in saida
    assert "IMAGEM_DEVICE=cuda" in saida


def test_queda_para_cpu_apesar_de_cuda_e_relatada(capsys, monkeypatch):
    """O caso silencioso que motivou o campo. O worker pediu a placa, a VRAM
    faltou — porque o Ollama carregou um modelo no meio — e ele refez em CPU.
    A imagem sai, e o unico sintoma seria demora.

    Hoje o worker recusa com 503 em vez de cair para CPU, a menos que alguem
    ligue `IMAGEM_PERMITIR_CPU`. O campo continua valendo justamente para
    denunciar quem ligou.
    """
    _fingir_health(monkeypatch, _saude(ultimo_dispositivo="cpu"))

    with override_settings(**CONFIG):
        call_command("configurar_imagem", testar=True)

    saida = capsys.readouterr().out
    assert "caiu para CPU" in saida
    assert "faltou VRAM" in saida


def test_worker_inalcancavel_aponta_rede_e_tailscale(monkeypatch):
    import httpx

    def recusar(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", recusar)

    with override_settings(**CONFIG):
        with pytest.raises(CommandError, match="Tailscale"):
            call_command("configurar_imagem", testar=True)


def test_endereco_que_responde_outra_coisa_nao_passa_por_worker(monkeypatch):
    """Apontar para o Ollama por engano (11434 em vez de 8101) responde, mas
    nao e o servico de imagem."""
    _fingir_health(monkeypatch, {}, status=404)

    with override_settings(**CONFIG):
        with pytest.raises(CommandError, match="nao e o servico de imagem"):
            call_command("configurar_imagem", testar=True)


def test_provedor_sem_health_nao_e_tratado_como_falha(capsys, monkeypatch):
    """Um provedor pago nao tem `/health/`. Responder 200 com HTML nao e erro:
    so nao ha nada a relatar."""
    import httpx

    def get(url, **kwargs):
        return httpx.Response(200, text="<html>ok</html>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)

    with override_settings(**{**CONFIG, "IMAGEM_BASE_URL": "https://api.exemplo.com"}):
        call_command("configurar_imagem", testar=True)

    assert "respondeu 200" in capsys.readouterr().out


def _fingir_health(monkeypatch, corpo: dict, status: int = 200) -> None:
    import httpx

    def get(url, **kwargs):
        return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
