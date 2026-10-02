"""Corte da busca pelos rotulos de outra IA (knowledge/calibracao.py)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.knowledge import calibracao
from tests.test_interface import ambiente, embedding_falso  # noqa: F401


def test_ler_aceita_variacoes_e_ignora_o_resto():
    resposta = "Claro!\nT1: R\nT2 - l\n**T3**: I\nT4 = R (talvez)\nT9: R\nT2x: R"
    assert calibracao.ler(resposta, 4) == {1: "R", 2: "L", 3: "I", 4: "R"}


def test_pedido_nao_mostra_a_distancia():
    linhas = [
        {"conteudo": "texto um", "titulo": "Fonte", "heading": "Intro", "distancia": 0.2058},
        {"conteudo": "texto dois", "titulo": "Outra", "heading": "", "distancia": 0.2071},
    ]
    texto = calibracao.pedido("fidelizacao", linhas)
    assert "T1 | fonte: Fonte | secao: Intro" in texto and "T2 | fonte: Outra" in texto
    assert "0.2058" not in texto and "0,2058" not in texto


def _trecho(documento, n, distancia):
    from apps.knowledge.embeddings import get_embedding_client
    from apps.knowledge.models import SuperChunk

    texto = f"conteudo {n} sobre clientes"
    trecho = SuperChunk.objects.create(
        document=documento,
        content=texto,
        paragraph_index=n,
        embedding=get_embedding_client().embed_passage([texto])[0],
        is_active=True,
    )
    trecho.distancia = distancia
    return trecho


@pytest.mark.django_db
def test_corte_separa_relevante_de_irrelevante_e_ignora_lixo(ambiente):  # noqa: F811
    from tests.test_interface import _documento_curado as _documento

    documento = _documento()
    # Lixo perto (0.200) nao puxa o corte para baixo; o corte fica depois do
    # ultimo relevante antes dos irrelevantes.
    dados = [
        (0.200, "L"),
        (0.205, "R"),
        (0.207, "R"),
        (0.209, "R"),
        (0.211, "I"),
        (0.212, "R"),
        (0.214, "I"),
        (0.216, "I"),
        (0.218, "L"),
    ]
    trechos = [_trecho(documento, n, d) for n, (d, _) in enumerate(dados)]
    rotulos = {n + 1: r for n, (_, r) in enumerate(dados)}
    assert calibracao.gravar("fidelizacao", trechos, rotulos) == len(dados)

    sugestao = calibracao.sugerir_corte()
    assert sugestao.corte == pytest.approx(0.2125, abs=1e-4)
    assert sugestao.relevantes == 4 and sugestao.irrelevantes == 3 and sugestao.lixo == 2
    assert calibracao.lixo_marcado().count() == 2

    _, _, client = ambiente
    client.post(reverse("knowledge:tirar_lixo_da_busca", urlconf="core.urls_tenants"))
    assert calibracao.lixo_marcado().count() == 0

    html = client.get(reverse("knowledge:busca", urlconf="core.urls_tenants")).content.decode()
    assert "Corte sugerido" in html and "0.2125" in html.replace(",", ".")


@pytest.mark.django_db
def test_tela_oferece_o_pedido_e_grava_a_resposta(ambiente, monkeypatch):  # noqa: F811
    from apps.knowledge.models import RotuloDeCalibracao
    from tests.test_interface import _documento_curado as _documento

    documento = _documento()
    for n in range(3):
        _trecho(documento, n, 0)
    _, _, client = ambiente
    url = reverse("knowledge:busca", urlconf="core.urls_tenants")
    html = client.get(url, {"consulta": "fidelizacao de clientes"}).content.decode()
    assert "Copiar para outra IA" in html and "T1 | fonte:" in html

    import re

    ids = re.search(r'name="ids" value="([^"]+)"', html).group(1)
    client.post(
        reverse("knowledge:rotular_busca", urlconf="core.urls_tenants"),
        {"consulta": "fidelizacao de clientes", "ids": ids, "resposta": "T1: R\nT2: I\nT3: L"},
    )
    assert sorted(RotuloDeCalibracao.objects.values_list("rotulo", flat=True)) == ["I", "L", "R"]
    html = client.get(url, {"consulta": "fidelizacao de clientes"}).content.decode()
    assert "relevante" in html and "lixo" in html
