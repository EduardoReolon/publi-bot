"""O radar como fornecedor de fontes, e o bloqueio de site.

* so pagina que parece artigo (JSON-LD, og:type ou texto corrido);
* so tema que o acervo nao cobre; cota por rodada e teto de pendentes;
* home, listagem, plataforma aberta, proprio site, concorrente e caminho
  bloqueado ficam de fora;
* recusar bloqueando o site (ou a area) tira da fila o que ja estava la e
  impede o que viria.
"""

from __future__ import annotations

import pytest

from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte
from apps.knowledge.web import Classificacao, classificar_pagina
from apps.radar.models import ConfiguracaoDoRadar, ResultadoOrganico, RodadaDoRadar
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401

ARTIGO_JSONLD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},{"@type":["BlogPosting"]}]}
</script></head><body><p>curto</p></body></html>"""
PRODUTO = """<html><head><meta property="og:type" content="product">
<script type="application/ld+json">{"@type":"Product"}</script></head><body></body></html>"""
OG_ARTIGO = '<html><head><meta property="og:type" content="article"></head><body></body></html>'


def test_classificacao_pelo_que_o_site_declara():
    assert classificar_pagina(ARTIGO_JSONLD).e_artigo
    assert classificar_pagina(OG_ARTIGO).e_artigo
    produto = classificar_pagina(PRODUTO)
    assert not produto.e_artigo and "product" in produto.motivo


def test_sem_declaracao_decide_o_tamanho_do_texto():
    # Paragrafos diferentes: a trafilatura descarta texto repetido.
    def paragrafos(n):
        return "".join(
            "<p>" + " ".join(f"termo{p}x{i}" for i in range(60)) + ".</p>" for p in range(n)
        )

    longo = f"<html><body><article>{paragrafos(10)}</article></body></html>"
    curto = f"<html><body><article>{paragrafos(3)}</article></body></html>"
    classificacao = classificar_pagina(longo)
    assert classificacao.e_artigo
    assert "termo9x59" in classificacao.texto  # o texto vai junto, para conferir
    assert not classificar_pagina(curto).e_artigo


def _rodada_com(urls):
    rodada = RodadaDoRadar.objects.create(origem="manual")
    for posicao, url in enumerate(urls, start=1):
        ResultadoOrganico.objects.create(
            rodada=rodada,
            consulta="como calcular bdi",
            url=url,
            titulo=f"T{posicao}",
            posicao=posicao,
        )
    return rodada


@pytest.mark.django_db
def test_sugere_so_artigo_e_so_o_que_pode(radar, monkeypatch):  # noqa: F811
    from apps.integrations.models import Site
    from apps.radar import fontes

    Site.objects.create(name="S", slug="s", base_url="https://meusite.com.br")
    config = ConfiguracaoDoRadar.carregar()
    config.concorrentes = "rival.com.br"
    config.save()
    CaminhoConfiavel.objects.create(prefixo="ruim.com.br", nivel="bloquear")
    classificadas = []

    def classificar(url):
        classificadas.append(url)
        return Classificacao("loja" not in url, "teste", "o corpo do artigo")

    monkeypatch.setattr(fontes, "_classificar", classificar)
    rodada = _rodada_com(
        [
            "https://www.tcu.gov.br/",  # pagina inicial
            "https://revista.com.br/bdi-em-obra-publica",
            "https://meusite.com.br/bdi",  # o proprio site
            "https://rival.com.br/bdi",  # concorrente
            "https://ruim.com.br/bdi",  # bloqueado
            "https://www.youtube.com/watch?v=1",  # plataforma aberta
            "https://blog.com.br/tag/bdi/",  # listagem
            "https://loja.com.br/planilha-bdi",  # nao e artigo
            "https://outra.com.br/estudo-bdi.pdf",
        ]
    )

    assert fontes.sugerir_fontes(rodada, cota=5) == 2
    urls = set(CandidatoDeFonte.objects.values_list("url", flat=True))
    assert urls == {
        "https://revista.com.br/bdi-em-obra-publica",
        "https://outra.com.br/estudo-bdi.pdf",
    }
    candidato = CandidatoDeFonte.objects.get(url__endswith="obra-publica")
    assert candidato.origem == "radar" and candidato.consulta == "como calcular bdi"
    assert candidato.texto_extraido == "o corpo do artigo"
    assert "https://loja.com.br/planilha-bdi" in classificadas


@pytest.mark.django_db
def test_cota_e_teto_de_pendentes(radar, monkeypatch):  # noqa: F811
    from apps.radar import fontes

    monkeypatch.setattr(fontes, "_classificar", lambda url: Classificacao(True, "teste"))
    rodada = _rodada_com([f"https://site{i}.com.br/artigo" for i in range(10)])

    assert fontes.sugerir_fontes(rodada, cota=3) == 3

    for i in range(fontes.MAXIMO_DE_PENDENTES):
        CandidatoDeFonte.objects.create(url=f"https://x{i}.com/a")
    outra = _rodada_com([f"https://novo{i}.com.br/artigo" for i in range(5)])
    assert fontes.sugerir_fontes(outra, cota=3) == 0


@pytest.mark.django_db
def test_tema_que_o_acervo_ja_cobre_nao_gera_sugestao(radar, monkeypatch):  # noqa: F811
    from apps.radar import fontes

    monkeypatch.setattr(fontes, "_classificar", lambda url: Classificacao(True, "teste"))
    monkeypatch.setattr(fontes, "_coberta", lambda consulta: True)
    rodada = _rodada_com(["https://revista.com.br/bdi"])

    assert fontes.sugerir_fontes(rodada, cota=3) == 0


@pytest.mark.django_db
def test_recusar_bloqueando_o_site_limpa_a_fila_e_impede_o_resto(radar):  # noqa: F811
    from apps.knowledge.fontes_web import bloqueada, recusar

    primeiro = CandidatoDeFonte.objects.create(url="https://ruim.com.br/forum/topico-1")
    outro = CandidatoDeFonte.objects.create(url="https://ruim.com.br/blog/post")
    alheio = CandidatoDeFonte.objects.create(url="https://bom.com.br/post")

    caminho = recusar(primeiro, bloquear="caminho")
    assert caminho.prefixo == "ruim.com.br/forum"
    outro.refresh_from_db()
    assert outro.situacao == "pendente"  # outra area do mesmo site segue
    assert bloqueada("https://ruim.com.br/forum/topico-2")

    recusar(outro, bloquear="site")
    assert bloqueada("https://ruim.com.br/qualquer")
    alheio.refresh_from_db()
    assert alheio.situacao == "pendente"
    assert CaminhoConfiavel.objects.get(prefixo="ruim.com.br").nivel == "bloquear"


@pytest.mark.django_db
def test_rodada_guarda_os_resultados_e_sugere(radar, monkeypatch):  # noqa: F811
    from apps.radar import coleta, fontes
    from apps.radar.provedores import ItemDeBusca, ResultadoDeBusca

    monkeypatch.setattr(fontes, "_classificar", lambda url: Classificacao(True, "texto corrido"))
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.sementes = "bdi"
    config.usar_perguntas_do_site = False
    config.save()

    def buscar(consulta, *, finalidade):
        return ResultadoDeBusca(
            provedor="searxng",
            resultados=[ItemDeBusca(url="https://revista.com.br/bdi", titulo="BDI")],
        )

    monkeypatch.setattr(coleta, "buscar", buscar)

    rodada = coleta.executar_rodada()

    assert ResultadoOrganico.objects.filter(rodada=rodada).count() == 1
    assert rodada.resumo["fontes_sugeridas"] == 1
    assert CandidatoDeFonte.objects.get().classificacao == "texto corrido"


@pytest.mark.django_db
def test_tela_mostra_o_texto_capturado_e_captura_quando_falta(ambiente, monkeypatch):  # noqa: F811
    from django.urls import reverse

    from apps.knowledge import web

    _, _, client = ambiente
    CandidatoDeFonte.objects.create(url="https://a.com.br/post", texto_extraido="Menu Home corpo")
    sem_texto = CandidatoDeFonte.objects.create(url="https://b.com.br/post")
    url = reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants")
    html = client.get(url).content.decode()
    assert "Menu Home corpo" in html
    assert "Ver o texto que seria capturado" in html

    monkeypatch.setattr(web, "texto_da_pagina", lambda u: "texto de " + u)
    client.post(
        reverse(
            "knowledge:capturar_texto_do_candidato",
            args=[sem_texto.pk],
            urlconf="core.urls_tenants",
        )
    )
    sem_texto.refresh_from_db()
    assert sem_texto.texto_extraido == "texto de https://b.com.br/post"
