"""Testes com o modelo de embedding REAL.

Marcados como `integration` porque carregam cerca de 2 GB e levam alguns
segundos. Rodam com:

    pytest -m integration

Existem porque as garantias que eles verificam nao podem ser testadas com o
cliente falso: a compatibilidade multilingue e a faixa de distancias sao
propriedades do modelo, nao do codigo.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cliente_real():
    from django.conf import settings

    from apps.knowledge.embeddings import FastEmbedClient

    try:
        cliente = FastEmbedClient()
        cliente.embed_query("aquecimento")
    except Exception as exc:  # modelo ausente no ambiente
        pytest.skip(f"modelo de embedding indisponivel: {exc}")
    assert cliente.dimensions == settings.EMBEDDING_DIM
    return cliente


def test_dimensao_bate_com_a_coluna_do_banco(cliente_real):
    """Se divergir, todo INSERT no pgvector falha."""
    v = cliente_real.embed_query("teste")
    assert len(v) == cliente_real.dimensions == 1024


def test_vetores_saem_normalizados(cliente_real):
    """O modelo devolve norma proxima de 29, nao 1. Normalizar na gravacao
    mantem a distancia de cosseno comparavel entre execucoes."""
    import numpy as np

    v = cliente_real.embed_passage(["uma passagem qualquer"])[0]
    assert pytest.approx(1.0, abs=1e-4) == float(np.linalg.norm(v))


def test_recuperacao_atravessa_idiomas(cliente_real):
    """A razao de ser do modelo multilingue: literatura cientifica em ingles
    precisa ser encontrada por uma consulta em portugues."""
    import numpy as np

    def cos(a, b):
        return float(np.dot(a, b))

    q = cliente_real.embed_query("monitoramento de pressao alta na gravidez")
    pt, en, fora = cliente_real.embed_passage(
        [
            "A hipertensao gestacional exige monitoramento continuo da pressao arterial.",
            "Gestational hypertension requires continuous blood pressure monitoring.",
            "Receita de bolo de cenoura com cobertura de chocolate.",
        ]
    )

    assert cos(q, pt) > cos(q, fora)
    assert cos(q, en) > cos(q, fora), "recuperacao cross-lingual falhou"


def test_limiar_configurado_separa_relevante_de_irrelevante(cliente_real, settings):
    """Guarda contra um limiar herdado de recomendacao generica.

    Medido neste projeto: as distancias deste modelo se concentram entre 0.119
    e 0.200. Um limiar de 0.35 — que soa razoavel em abstrato — deixaria passar
    ate uma receita de bolo. Este teste falha se alguem afrouxar o valor sem
    medir.
    """
    import numpy as np

    q = np.array(cliente_real.embed_query("monitoramento de pressao alta na gravidez"))
    relevante, irrelevante = (
        np.array(v)
        for v in cliente_real.embed_passage(
            [
                "A hipertensao gestacional exige monitoramento continuo da pressao arterial.",
                "Receita de bolo de cenoura com cobertura de chocolate.",
            ]
        )
    )

    d_relevante = 1 - float(np.dot(q, relevante))
    d_irrelevante = 1 - float(np.dot(q, irrelevante))
    limiar = settings.RAG_MAX_COSINE_DISTANCE

    assert d_relevante <= limiar, f"trecho relevante barrado (d={d_relevante:.4f} > {limiar})"
    assert d_irrelevante > limiar, (
        f"trecho irrelevante passou (d={d_irrelevante:.4f} <= {limiar}). "
        f"O limiar esta frouxo demais para este modelo."
    )


def test_contagem_de_tokens_usa_o_tokenizador_real(cliente_real):
    """Estimar por caracteres erraria: o modelo trunca em 512 tokens sem avisar,
    e a diferenca entre estimativa e realidade e justamente onde o texto sumiria."""
    curto = cliente_real.contar_tokens("uma frase curta")
    longo = cliente_real.contar_tokens("uma frase curta " * 50)
    assert 0 < curto < longo
    # O prefixo "passage: " tambem consome do orcamento de 512.
    assert curto >= 4
