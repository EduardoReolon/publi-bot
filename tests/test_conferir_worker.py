"""`manage.py conferir_worker` — a conferencia que chama as rotas de verdade.

Os tres `configurar_*` respondem "o endereco responde". Este responde "o
caminho inteiro funciona", e a diferenca e todo o valor: um `/health/` verde
convive com credencial errada no banco, modelo ausente no disco e Docling que
explode na primeira pagina.

Aqui os adaptadores sao substituidos — o que se exercita e a conferencia:
que ela use o BANCO e nao o `.env`, que distinga bloco doente de rota
desligada, e que saia com erro quando algo falha, porque ela vai rodar em
script de implantacao.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave

SAUDE = json.loads(
    (Path(__file__).resolve().parent / "contrato_do_worker" / "saude-resposta.json").read_text(
        encoding="utf-8"
    )
)


def _saude(**trocas) -> dict:
    """A resposta real do worker, com o que o teste quiser mudar."""
    corpo = {c: v for c, v in SAUDE.items() if not c.startswith("_")}
    corpo.update(trocas)
    return corpo


@pytest.fixture
def conexoes(db):
    """As tres linhas, como ficam depois dos `configurar_*`."""
    criadas = {}
    for kind, nome, modelo in (
        (InferenceConnection.Kind.OPENAI_COMPATIBLE, "LLM principal", "qwen2.5:7b-instruct"),
        (InferenceConnection.Kind.IMAGE, "Geracao de imagem", "sdxl"),
        (InferenceConnection.Kind.DOCLING, "Conversao de PDF", ""),
    ):
        conexao = InferenceConnection(
            name=nome,
            kind=kind,
            base_url="http://worker:8090",
            default_model=modelo,
            is_active=True,
        )
        guardar_chave(conexao, "segredo")
        conexao.save()
        criadas[kind] = conexao
    return criadas


@pytest.fixture
def health_falso(monkeypatch):
    def instalar(corpo: dict, status: int = 200):
        def get(url, **kwargs):
            return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", get)

    instalar(_saude())
    return instalar


@pytest.fixture
def texto_falso(monkeypatch):
    from apps.inference.providers.base import LLMResponse

    class Falso:
        def chat(self, **kwargs):
            return LLMResponse(text="funcionou", model="qwen2.5:7b-instruct")

    monkeypatch.setattr("apps.inference.providers.base.get_provider", lambda *a, **k: Falso())
    return Falso


@pytest.mark.django_db
def test_o_caminho_feliz_do_texto(conexoes, health_falso, texto_falso, capsys):
    call_command("conferir_worker", "--rapido")

    saida = capsys.readouterr().out
    assert "[ok  ] /health/" in saida
    assert "[ok  ] texto" in saida
    assert "funcionou" in saida


@pytest.mark.django_db
def test_um_recurso_que_falha_derruba_o_comando(conexoes, health_falso, monkeypatch):
    """Ela vai rodar em script de implantacao: sair com zero depois de
    imprimir FALHOU faria a implantacao seguir como se estivesse tudo bem."""

    class Quebrado:
        def chat(self, **kwargs):
            raise RuntimeError("o modelo nao existe no disco")

    monkeypatch.setattr("apps.inference.providers.base.get_provider", lambda *a, **k: Quebrado())

    with pytest.raises(CommandError, match="nao respondeu como o PubliBot espera"):
        call_command("conferir_worker", "--rapido")


@pytest.mark.django_db
def test_um_bloco_doente_do_health_nao_passa_por_saudavel(conexoes, health_falso, texto_falso):
    """O `/health/` do worker nunca da 500: um bloco que falha vira
    `{"erro": ...}` com HTTP 200. Ler `.get()` direto diria "tudo certo"."""
    health_falso(_saude(conversao={"erro": "RuntimeError: docling nao carrega"}))

    with pytest.raises(CommandError):
        call_command("conferir_worker", "--rapido")


@pytest.mark.django_db
def test_rota_desligada_e_relatada_sem_ser_falha(conexoes, health_falso, texto_falso, capsys):
    """`IMAGEM_ATIVA=nao` e uma escolha de quem instalou, nao um defeito. Uma
    conferencia que falha por isso ensina a ignorar a conferencia."""
    corpo = _saude(rotas={"texto": True, "imagem": False, "conversao": True})
    del corpo["imagem"]
    health_falso(corpo)

    call_command("conferir_worker", "--rapido")

    assert "rota DESLIGADA no worker" in capsys.readouterr().out


@pytest.mark.django_db
def test_sem_conexao_cadastrada_diz_qual_comando_rodar(db, health_falso):
    with pytest.raises(CommandError, match="configurar_inferencia"):
        call_command("conferir_worker", "--rapido")


@pytest.mark.django_db
def test_le_o_endereco_do_banco_e_nao_do_env(conexoes, texto_falso, monkeypatch, settings, capsys):
    """O caso que ja aconteceu: `.env` corrigido, linha antiga preservada. Uma
    conferencia que lesse o `.env` diria que esta tudo certo enquanto os
    fluxos falam com o endereco velho."""
    settings.INFERENCIA_BASE_URL = "http://endereco-do-env:9999"

    vistas = []

    def get(url, **kwargs):
        vistas.append(url)
        return httpx.Response(200, json=_saude(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)

    call_command("conferir_worker", "--rapido")

    assert vistas == ["http://worker:8090/health/"]


@pytest.mark.django_db
def test_a_conversao_usa_a_mesma_funcao_do_fluxo(conexoes, health_falso, monkeypatch, capsys):
    """Se a conferencia tivesse um POST proprio, ela confirmaria o POST
    proprio. O `converter_no_worker` e o mesmo que `_extrair_com_docling`
    chama."""
    from apps.knowledge.extraction import ResultadoDaExtracao

    vistos = {}

    def converter(conexao, **kwargs):
        vistos.update(kwargs)
        return ResultadoDaExtracao(markdown="# Teste de conversao", metodo="docling")

    monkeypatch.setattr("apps.knowledge.extraction.converter_no_worker", converter)

    call_command("conferir_worker", "--so", "conversao")

    assert vistos["nome"] == "conferencia.pdf"
    assert vistos["conteudo"].startswith(b"%PDF")
    # O sha256 tem de ser o do conteudo enviado: o worker o confere, e um
    # valor errado viraria 422 em vez de conversao.
    import hashlib

    assert vistos["sha256"] == hashlib.sha256(vistos["conteudo"]).hexdigest()


@pytest.mark.django_db
def test_markdown_vazio_e_falha_e_nao_sucesso(conexoes, health_falso, monkeypatch):
    """Num PDF com camada de texto, Markdown vazio e o Docling falhando por
    dentro. Aceitar isso como "ok" e o defeito que a conferencia existe para
    pegar."""
    from apps.knowledge.extraction import ResultadoDaExtracao

    monkeypatch.setattr(
        "apps.knowledge.extraction.converter_no_worker",
        lambda *a, **k: ResultadoDaExtracao(markdown="   ", metodo="docling"),
    )

    with pytest.raises(CommandError):
        call_command("conferir_worker", "--so", "conversao")
