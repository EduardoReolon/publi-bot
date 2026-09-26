"""Busca hibrida: o termo exato ajuda, mas nao passa por cima do limiar.

A busca vetorial aproxima mal sigla e nome proprio ("SINAPI", "BDI"). A
textual acerta esses, e erra o resto. Estes testes guardam a combinacao:

* o trecho que tem o termo exato sobe na ordem;
* o limiar continua sendo a trava — ter a palavra nao basta;
* a folga para o termo exato e pequena e so vale para quem casou por texto;
* o reordenador, quando ligado, decide a ordem final.

As distancias sao fixadas por um cliente de embedding de vetores escolhidos:
com o cliente falso de sempre elas seriam aleatorias, e nada aqui seria
testavel.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.knowledge.embeddings import EmbeddingClient
from apps.knowledge.models import Document, DocumentCategory, SuperChunk

DIM = 1024


def _vetor_a_distancia(distancia: float) -> list[float]:
    """Vetor unitario com distancia de cosseno `distancia` do eixo 0."""
    cosseno = 1.0 - distancia
    seno = math.sqrt(max(0.0, 1.0 - cosseno**2))
    vetor = np.zeros(DIM, dtype=np.float32)
    vetor[0], vetor[1] = cosseno, seno
    return vetor.tolist()


class VetoresEscolhidos(EmbeddingClient):
    """A consulta e o eixo 0; cada trecho fica a distancia que o teste mandar."""

    model_name = "vetores-escolhidos"
    dimensions = DIM
    distancias: dict[str, float] = {}

    def embed_query(self, texto):
        return _vetor_a_distancia(0.0)

    def embed_passage(self, textos):
        return [_vetor_a_distancia(self.distancias.get(t, 1.0)) for t in textos]

    def contar_tokens(self, texto):
        return max(1, len(texto) // 4)


@pytest.fixture
def acervo(tenant_factory, settings):
    settings.EMBEDDING_CLIENT = "tests.test_busca_hibrida.VetoresEscolhidos"
    settings.RAG_BUSCA_HIBRIDA = True
    settings.RAG_FOLGA_TEXTUAL = 0.03
    settings.RAG_RERANKER_MODEL = ""
    from apps.knowledge.embeddings import get_embedding_client
    from apps.knowledge.reranker import get_reordenador

    get_embedding_client.cache_clear()
    get_reordenador.cache_clear()
    VetoresEscolhidos.distancias = {}

    tenant = tenant_factory("hibrida")
    with schema_context(tenant.schema_name):
        yield DocumentCategory.objects.create(name="Norma", slug="norma")

    get_embedding_client.cache_clear()
    get_reordenador.cache_clear()


def _trecho(categoria, texto: str, distancia: float) -> SuperChunk:
    from apps.knowledge.services import salvar_super_chunk

    VetoresEscolhidos.distancias[texto] = distancia
    bruto = texto.encode()
    documento = Document.objects.create(
        category=categoria,
        original_file=ContentFile(bruto, name="d.md"),
        file_sha256=hashlib.sha256(bruto).hexdigest(),
        title=texto[:40],
        status=Document.Status.CURATED,
    )
    return salvar_super_chunk(document=documento, kind="custom", content=texto)


def _ids(consulta: str, **kwargs):
    from apps.knowledge.services import recuperar

    _, trechos = recuperar(consulta=consulta, origem="article", **kwargs)
    return [t.chunk.pk for t in trechos]


@pytest.mark.django_db
def test_o_trecho_com_o_termo_exato_sobe(acervo):
    _trecho(acervo, "Custos de referencia da construcao civil por estado.", 0.02)
    exato = _trecho(acervo, "A tabela SINAPI traz custos de referencia mensais.", 0.10)

    assert _ids("tabela SINAPI de setembro", top_k=1, distancia_maxima=0.2) == [exato.pk]


@pytest.mark.django_db
def test_sem_a_busca_textual_vale_so_a_distancia(acervo, settings):
    settings.RAG_BUSCA_HIBRIDA = False
    parecido = _trecho(acervo, "Custos de referencia da construcao civil por estado.", 0.02)
    _trecho(acervo, "A tabela SINAPI traz custos de referencia mensais.", 0.10)

    assert _ids("tabela SINAPI de setembro", top_k=1, distancia_maxima=0.2) == [parecido.pk]


@pytest.mark.django_db
def test_ter_a_palavra_nao_passa_por_cima_do_limiar(acervo):
    _trecho(acervo, "O SINAPI e mantido pela Caixa, e fala de outra coisa.", 0.60)

    assert _ids("tabela SINAPI", distancia_maxima=0.2) == []


@pytest.mark.django_db
def test_a_folga_so_vale_para_quem_casou_por_texto(acervo):
    com_termo = _trecho(acervo, "Tabela SINAPI de insumos.", 0.22)
    _trecho(acervo, "Relacao de insumos de obra.", 0.22)

    assert _ids("tabela SINAPI", distancia_maxima=0.2, top_k=5) == [com_termo.pk]


@pytest.mark.django_db
def test_o_indice_textual_ignora_acento_e_caixa(acervo):
    trecho = _trecho(acervo, "Cálculo do BDI em obras públicas.", 0.05)

    assert _ids("calculo bdi obras publicas", distancia_maxima=0.2) == [trecho.pk]
    trecho.refresh_from_db()
    assert trecho.search_vector


@pytest.mark.django_db
def test_o_reordenador_decide_a_ordem_final(acervo, settings):
    from apps.knowledge.reranker import get_reordenador

    settings.RAG_RERANKER_MODEL = "falso"
    get_reordenador.cache_clear()
    perto = _trecho(acervo, "Texto sobre outra coisa qualquer.", 0.01)
    relevante = _trecho(acervo, "Como calcular o BDI de uma obra publica.", 0.15)

    ordem = _ids("calcular BDI obra publica", distancia_maxima=0.2, top_k=2)

    assert ordem == [relevante.pk, perto.pk]


@pytest.mark.django_db
def test_reordenador_quebrado_nao_derruba_a_busca(acervo, monkeypatch):
    from apps.knowledge import reranker

    class Quebrado(reranker.Reordenador):
        model_name = "quebrado"

        def notas(self, consulta, textos):
            raise RuntimeError("modelo nao carregou")

    monkeypatch.setattr(reranker, "get_reordenador", lambda: Quebrado())
    trecho = _trecho(acervo, "Tabela de custos.", 0.05)
    _trecho(acervo, "Outra tabela de custos.", 0.08)

    assert _ids("tabela de custos", distancia_maxima=0.2, top_k=1) == [trecho.pk]
