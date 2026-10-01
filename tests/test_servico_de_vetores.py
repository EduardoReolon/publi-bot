"""Servico de vetores: uma copia do modelo para a maquina toda.

Sobe o servidor HTTP de verdade numa porta livre, com um cliente falso no lugar
do modelo, e fala com ele pelo `ServicoDeVetoresClient`.
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from apps.knowledge.embeddings import (
    FakeEmbeddingClient,
    ServicoDeVetoresClient,
    VetorizacaoAdiada,
)
from apps.knowledge.servico_de_vetores import Servico, manipulador


class ModeloFalso(FakeEmbeddingClient):
    """O que o servico ve do modelo: texto preparado entra, vetor sai."""

    def __init__(self):
        super().__init__(model_name="intfloat/multilingual-e5-large")
        self.recebido: list[str] = []

    def _carregar(self):
        return self

    def vetorizar_preparados(self, entradas):
        self.recebido += entradas
        return [self._vetor(e) for e in entradas]


@pytest.fixture
def servico(settings):
    modelo = ModeloFalso()
    servico = Servico(cliente=modelo)
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), manipulador(servico))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    settings.EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
    settings.EMBEDDING_SERVICO_URL = f"http://127.0.0.1:{servidor.server_port}"
    yield servico
    servidor.shutdown()
    servidor.server_close()


def test_vetores_saem_iguais_aos_do_modelo_e_com_o_prefixo_certo(servico):
    servico.carregar()
    cliente = ServicoDeVetoresClient()
    modelo = servico.cliente

    passagens = cliente.embed_passage(["primeiro", "segundo"])
    consulta = cliente.embed_query("o que procuro")

    assert passagens[0] == pytest.approx(modelo._vetor("passage: primeiro"), abs=1e-6)
    assert passagens[1] == pytest.approx(modelo._vetor("passage: segundo"), abs=1e-6)
    # A consulta vai sem prefixo, como sempre foi: o limiar da busca foi medido assim.
    assert consulta == pytest.approx(modelo._vetor("o que procuro"), abs=1e-6)
    assert modelo.recebido == ["passage: primeiro", "passage: segundo", "o que procuro"]


def test_passagens_vao_em_lotes(servico):
    servico.carregar()
    textos = [f"t{i}" for i in range(ServicoDeVetoresClient.LOTE * 2 + 3)]

    vetores = ServicoDeVetoresClient().embed_passage(textos)

    assert len(vetores) == len(textos)
    assert vetores[-1] == pytest.approx(servico.cliente._vetor(f"passage: {textos[-1]}"), abs=1e-6)


def test_enquanto_carrega_o_pedido_e_adiado(servico):
    with pytest.raises(VetorizacaoAdiada) as erro:
        ServicoDeVetoresClient().embed_query("cedo demais")
    assert erro.value.retry_after == 30
    saude = httpx.get(f"{servico_url()}/saude").json()
    assert saude["pronto"] is False


def test_servico_fora_do_ar_e_adiado(settings):
    settings.EMBEDDING_SERVICO_URL = "http://127.0.0.1:9"
    with pytest.raises(VetorizacaoAdiada, match="nao respondeu"):
        ServicoDeVetoresClient().embed_passage(["x"])


def test_recusa_outro_modelo_e_entrada_invalida(servico):
    servico.carregar()
    url = f"{servico_url()}/v1/embeddings"

    assert httpx.post(url, json={"model": "outro", "input": ["x"]}).status_code == 400
    assert httpx.post(url, json={"input": []}).status_code == 400
    assert httpx.post(url, json={"input": [1, 2]}).status_code == 400
    assert httpx.post(url, json={"input": "um so"}).status_code == 200


def test_o_cliente_nunca_abre_o_modelo_no_proprio_processo():
    with pytest.raises(RuntimeError, match="servir_vetores"):
        ServicoDeVetoresClient()._carregar()


def servico_url():
    from django.conf import settings

    return settings.EMBEDDING_SERVICO_URL
