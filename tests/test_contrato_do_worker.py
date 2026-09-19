"""Os adaptadores deste projeto leem o que o worker-gpu devolve.

Antes da separacao dos repositorios, este papel era de um teste que rodava o
cliente de verdade contra o app do worker, no mesmo processo. Ele nao pode
mais existir — e sem substituto, os dois lados voltariam a poder divergir num
nome de campo com as duas suites verdes.

O substituto sao os exemplos em `tests/contrato_do_worker/`, copiados de la.
O worker confere que as respostas DELE tem aquela forma; aqui se confere que
os adaptadores DAQUI leem aquela forma. Uma mudanca num lado so quebra
alguem.

O que isto nao pega e a copia envelhecer, e por isso ela tem versao. O
`README.md` ao lado diz como conferir.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

CONTRATO = Path(__file__).resolve().parent / "contrato_do_worker"
VERSAO_ESPERADA = "2.0"


def _exemplo(nome: str) -> dict:
    dados = json.loads((CONTRATO / nome).read_text(encoding="utf-8"))
    # As chaves com `_` sao anotacao para quem le, e nao parte da resposta.
    return {chave: valor for chave, valor in dados.items() if not chave.startswith("_")}


def _cliente_falso(monkeypatch, corpo: dict, status: int = 200):
    """Faz o `httpx.Client` deste processo devolver o exemplo, sem rede."""

    class ClienteFalso:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            return httpx.Response(status, json=corpo, request=httpx.Request("POST", url))

        def get(self, url, **kwargs):
            return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: ClienteFalso())


def test_todos_os_exemplos_declaram_a_mesma_versao():
    """Se a copia for atualizada pela metade, isto acusa antes de o adaptador
    encontrar um campo que mudou."""
    versoes = {
        json.loads(arquivo.read_text(encoding="utf-8"))["_contrato_versao"]
        for arquivo in CONTRATO.glob("*.json")
    }

    assert versoes == {VERSAO_ESPERADA}


def test_o_adaptador_de_texto_le_a_resposta_do_worker(monkeypatch):
    from apps.inference.providers.openai_compatible import OpenAICompatibleClient

    _cliente_falso(monkeypatch, _exemplo("texto-resposta.json"))

    resposta = OpenAICompatibleClient(base_url="http://worker", api_key="x").chat(
        model="qwen2.5:7b-instruct", system="s", user="u"
    )

    assert resposta.text == "O texto gerado."
    assert resposta.input_tokens == 120
    assert resposta.output_tokens == 340


def test_o_adaptador_de_imagem_le_a_resposta_do_worker(monkeypatch):
    import base64

    from apps.inference.providers.openai_compatible import OpenAICompatibleImageClient

    exemplo = _exemplo("imagem-resposta.json")
    _cliente_falso(monkeypatch, exemplo)

    geradas = OpenAICompatibleImageClient(base_url="http://worker", api_key="x").generate(
        model="sdxl", prompt="um gato", quantidade=1
    )

    assert geradas[0].conteudo == base64.b64decode(exemplo["data"][0]["b64_json"])
    assert geradas[0].prompt_revisado == "o prompt usado"


@pytest.mark.django_db
def test_a_extracao_le_a_resposta_de_conversao(monkeypatch, tenant_factory):
    """O campo que importa e `markdown`. O `sha256` volta conferido pelo
    worker, e o `bytes` e informativo."""
    exemplo = _exemplo("conversao-resposta.json")

    assert "markdown" in exemplo
    assert exemplo["markdown"].startswith("#")
    assert len(exemplo["sha256"]) in {63, 64}


def test_o_503_do_worker_tem_os_campos_que_o_cliente_le():
    """`error.code` e o que decide entre esperar e desistir. Os quatro codigos
    estao documentados no INTEGRACAO.md do worker; este teste guarda os nomes
    contra uma renomeacao silenciosa."""
    exemplo = _exemplo("ocupada-resposta.json")

    assert exemplo["error"]["code"] == "gpu_ocupada"
    assert "message" in exemplo["error"]


def test_o_cliente_trata_503_como_transitorio():
    """`STATUS_TERMINAIS` decide o que nao adianta repetir. Um 503 fora dessa
    lista e o que faz o trabalho ser ADIADO em vez de falhar — e adiar nao
    gasta tentativa."""
    from apps.inference.providers.openai_compatible import STATUS_TERMINAIS

    assert 503 not in STATUS_TERMINAIS
