"""APIs externas: os adaptadores de voz (ElevenLabs, OpenAI, Google), a raiz
da API compativel com a OpenAI e o comando `configurar_api`."""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from django.core.management import call_command

from apps.inference.models import InferenceConnection
from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError
from apps.inference.providers.openai_compatible import raiz_da_api
from apps.inference.providers.voz import (
    ElevenLabsClient,
    GoogleVoiceClient,
    OpenAIVoiceClient,
    get_voice_provider,
)
from apps.inference.security import decifrar_chave


@pytest.fixture
def chamadas(monkeypatch):
    """httpx.post falso: guarda o pedido e devolve a resposta da vez."""
    feitas, respostas = [], []

    def post(url, **kwargs):
        feitas.append((url, kwargs))
        status, corpo = respostas.pop(0) if respostas else (200, b"MP3")
        conteudo = corpo if isinstance(corpo, bytes) else None
        return httpx.Response(
            status,
            content=conteudo,
            json=None if conteudo is not None else corpo,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    return feitas, respostas


def test_raiz_da_api_poe_v1_so_quando_falta():
    assert raiz_da_api("https://api.openai.com") == "https://api.openai.com/v1"
    assert raiz_da_api("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    gemini = "https://generativelanguage.googleapis.com/v1beta/openai"
    assert raiz_da_api(gemini) == gemini
    assert raiz_da_api("https://api.groq.com/openai") == "https://api.groq.com/openai/v1"


def test_elevenlabs_manda_a_voz_na_rota_e_a_chave_no_cabecalho(chamadas):
    feitas, _ = chamadas
    audio = ElevenLabsClient(base_url="https://api.elevenlabs.io", api_key="k").falar(
        texto="Ola", voz="abc"
    )
    url, kwargs = feitas[0]
    assert audio == b"MP3"
    assert url.startswith("https://api.elevenlabs.io/v1/text-to-speech/abc")
    assert kwargs["headers"]["xi-api-key"] == "k"
    assert kwargs["json"] == {"text": "Ola", "model_id": "eleven_multilingual_v2"}
    ElevenLabsClient(base_url="https://api.elevenlabs.io").falar(texto="Ola", voz="")
    assert ElevenLabsClient.VOZ_PADRAO in feitas[1][0]  # sem voz: a padrao


def test_openai_e_google(chamadas):
    feitas, respostas = chamadas
    OpenAIVoiceClient(base_url="https://api.openai.com", api_key="k").falar(
        texto="Ola", voz="nova", modelo="gpt-4o-mini-tts"
    )
    url, kwargs = feitas[0]
    assert url == "https://api.openai.com/v1/audio/speech"
    assert kwargs["json"]["voice"] == "nova" and kwargs["json"]["model"] == "gpt-4o-mini-tts"

    respostas.append((200, {"audioContent": base64.b64encode(b"som").decode()}))
    audio = GoogleVoiceClient(base_url="https://texttospeech.googleapis.com", api_key="g").falar(
        texto="Ola", voz="pt-BR-Neural2-B"
    )
    url, kwargs = feitas[1]
    assert audio == b"som" and url.endswith("/v1/text:synthesize")
    assert kwargs["params"] == {"key": "g"}
    assert kwargs["json"]["voice"] == {"languageCode": "pt-BR", "name": "pt-BR-Neural2-B"}


def test_erros_de_voz_viram_transitorio_ou_permanente(chamadas):
    _, respostas = chamadas
    cliente = ElevenLabsClient(base_url="https://api.elevenlabs.io", api_key="k")
    respostas.append((429, b"devagar"))
    with pytest.raises(ProviderTransientError):
        cliente.falar(texto="Ola", voz="v")
    respostas.append((401, b"chave errada"))
    with pytest.raises(ProviderPermanentError):
        cliente.falar(texto="Ola", voz="v")


@pytest.mark.django_db
def test_configurar_api_cria_e_atualiza_a_conexao(public_tenant):
    saida = io.StringIO()
    call_command("configurar_api", "gemini", "--chave", "AIza-teste", stdout=saida)
    conexao = InferenceConnection.objects.get(name="Gemini (API)")
    assert conexao.kind == InferenceConnection.Kind.OPENAI_COMPATIBLE
    assert conexao.base_url.endswith("/v1beta/openai")
    assert conexao.workloads == [InferenceConnection.Workload.OUTRA_IA]
    assert decifrar_chave(conexao) == "AIza-teste"
    assert "AIza-teste" not in saida.getvalue()

    call_command(
        "configurar_api", "gemini", "--chave", "outra", "--modelo", "gemini-x", stdout=saida
    )
    assert InferenceConnection.objects.filter(name="Gemini (API)").count() == 1
    assert InferenceConnection.objects.get(name="Gemini (API)").default_model == "gemini-x"

    call_command("configurar_api", "elevenlabs", "--chave", "xi", stdout=saida)
    voz = InferenceConnection.objects.get(name="ElevenLabs (voz)")
    assert voz.workloads == [InferenceConnection.Workload.VOZ]
    assert isinstance(get_voice_provider(voz), ElevenLabsClient)


# -- Animacao (dialeto /v1/videos: Sora e o worker) ---------------------------------------
@pytest.fixture
def servidor_de_video(monkeypatch):
    """httpx.request falso: guarda os pedidos e responde pela rota."""
    feitos, situacoes = [], ["queued", "in_progress", "completed"]
    respostas_extra = {}

    def request(metodo, url, **kwargs):
        feitos.append((metodo, url, kwargs))
        pedido = httpx.Request(metodo, url)
        if url in respostas_extra:
            status, corpo, cabecalhos = respostas_extra[url]
            return httpx.Response(status, json=corpo, headers=cabecalhos, request=pedido)
        if metodo == "POST":
            return httpx.Response(200, json={"id": "video_1", "status": "queued"}, request=pedido)
        if url.endswith("/content"):
            return httpx.Response(200, content=b"MP4", request=pedido)
        return httpx.Response(
            200, json={"id": "video_1", "status": situacoes.pop(0), "progress": 50}, request=pedido
        )

    monkeypatch.setattr(httpx, "request", request)
    return feitos, respostas_extra


def test_animacao_cria_espera_e_baixa(servidor_de_video):
    from apps.inference.providers.animacao import OpenAIVideoClient

    feitos, _ = servidor_de_video
    cliente = OpenAIVideoClient(base_url="https://api.openai.com", api_key="k")
    clipe = cliente.animar(
        imagem=b"PNG", prompt="slow blink", modelo="sora-2", dormir=lambda s: None
    )
    assert clipe == b"MP4"
    metodo, url, kwargs = feitos[0]
    assert (metodo, url) == ("POST", "https://api.openai.com/v1/videos")
    assert kwargs["data"] == {
        "prompt": "slow blink",
        "model": "sora-2",
        "seconds": "4",
        "size": "720x1280",
    }
    assert kwargs["files"]["input_reference"][1] == b"PNG"
    assert kwargs["headers"] == {"Authorization": "Bearer k"}
    assert [u for _, u, _ in feitos[1:]] == [
        "https://api.openai.com/v1/videos/video_1",
        "https://api.openai.com/v1/videos/video_1",
        "https://api.openai.com/v1/videos/video_1",
        "https://api.openai.com/v1/videos/video_1/content",
    ]


def test_animacao_placa_ocupada_e_transitoria_e_falha_e_permanente(servidor_de_video):
    from apps.inference.providers.animacao import OpenAIVideoClient

    _, extra = servidor_de_video
    cliente = OpenAIVideoClient(base_url="http://gpu:8090", api_key="s")
    extra["http://gpu:8090/v1/videos"] = (
        503,
        {"error": {"code": "gpu_ocupada"}},
        {"Retry-After": "40"},
    )
    with pytest.raises(ProviderTransientError, match="40s"):
        cliente.animar(imagem=b"PNG", prompt="p", modelo="ltx-video", dormir=lambda s: None)
    extra.clear()
    extra["http://gpu:8090/v1/videos/video_1"] = (
        200,
        {"id": "video_1", "status": "failed", "error": {"message": "sem VRAM"}},
        {},
    )
    with pytest.raises(ProviderPermanentError, match="sem VRAM"):
        cliente.animar(imagem=b"PNG", prompt="p", modelo="ltx-video", dormir=lambda s: None)


@pytest.mark.django_db
def test_configurar_api_do_worker_usa_o_env(public_tenant, settings):
    settings.IMAGEM_BASE_URL = "http://100.64.0.5:8090"
    settings.IMAGEM_SEGREDO = "segredo-do-worker"
    call_command("configurar_api", "worker_animacao", stdout=io.StringIO())
    conexao = InferenceConnection.objects.get(name="Worker (animacao)")
    assert conexao.base_url == "http://100.64.0.5:8090"
    assert conexao.workloads == [InferenceConnection.Workload.ANIMACAO]
    assert decifrar_chave(conexao) == "segredo-do-worker"
    call_command("configurar_api", "worker_voz", stdout=io.StringIO())
    voz = InferenceConnection.objects.get(name="Worker (voz)")
    assert isinstance(get_voice_provider(voz), OpenAIVoiceClient)
    with pytest.raises(Exception, match="--chave"):
        call_command("configurar_api", "anthropic", stdout=io.StringIO())


@pytest.mark.django_db
def test_maquina_ocupada_nao_vira_animacao_inexistente(public_tenant, settings):
    """Sem vaga (ou com o circuito aberto), pedir a animacao e "tente depois",
    nao "nenhuma animacao cadastrada"; e conferir o trabalho ja pedido nao
    disputa vaga: usa a conexao assim mesmo."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.inference.providers.base import ProviderTransientError
    from apps.social import fontes

    settings.IMAGEM_BASE_URL = "http://100.64.0.5:8090"
    settings.IMAGEM_SEGREDO = "segredo-do-worker"
    call_command("configurar_api", "worker_animacao", stdout=io.StringIO())
    conexao = InferenceConnection.objects.get(name="Worker (animacao)")
    conexao.circuit_open_until = timezone.now() + timedelta(minutes=5)
    conexao.save(update_fields=["circuit_open_until"])
    with pytest.raises(ProviderTransientError, match="ocupada"):
        fontes._animacao()
    assert fontes._animacao(para_pedir=False) == conexao
    conexao.is_active = False
    conexao.save(update_fields=["is_active"])
    with pytest.raises(Exception, match="nenhuma animacao cadastrada"):
        fontes._animacao(para_pedir=False)


@pytest.mark.django_db
def test_voz_do_kokoro_vai_no_worker_e_recusa_passa_para_a_outra(
    public_tenant, settings, monkeypatch
):
    """Duas vozes cadastradas (ElevenLabs e o worker): "pm_alex" e do Kokoro e
    vai no worker; se uma conexao recusa a voz, a fala tenta a outra."""
    from contextlib import nullcontext

    from apps.inference import leases
    from apps.inference.providers import voz as modulo_de_voz
    from apps.inference.providers.base import ProviderPermanentError
    from apps.social import fontes

    settings.IMAGEM_BASE_URL = "http://100.64.0.5:8090"
    settings.IMAGEM_SEGREDO = "segredo-do-worker"
    call_command("configurar_api", "elevenlabs", "--chave", "k", stdout=io.StringIO())
    call_command("configurar_api", "worker_voz", stdout=io.StringIO())
    worker = InferenceConnection.objects.get(name="Worker (voz)")
    assert fontes._conexoes_de_voz("pm_alex")[0] == worker

    falaram = []

    class Falso:
        def __init__(self, conexao):
            self.conexao = conexao

        def falar(self, *, texto, voz, modelo=""):
            falaram.append(self.conexao.name)
            if self.conexao != worker:
                raise ProviderPermanentError("voice_not_found")
            return b"MP3"

    monkeypatch.setattr(modulo_de_voz, "get_voice_provider", lambda c: Falso(c))
    monkeypatch.setattr(leases, "reserva", lambda *a, **k: nullcontext())
    monkeypatch.setattr(
        fontes,
        "_conexoes_de_voz",
        lambda voz: [c for c in InferenceConnection.objects.filter(is_active=True) if c != worker]
        + [worker],
    )
    assert fontes.falar("Ola", "pm_alex") == b"MP3"
    assert falaram[-1] == "Worker (voz)" and len(falaram) == 2
