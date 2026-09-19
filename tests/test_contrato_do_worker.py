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


def _documento_de_teste():
    """Um PDF minusculo gravado num tenant, que e o que a extracao recebe."""
    import hashlib

    from django.core.files.base import ContentFile

    from apps.knowledge.models import Document, DocumentCategory

    bruto = b"%PDF-1.4 conteudo qualquer"
    categoria, _ = DocumentCategory.objects.get_or_create(name="Artigo", slug="artigo")
    return Document.objects.create(
        category=categoria,
        original_file=ContentFile(bruto, name="estudo.pdf"),
        file_sha256=hashlib.sha256(bruto).hexdigest(),
        file_size_bytes=len(bruto),
        status=Document.Status.UPLOADED,
    )


def _conexao_de_conversao():
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


def _resposta_falsa(monkeypatch, corpo: dict, status: int = 200):
    """A conversao usa `httpx.post` direto, nao um `Client`."""
    import httpx

    def post(url, **kwargs):
        return httpx.Response(status, json=corpo, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)


@pytest.mark.django_db
def test_a_extracao_le_a_resposta_de_conversao(monkeypatch, tenant_factory):
    """Aqui roda o adaptador de verdade, e nao so uma conferencia sobre o
    arquivo de exemplo: e a leitura de `markdown` e `duration_ms` que quebraria
    se o worker renomeasse um campo."""
    from django_tenants.utils import schema_context

    from apps.knowledge.extraction import _extrair_com_docling

    exemplo = _exemplo("conversao-resposta.json")
    _resposta_falsa(monkeypatch, exemplo)

    tenant = tenant_factory("contrato")
    with schema_context(tenant.schema_name):
        resultado = _extrair_com_docling(_documento_de_teste(), _conexao_de_conversao(), timeout=5)

    assert resultado.markdown == exemplo["markdown"]
    assert resultado.markdown.startswith("#")
    assert resultado.metodo == "docling"
    assert resultado.duracao_ms == exemplo["duration_ms"]


@pytest.mark.django_db
def test_o_503_na_conversao_adia_em_vez_de_falhar(monkeypatch, tenant_factory):
    """Mesmo contrato do texto e da imagem, no caminho do PDF: um 503 e a placa
    ocupada, nao um documento ruim. Tratado como falha, ele gastaria as
    tentativas de um envio que so precisava da vez."""
    from django_tenants.utils import schema_context

    from apps.knowledge.extraction import ConversorOcupado, _extrair_com_docling

    _resposta_falsa(monkeypatch, _exemplo("ocupada-resposta.json"), status=503)

    tenant = tenant_factory("contrato-503")
    with schema_context(tenant.schema_name):
        with pytest.raises(ConversorOcupado):
            _extrair_com_docling(_documento_de_teste(), _conexao_de_conversao(), timeout=5)


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
