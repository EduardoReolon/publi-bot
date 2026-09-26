"""YouTube: comentarios viram demanda, videos viram candidatos, audio vira texto.

O que estes testes guardam:

* so comentario que PERGUNTA vira sinal — e nenhum LLM le os brutos;
* o video vira candidato a fonte, e o canal aprovado como confiavel aprova
  o video sozinho;
* legenda bloqueada nao perde o video: ele fica esperando o audio, com a
  instrucao de como consegui-lo;
* o audio segue o contrato do worker (503 adia, conexao cortada falha).
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django_tenants.utils import schema_context

from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte, Document
from apps.knowledge.videos import (
    LegendaIndisponivel,
    aprovar_video,
    id_do_video,
    markdown_da_transcricao,
)
from apps.radar.models import ChamadaExterna, ContasExternas, SinalDeDemanda
from apps.radar.youtube import colher_sinais, e_pergunta
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar carregaria o modelo real."""


@pytest.fixture
def tenant(tenant_factory):
    t = tenant_factory("youtube")
    with schema_context(t.schema_name):
        yield t


def _com_youtube():
    from apps.inference.security import cifrar

    contas = ContasExternas.carregar()
    contas.youtube_chave_ciphertext = cifrar("chave-youtube")
    contas.save()


BUSCA = {
    "items": [
        {
            "id": {"videoId": "abc123"},
            "snippet": {
                "title": "Como fazer orcamento de obra",
                "description": "Passo a passo.",
                "channelId": "UCcanal1",
                "channelTitle": "Engenharia Pratica",
                "publishedAt": "2026-02-01T10:00:00Z",
            },
        }
    ]
}

COMENTARIOS = {
    "items": [
        {
            "snippet": {
                "topLevelComment": {
                    "snippet": {"textOriginal": "Como calculo o BDI da obra?", "likeCount": 12}
                }
            }
        },
        {
            "snippet": {
                "topLevelComment": {
                    "snippet": {"textOriginal": "Otimo video, parabens!", "likeCount": 40}
                }
            }
        },
        {
            "snippet": {
                "topLevelComment": {
                    "snippet": {
                        "textOriginal": "qual planilha voce usa pra orcar reforma",
                        "likeCount": 3,
                    }
                }
            }
        },
    ]
}


def _youtube_responde(monkeypatch, *, busca=BUSCA, comentarios=COMENTARIOS, status=200, erro=""):
    def get(url, params=None, timeout=None):
        if status != 200:
            corpo = {"error": {"errors": [{"reason": erro}]}}
            return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))
        corpo = busca if url.endswith("/search") else comentarios
        return httpx.Response(200, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


# ---------------------------------------------------------------------------
# Unidades
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Como calculo o BDI da obra?", True),
        ("qual planilha voce usa pra orcar reforma", True),
        ("Otimo video, parabens!", False),
        ("?", False),
        ("Veja meu canal http://spam.com ?", False),
    ],
)
def test_so_pergunta_vira_sinal(texto, esperado):
    assert e_pergunta(texto) is esperado


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123&t=30s",
        "https://youtu.be/abc123",
        "https://youtube.com/shorts/abc123",
        "https://m.youtube.com/watch?v=abc123",
    ],
)
def test_id_do_video(url):
    assert id_do_video(url) == "abc123"


def test_transcricao_vira_secoes_por_janela_de_tempo():
    trechos = [(0, "Ola."), (30, "Hoje: BDI."), (95, "Primeiro passo."), (200, "Fim.")]

    markdown = markdown_da_transcricao("Video", trechos)

    assert markdown.startswith("# Video")
    assert "## 00:00\n\nOla. Hoje: BDI." in markdown
    assert "## 01:35\n\nPrimeiro passo." in markdown
    assert "## 03:20\n\nFim." in markdown


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_comentarios_que_perguntam_viram_sinais_e_o_video_vira_candidato(tenant, monkeypatch):
    _com_youtube()
    _youtube_responde(monkeypatch)

    sinais = colher_sinais(["orcamento de obra"], rodada=None, videos=1)

    assert {s.texto for s in sinais} == {
        "Como calculo o BDI da obra?",
        "qual planilha voce usa pra orcar reforma",
    }
    assert all(s.fonte == SinalDeDemanda.Fonte.YOUTUBE for s in sinais)
    candidato = CandidatoDeFonte.objects.get()
    assert candidato.tipo == "video"
    assert candidato.canal_nome == "Engenharia Pratica"
    assert candidato.dominio == "youtube.com/channel/UCcanal1"
    chamadas = ChamadaExterna.objects.filter(provedor="youtube")
    assert chamadas.count() == 2
    assert all(c.custo_usd == 0 for c in chamadas)


@pytest.mark.django_db
def test_cota_esgotada_e_erro_claro(tenant, monkeypatch):
    from apps.radar.provedores import ProvedorIndisponivel

    _com_youtube()
    _youtube_responde(monkeypatch, status=403, erro="quotaExceeded")

    with pytest.raises(ProvedorIndisponivel, match="cota"):
        colher_sinais(["x"], rodada=None, videos=1)


@pytest.mark.django_db
def test_sem_chave_nao_chama(tenant, monkeypatch):
    from apps.radar.provedores import ProvedorIndisponivel

    _youtube_responde(monkeypatch)
    with pytest.raises(ProvedorIndisponivel, match="chave"):
        colher_sinais(["x"], rodada=None, videos=1)


@pytest.mark.django_db
def test_canal_confiavel_aprova_o_video_sozinho(tenant, monkeypatch):
    from apps.knowledge.perfis import categoria_da_natureza

    _com_youtube()
    _youtube_responde(monkeypatch)
    CaminhoConfiavel.objects.create(
        prefixo="youtube.com/channel/UCcanal1",
        nivel="aprovar",
        categoria=categoria_da_natureza("video"),
    )
    monkeypatch.setattr(
        "apps.knowledge.videos.buscar_legenda", lambda video_id: [(0, "Texto do video sobre BDI.")]
    )

    colher_sinais(["orcamento"], rodada=None, videos=1)

    candidato = CandidatoDeFonte.objects.get()
    assert candidato.situacao == "aprovado"
    assert candidato.documento.status == Document.Status.CURATED
    assert candidato.documento.chunks.exists()


# ---------------------------------------------------------------------------
# Aprovar video: legenda ou audio
# ---------------------------------------------------------------------------
def _candidato_de_video():
    return CandidatoDeFonte.objects.create(
        url="https://www.youtube.com/watch?v=abc123",
        tipo="video",
        titulo="Como fazer orcamento",
        canal_id="UCcanal1",
        canal_nome="Engenharia Pratica",
    )


@pytest.mark.django_db
def test_video_com_legenda_vai_para_a_curadoria(tenant, monkeypatch):
    monkeypatch.setattr(
        "apps.knowledge.videos.buscar_legenda",
        lambda video_id: [(0, "Primeiro."), (120, "Depois.")],
    )

    candidato = aprovar_video(_candidato_de_video())

    documento = candidato.documento
    assert documento.status == Document.Status.PENDING_CURATION
    assert documento.extraction_method == "legenda"
    assert documento.authors == "Engenharia Pratica"
    assert documento.source_url == "https://www.youtube.com/watch?v=abc123"
    assert "## 02:00" in documento.markdown_full


@pytest.mark.django_db
def test_legenda_bloqueada_deixa_o_video_esperando_o_audio(tenant, monkeypatch):
    def bloqueada(video_id):
        raise LegendaIndisponivel("o YouTube recusou a leitura da legenda a partir deste servidor.")

    monkeypatch.setattr("apps.knowledge.videos.buscar_legenda", bloqueada)

    candidato = aprovar_video(_candidato_de_video())

    assert candidato.situacao == CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO
    assert "Baixe o audio" in candidato.motivo
    assert candidato.documento is None


@pytest.mark.django_db
def test_enviar_o_audio_pela_tela(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    candidato = _candidato_de_video()
    candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO
    candidato.save()
    iniciadas = []
    monkeypatch.setattr("apps.knowledge.tasks.iniciar_ingestao", lambda d: iniciadas.append(d))

    client.post(
        reverse("knowledge:enviar_audio", args=[candidato.pk], urlconf="core.urls_tenants"),
        {"audio": ContentFile(b"ID3 audio falso", name="video.mp3")},
    )

    candidato.refresh_from_db()
    assert candidato.situacao == "aprovado"
    assert candidato.documento.origin == Document.Origin.YOUTUBE
    assert candidato.documento.source_url == candidato.url
    assert len(iniciadas) == 1


# ---------------------------------------------------------------------------
# Transcricao no worker
# ---------------------------------------------------------------------------
def _conexao_do_worker():
    from apps.inference.models import InferenceConnection
    from apps.inference.security import guardar_chave

    conexao = InferenceConnection(
        name="Worker",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://worker:8090",
        is_active=True,
    )
    guardar_chave(conexao, "segredo")
    conexao.save()
    return conexao


def _audio():
    from apps.knowledge.perfis import categoria_da_natureza

    bruto = b"ID3 audio falso"
    return Document.objects.create(
        category=categoria_da_natureza("video"),
        original_file=ContentFile(bruto, name="video.mp3"),
        file_sha256=hashlib.sha256(bruto).hexdigest(),
        title="Como fazer orcamento",
    )


def _worker_responde(monkeypatch, *, status=200, corpo=None, excecao=None):
    enviados = []

    def post(url, files=None, data=None, headers=None, timeout=None):
        enviados.append({"url": url, "data": data, "headers": headers})
        if excecao is not None:
            raise excecao
        return httpx.Response(status, json=corpo or {}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    return enviados


@pytest.mark.django_db
def test_audio_e_transcrito_no_worker_com_marcas_de_tempo(tenant, monkeypatch):
    from apps.knowledge.extraction import extrair_markdown

    _conexao_do_worker()
    enviados = _worker_responde(
        monkeypatch,
        corpo={
            "text": "Ola. BDI.",
            "duration": 130.5,
            "segments": [{"start": 0.0, "text": "Ola."}, {"start": 100.0, "text": "BDI."}],
        },
    )

    resultado = extrair_markdown(_audio())

    assert enviados[0]["url"].endswith("/v1/audio/transcriptions")
    assert enviados[0]["headers"]["Authorization"] == "Bearer segredo"
    assert enviados[0]["data"]["response_format"] == "verbose_json"
    assert resultado.metodo == "audio"
    assert "## 01:40\n\nBDI." in resultado.markdown


@pytest.mark.django_db
def test_worker_ocupado_adia_a_transcricao(tenant, monkeypatch):
    from apps.knowledge.extraction import ConversorOcupado, extrair_markdown

    _conexao_do_worker()
    _worker_responde(
        monkeypatch, status=503, corpo={"error": {"code": "gpu_ocupada", "message": "ocupada"}}
    )

    with pytest.raises(ConversorOcupado):
        extrair_markdown(_audio())


@pytest.mark.django_db
def test_conexao_cortada_na_transcricao_e_falha(tenant, monkeypatch):
    from apps.knowledge.extraction import ExtracaoIndisponivel, extrair_markdown

    _conexao_do_worker()
    _worker_responde(monkeypatch, excecao=httpx.RemoteProtocolError("Server disconnected"))

    with pytest.raises(ExtracaoIndisponivel, match="caiu no meio"):
        extrair_markdown(_audio())


@pytest.mark.django_db
def test_worker_sem_a_rota_diz_onde_esta_a_especificacao(tenant, monkeypatch):
    from apps.knowledge.extraction import ExtracaoIndisponivel, extrair_markdown

    _conexao_do_worker()
    _worker_responde(monkeypatch, status=404, corpo={"detail": "Not Found"})

    with pytest.raises(ExtracaoIndisponivel, match="WORKER_TRANSCRICAO"):
        extrair_markdown(_audio())


# ---------------------------------------------------------------------------
# Na rodada do radar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rodada_com_youtube_ligado_colhe_os_comentarios(tenant, monkeypatch, settings):
    from apps.radar import coleta
    from apps.radar.coleta import executar_rodada
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "normal"
    config.usar_serp = False
    config.usar_youtube = True
    config.sementes = "orcamento de obra"
    config.save()
    _com_youtube()
    _youtube_responde(monkeypatch)
    monkeypatch.setattr(coleta, "propor_pautas", lambda grupos, limite: [])

    rodada = executar_rodada()

    assert rodada.situacao == "concluida", rodada.erro
    assert SinalDeDemanda.objects.filter(fonte="youtube").count() == 2
