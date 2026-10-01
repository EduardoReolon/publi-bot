"""Artigos cientificos como fonte: OpenAlex, Google Academico e o PDF.

* o OpenAlex vira candidato com DOI, resumo, citacoes e PDF aberto;
* o bloco "Google Academico" da busca ja paga e completado pelo OpenAlex;
* aprovar baixa o PDF e da autoridade pelas citacoes (fonte forte);
* sem PDF livre, ou com o site recusando, o artigo espera a pessoa enviar.
"""

from __future__ import annotations

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.knowledge import academicos
from apps.knowledge.models import CandidatoDeFonte, Document
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401

TRABALHO = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1000/rfm.2020",
    "display_name": "Analise RFM para retencao de clientes",
    "publication_year": 2020,
    "cited_by_count": 120,
    "language": "pt",
    "type": "article",
    "abstract_inverted_index": {"clientes": [2], "segmenta": [1], "RFM": [0]},
    "open_access": {"is_oa": True, "oa_url": "https://revista.org/rfm.pdf"},
    "best_oa_location": {
        "pdf_url": "https://revista.org/rfm.pdf",
        "landing_page_url": "https://revista.org/rfm",
    },
    "primary_location": {
        "source": {"display_name": "Revista de Marketing"},
        "landing_page_url": "https://doi.org/10.1000/rfm.2020",
    },
    "authorships": [{"author": {"display_name": "Ana Souza"}}],
}
SEM_PDF = {
    **TRABALHO,
    "doi": "https://doi.org/10.1000/fechado",
    "display_name": "Churn em varejo",
    "open_access": {"is_oa": False, "oa_url": None},
    "best_oa_location": None,
}


@pytest.fixture
def bases_academicas(monkeypatch):
    """OpenAlex e Unpaywall simulados; guarda os parametros de cada chamada."""
    chamadas = []
    respostas = {"openalex": [TRABALHO, SEM_PDF], "unpaywall": 404}

    def get(url, params=None, **kwargs):
        chamadas.append((url, params or {}))
        pedido = httpx.Request("GET", url)
        if url.startswith(academicos.OPENALEX):
            return httpx.Response(200, json={"results": respostas["openalex"]}, request=pedido)
        return httpx.Response(respostas["unpaywall"], json={}, request=pedido)

    monkeypatch.setattr(academicos.httpx, "get", get)
    return chamadas, respostas


def test_trabalho_do_openalex_vira_dados_legiveis():
    trabalho = academicos.ler_trabalho(TRABALHO)
    assert trabalho.resumo == "RFM segmenta clientes"
    assert trabalho.url == "https://doi.org/10.1000/rfm.2020"
    assert trabalho.pdf_url == "https://revista.org/rfm.pdf"
    assert trabalho.revista == "Revista de Marketing" and trabalho.autores == ["Ana Souza"]
    assert academicos.autoridade(0) == 70
    assert 85 <= academicos.autoridade(120) <= 90
    assert academicos.autoridade(10**9) == 95


@pytest.mark.django_db
def test_pauta_ganha_artigos_sem_repetir(radar, bases_academicas):  # noqa: F811
    from apps.content.models import Topic
    from apps.radar.models import ContasExternas

    chamadas, _ = bases_academicas
    contas = ContasExternas.carregar()
    contas.email_para_bases_academicas = "eu@exemplo.com"
    contas.save()
    pauta = Topic.objects.create(title="Analise RFM", target_keyword="analise rfm")

    novos = academicos.buscar_para_pauta(pauta, limite=5)
    assert [c.tipo for c in novos] == ["artigo", "artigo"]
    rfm = CandidatoDeFonte.objects.get(doi="10.1000/rfm.2020")
    assert rfm.url == "https://doi.org/10.1000/rfm.2020" and rfm.citacoes == 120
    assert rfm.pdf_url and rfm.trecho == "RFM segmenta clientes" and rfm.pauta == pauta
    assert chamadas[0][1]["mailto"] == "eu@exemplo.com"
    # Por sentido: a busca semantica, com a pauta inteira.
    assert "search.semantic" in chamadas[0][1] and "is_retracted:false" in chamadas[0][1]["filter"]

    assert academicos.buscar_para_pauta(pauta, limite=5) == []  # ja conhecidos


@pytest.mark.django_db
def test_google_academico_da_busca_paga_e_completado_pelo_openalex(radar, bases_academicas):  # noqa: F811
    from apps.radar.provedores import ler_serp

    _, respostas = bases_academicas
    tarefa = {
        "result": [
            {
                "items": [
                    {
                        "type": "scholarly_articles",
                        "items": [
                            {
                                "title": "Analise RFM para retencao de clientes",
                                "url": "http://scholar.google.com/scholar_url?url=https://x.org/a",
                                "author": "‎Souza",
                            },
                            {
                                "title": "Um livro sem par no OpenAlex",
                                "url": "http://scholar.google.com/scholar_url?url=https://livros.org/b.pdf&hl=pt",
                                "author": "‎Lima",
                            },
                        ],
                    }
                ]
            }
        ]
    }
    itens = ler_serp(tarefa).academicos
    assert itens[0]["autor"] == "Souza"

    respostas["openalex"] = [TRABALHO]
    assert academicos.registrar_do_google(itens, consulta="rfm") == 2
    completado = CandidatoDeFonte.objects.get(origem="scholar", doi="10.1000/rfm.2020")
    assert completado.revista == "Revista de Marketing"
    sem_par = CandidatoDeFonte.objects.get(url="https://livros.org/b.pdf")
    assert sem_par.pdf_url == "https://livros.org/b.pdf" and sem_par.autores == "Lima"


@pytest.mark.django_db
def test_aprovar_baixa_o_pdf_e_da_autoridade(radar, bases_academicas, monkeypatch):  # noqa: F811
    from apps.knowledge.perfis import categoria_da_natureza

    academicos.buscar_para_pauta(_pauta(), limite=5)
    categoria = categoria_da_natureza("cientifico")
    monkeypatch.setattr(
        "apps.knowledge.entradas.baixar",
        lambda url: (b"%PDF-1.7 conteudo", url, "application/pdf"),
    )
    rfm = CandidatoDeFonte.objects.get(doi="10.1000/rfm.2020")
    academicos.aprovar_artigo(rfm, categoria=categoria)
    rfm.refresh_from_db()
    assert rfm.situacao == "aprovado"
    documento = rfm.documento
    assert documento.source_url == "https://doi.org/10.1000/rfm.2020"
    assert documento.authority_score >= 85 and documento.authors == "Ana Souza"

    # Sem PDF aberto (e o Unpaywall sem nada): espera a pessoa.
    fechado = CandidatoDeFonte.objects.get(doi="10.1000/fechado")
    academicos.aprovar_artigo(fechado, categoria=categoria)
    fechado.refresh_from_db()
    assert fechado.situacao == "pdf" and "https://doi.org/10.1000/fechado" in fechado.motivo


@pytest.mark.django_db
def test_pdf_que_vira_pagina_espera_a_pessoa(radar, bases_academicas, monkeypatch):  # noqa: F811
    from apps.knowledge.perfis import categoria_da_natureza

    academicos.buscar_para_pauta(_pauta(), limite=5)
    monkeypatch.setattr(
        "apps.knowledge.entradas.baixar",
        lambda url: (b"<html>aceite os cookies</html>", url, "text/html"),
    )
    rfm = CandidatoDeFonte.objects.get(doi="10.1000/rfm.2020")
    academicos.aprovar_artigo(rfm, categoria=categoria_da_natureza("cientifico"))
    rfm.refresh_from_db()
    assert rfm.situacao == "pdf" and not Document.objects.exists()


@pytest.mark.django_db
def test_enviar_o_pdf_pela_tela(ambiente, bases_academicas):  # noqa: F811
    _, _, client = ambiente
    academicos.buscar_para_pauta(_pauta(), limite=5)
    fechado = CandidatoDeFonte.objects.get(doi="10.1000/fechado")
    fechado.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    fechado.save()

    tela = client.get(reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants"))
    html = tela.content.decode()
    assert "Artigos aguardando o PDF" in html and "artigo cientifico" in html

    client.post(
        reverse("knowledge:enviar_pdf", args=[fechado.pk], urlconf="core.urls_tenants"),
        {"pdf": SimpleUploadedFile("churn.pdf", b"%PDF-1.7 churn", "application/pdf")},
    )
    fechado.refresh_from_db()
    assert fechado.situacao == "aprovado"
    assert fechado.documento.title == "Churn em varejo"
    assert fechado.documento.category.source_class == "cientifico"


@pytest.mark.django_db
def test_rodada_busca_artigos_pelas_sementes_cientificas(radar, bases_academicas):  # noqa: F811
    """Nao pelas buscas do Google: artigo academico usa outro vocabulario."""
    from apps.radar.models import ConfiguracaoDoRadar, ResultadoOrganico, RodadaDoRadar

    rodada = RodadaDoRadar.objects.create(origem="manual", resumo={})
    ResultadoOrganico.objects.create(
        rodada=rodada, consulta="cliente sumiu", url="https://x.com/a", posicao=1
    )
    assert academicos.sugerir_pelo_radar(rodada, cota=5) == 0  # sem sementes cientificas

    config = ConfiguracaoDoRadar.carregar()
    config.sementes_cientificas = "rfm analysis\ncustomer churn"
    config.save()

    assert academicos.sugerir_pelo_radar(rodada, cota=1) == 1
    assert CandidatoDeFonte.objects.get().consulta == "rfm analysis"
    assert rodada.resumo["sementes_cientificas"] == ["rfm analysis"]


@pytest.mark.django_db
def test_pedido_e_resposta_das_sementes_cientificas(radar):  # noqa: F811
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "cliente sumiu"
    config.save()
    pedido = academicos.pedido_das_sementes_cientificas()
    assert "CIENTIFICAS:" in pedido and "- cliente sumiu" in pedido

    resposta = (
        "Claro!\n**CIENTIFICAS:**\n- customer retention\n- churn prediction\n"
        "- customer retention\nFIM"
    )
    assert academicos.ler_sementes_cientificas(resposta) == [
        "customer retention",
        "churn prediction",
    ]


def _pauta():
    from apps.content.models import Topic

    return Topic.objects.create(title="Analise RFM", target_keyword="analise rfm")


@pytest.mark.django_db
def test_tela_salva_as_sementes_e_busca_agora(ambiente, bases_academicas):  # noqa: F811
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    client.post(
        reverse("radar:sementes_cientificas", urlconf="core.urls_tenants"),
        {"resposta": "CIENTIFICAS:\n- rfm analysis\n- customer churn\nFIM"},
    )
    assert ConfiguracaoDoRadar.carregar().lista_de_sementes_cientificas == [
        "rfm analysis",
        "customer churn",
    ]

    client.post(reverse("radar:buscar_artigos_agora", urlconf="core.urls_tenants"))
    pagina = client.get(reverse("radar:configuracao", urlconf="core.urls_tenants"))

    html = pagina.content.decode()
    assert "Novos na fila" in html and "rfm analysis" in html
    assert CandidatoDeFonte.objects.filter(consulta="rfm analysis").exists()


def test_metricas_do_openalex_e_retratado_nunca_vira_fonte():
    dados = {
        "display_name": "Churn em varejo",
        "doi": "https://doi.org/10.1/churn",
        "fwci": 2.4,
        "citation_normalized_percentile": {"value": 0.93, "is_in_top_10_percent": True},
        "is_retracted": False,
        "type": "article",
        "primary_topic": {
            "display_name": "Customer retention",
            "field": {"display_name": "Business"},
        },
    }
    trabalho = academicos.ler_trabalho(dados)
    assert trabalho.metricas["fwci"] == 2.4 and trabalho.metricas["top_10_por_cento"] is True
    assert trabalho.metricas["area"] == "Business"

    retratado = academicos.ler_trabalho({**dados, "is_retracted": True})
    assert retratado.metricas["retratado"] is True


@pytest.mark.django_db
def test_retratado_nao_e_registrado(radar):  # noqa: F811
    trabalho = academicos.Trabalho(titulo="X", doi="10.1/x", metricas={"retratado": True})
    assert academicos.registrar(trabalho, consulta="x", origem="openalex") is None


def test_filtros_de_numero():
    from apps.radar.templatetags.radar import duracao, numero_curto, porcentagem_de

    assert numero_curto(12345) == "12 mil" and numero_curto(2_300_000) == "2,3 mi"
    assert numero_curto(None) == "" and numero_curto(950) == "950"
    assert porcentagem_de(540, 12345) == "4,4%"
    assert duracao(725) == "12min05" and duracao(3723) == "1h02"


@pytest.mark.django_db
def test_paginas_e_videos_pendentes_nao_travam_a_busca_de_artigos(ambiente, bases_academicas):  # noqa: F811
    """A trava de 30 pendentes contava tudo; com a fila cheia de paginas, o
    botao nao fazia nada e nao dizia nada."""
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    for i in range(academicos.MAXIMO_DE_PENDENTES):
        CandidatoDeFonte.objects.create(url=f"https://pagina.com/{i}", tipo="pagina")
    config = ConfiguracaoDoRadar.carregar()
    config.sementes_cientificas = "rfm analysis"
    config.save()

    resposta = client.post(
        reverse("radar:buscar_artigos_agora", urlconf="core.urls_tenants"), follow=True
    )

    assert "artigo(s) novo(s) na fila" in resposta.content.decode()
    assert CandidatoDeFonte.objects.filter(tipo="artigo").exists()
