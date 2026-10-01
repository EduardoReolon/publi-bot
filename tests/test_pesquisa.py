"""Pesquisa da pauta: hipoteses, busca semantica, angulos, sinteses e os dois fluxos."""

from __future__ import annotations

import json

import httpx
import pytest
from django.urls import reverse

from apps.content.models import Topic
from apps.knowledge import pesquisa
from apps.knowledge.models import CandidatoDeFonte, Document
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar e ordenar carregariam o modelo real."""


def _resumo(texto: str) -> dict:
    """O indice invertido do OpenAlex: cada palavra com TODAS as posicoes."""
    indice: dict = {}
    for posicao, palavra in enumerate(texto.split()):
        indice.setdefault(palavra, []).append(posicao)
    return indice


def _trabalho(n: int, *, titulo: str, relacionados=()) -> dict:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/10.1/{n}",
        "display_name": titulo,
        "publication_year": 2015 + n % 8,
        "cited_by_count": 10 * n,
        "language": "en",
        "type": "article",
        "abstract_inverted_index": _resumo(
            f"{titulo} study with customers of small services and their loyalty " * 4
        ),
        "citation_normalized_percentile": {"value": 0.5 + n / 100},
        "primary_topic": {
            "display_name": "Customer Service Quality and Loyalty",
            "field": {"display_name": "Business"},
        },
        "related_works": list(relacionados),
        "relevance_score": 1.0,
    }


@pytest.fixture
def openalex(monkeypatch):
    """Cada busca semantica devolve trabalhos diferentes; a de ids, os relacionados."""
    chamadas = []

    def get(url, params, timeout):
        chamadas.append(params)
        if "search.semantic" in params:
            base = 100 * len([c for c in chamadas if "search.semantic" in c])
            itens = [
                _trabalho(
                    base + i,
                    titulo=f"Service recovery angle {base} paper {i}",
                    relacionados=[f"https://openalex.org/W{900 + i}"],
                )
                for i in range(4)
            ]
        else:
            itens = [_trabalho(900, titulo="Related justice in service recovery")]
        return httpx.Response(200, json={"results": itens}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(pesquisa.time, "sleep", lambda s: None)
    return chamadas


@pytest.fixture
def modelo(monkeypatch):
    """O modelo responde as hipoteses (refutavel) e as sinteses (com um pedido)."""
    from apps.content import inference

    pedidos = []

    def executar(*, key, variaveis, **kw):
        pedidos.append(key)
        if key == "research_hypotheses":
            texto = {
                "angulos": [
                    {"angulo": "mecanismo", "paragrafo": "Why service failures hurt loyalty."},
                    {"angulo": "solucao", "paragrafo": "Recovery strategies restore trust."},
                    {"angulo": "outro", "paragrafo": "Employee empowerment and apology."},
                ],
                "refutavel": True,
                "contrarias": [
                    {"angulo": "contra 1", "paragrafo": "Recovery does not restore loyalty."},
                    {"angulo": "contra 2", "paragrafo": "Paradox is a myth."},
                ],
            }
        else:
            texto = {
                "sintese": "Os estudos [1] mostram algo.",
                "pedidos": [{"artigo": 1, "o_que": "a amostra"}],
            }
        return type("R", (), {"texto": json.dumps(texto)})()

    monkeypatch.setattr(inference, "executar_prompt", executar)
    return pedidos


def test_pesquisa_completa(ambiente, openalex, modelo):  # noqa: F811
    pauta = Topic.objects.create(title="Recuperar o cliente depois de uma falha", briefing="Base.")
    resultado = pesquisa.pesquisar(pauta)

    semanticas = [c for c in openalex if "search.semantic" in c]
    assert len(semanticas) == 5  # 3 angulos + 2 contrarias
    assert any("openalex:" in c.get("filter", "") for c in openalex)  # bola de neve
    assert resultado["relacionados"] == 1
    assert 0 < len(resultado["artigos"]) <= pesquisa.ESCOLHIDOS
    assert any(a["contraponto"] for a in resultado["angulos"])
    assert all(a.get("sintese") for a in resultado["angulos"])
    # O pedido so vale no angulo que tem o artigo 1.
    pedidos = [p for a in resultado["angulos"] for p in a.get("pedidos", [])]
    assert [p["o_que"] for p in pedidos] == ["a amostra"]

    # As fontes: candidatos aprovados com o resumo, curado automaticamente.
    candidatos = CandidatoDeFonte.objects.filter(pk__in=resultado["candidatos"])
    assert candidatos.count() == len(resultado["artigos"])
    assert all(c.situacao == CandidatoDeFonte.Situacao.APROVADO for c in candidatos)
    documentos = Document.objects.filter(pk__in=pesquisa.documentos_da_pesquisa(pauta))
    assert documentos.count() == len(resultado["artigos"])
    assert all(d.status == Document.Status.CURATED for d in documentos)

    pauta.refresh_from_db()
    assert pauta.briefing.startswith("Base.") and pesquisa.MARCA_NA_ORIENTACAO in pauta.briefing
    assert pauta.busca_de_fontes["pesquisa"]["situacao"] == "pronta"

    # O fluxo da pesquisa usa so esses documentos.
    from apps.knowledge.referencias import trechos_da_pauta

    pauta.fluxo = Topic.Fluxo.PESQUISA
    pauta.save()
    usados = {t.chunk.document_id for t in trechos_da_pauta(pauta)}
    assert usados and usados <= set(documentos.values_list("pk", flat=True))
    assert pesquisa.pronta_para_gerar(pauta) == ""


def test_sem_modelo_uma_busca_so(ambiente, openalex):  # noqa: F811
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    assert len([c for c in openalex if "search.semantic" in c]) == 1
    assert resultado["hipoteses"]["do_modelo"] is False
    assert not any(a["contraponto"] for a in resultado["angulos"])


def test_tela_mostra_os_dois_fluxos_e_a_config_esconde(ambiente, openalex, modelo):  # noqa: F811
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    url = reverse("content:pautas", urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "Pelo acervo" in html and "Pela pesquisa de artigos" in html
    assert "Gerar com a pesquisa" not in html

    # Gerar pela pesquisa antes de pesquisar: avisa e nao gera.
    gerar = reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants")
    resposta = client.post(gerar, {"fluxo": "pesquisa"}, follow=True)
    assert "Ainda nao" in resposta.content.decode()

    pesquisa.pesquisar(pauta)
    html = client.get(url).content.decode()
    assert "Gerar com a pesquisa" in html and "Contraponto" in html and "a amostra" in html

    config = ConfiguracaoDoRadar.carregar()
    config.fluxo_do_acervo = False
    config.save()
    html = client.get(url).content.decode()
    assert "Pelo acervo" not in html and "Pela pesquisa de artigos" in html


def test_pedir_o_pdf_sem_pdf_aberto(ambiente, openalex, modelo):  # noqa: F811
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Recuperar o cliente")
    resultado = pesquisa.pesquisar(pauta)
    candidato = resultado["candidatos"][0]
    client.post(
        reverse("content:pdf_da_pesquisa", args=[pauta.pk, candidato], urlconf="core.urls_tenants")
    )
    assert (
        CandidatoDeFonte.objects.get(pk=candidato).situacao
        == CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    )


def test_outra_ia_no_fluxo_da_pesquisa_pede_e_le_os_pedidos(ambiente, openalex, modelo):  # noqa: F811
    from apps.content import outra_ia

    pauta = Topic.objects.create(title="Recuperar o cliente")
    pesquisa.pesquisar(pauta)
    pauta.fluxo = Topic.Fluxo.PESQUISA
    pauta.save()
    artigo = outra_ia.preparar(pauta)
    texto = outra_ia.pedido(artigo)
    assert "RESUMOS de artigos cientificos" in texto and "PEDIDOS:" in texto

    lido = outra_ia.ler(
        "TITULO SUGERIDO: x\nPEDIDOS:\n- fonte 2: a amostra do estudo\n"
        "CORPO DO ARTIGO:\nTexto.\nFIM DO ARTIGO"
    )
    assert lido["pedidos"] == ["fonte 2: a amostra do estudo"]
