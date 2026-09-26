"""Fila padrao da DataForSEO e analise de concorrentes.

O que estes testes guardam:

* com a fila, a rodada POSTA as tarefas e fica aguardando; o batimento colhe,
  e a rodada so passa para o volume quando a SERP inteira voltou, e so
  termina quando o volume voltou;
* tarefa que expira ou falha nao prende a rodada;
* o custo e registrado no POST (e ali que a DataForSEO cobra), e o teto e
  conferido antes;
* a busca manual continua ao vivo;
* o sitemap do concorrente vira sinal, lido com defusedxml e sem baixar as
  paginas; o que nao e conteudo (tag, contato, id) fica de fora;
* as buscas do concorrente ja chegam com volume;
* avaliacao so vira sinal se reclama ou pergunta, e sozinha nao vira pauta.
"""

from __future__ import annotations

import gzip
from decimal import Decimal

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.radar.models import (
    ChamadaExterna,
    ConfiguracaoDoRadar,
    GrupoDeDemanda,
    RodadaDoRadar,
    SinalDeDemanda,
    TarefaNaFila,
)
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import RESPOSTA_SERP, _com_dataforseo, radar  # noqa: F401


class DataForSEOFalsa:
    """Responde task_post criando tarefas, e task_get com o que o teste mandar."""

    def __init__(self, monkeypatch):
        self.posts: list[tuple[str, list]] = []
        self.gets: list[str] = []
        self.resultados: dict[str, dict] = {}  # task_id -> tarefa da resposta
        self.contador = 0
        monkeypatch.setattr(httpx, "post", self.post)
        monkeypatch.setattr(httpx, "get", self.get)

    def post(self, url, json=None, auth=None, timeout=None):
        self.posts.append((url, json))
        tarefas = []
        for corpo in json:
            self.contador += 1
            tarefas.append(
                {
                    "id": f"tarefa-{self.contador}",
                    "status_code": 20100,
                    "status_message": "Task Created.",
                    "data": corpo,
                }
            )
        preco = 0.06 if "search_volume" in url else 0.0006
        dados = {"status_code": 20000, "cost": preco * len(tarefas), "tasks": tarefas}
        return httpx.Response(200, json=dados, request=httpx.Request("POST", url))

    def get(self, url, auth=None, timeout=None):
        self.gets.append(url)
        task_id = url.rsplit("/", 1)[-1]
        tarefa = self.resultados.get(
            task_id, {"id": task_id, "status_code": 40602, "status_message": "Task In Queue."}
        )
        dados = {"status_code": 20000, "tasks": [tarefa]}
        return httpx.Response(200, json=dados, request=httpx.Request("GET", url))

    def responder(self, task_id, **tarefa):
        self.resultados[task_id] = {"id": task_id, "status_code": 20000, **tarefa}


def _config_com_fila(**campos):
    _com_dataforseo()
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.buscador = ConfiguracaoDoRadar.Buscador.DATAFORSEO
    config.modo_dataforseo = ConfiguracaoDoRadar.ModoDataForSEO.FILA
    config.usar_perguntas_do_site = False
    for campo, valor in campos.items():
        setattr(config, campo, valor)
    config.save()
    return config


# ---------------------------------------------------------------------------
# Fila padrao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rodada_pela_fila_do_post_ate_a_pauta(radar, monkeypatch):  # noqa: F811
    from apps.content.models import Topic
    from apps.radar.coleta import colher_fila, executar_rodada

    _config_com_fila(sementes="calcular bdi obra")
    api = DataForSEOFalsa(monkeypatch)

    rodada = executar_rodada()

    # Postou a SERP e ficou esperando; nada de chamada ao vivo.
    assert rodada.situacao == RodadaDoRadar.Situacao.AGUARDANDO
    assert [u for u, _ in api.posts] == [
        "https://api.dataforseo.com/v3/serp/google/organic/task_post"
    ]
    assert api.posts[0][1][0]["keyword"] == "calcular bdi obra"
    chamada = ChamadaExterna.objects.get()
    assert chamada.custo_usd == Decimal("0.0006")
    tarefa = TarefaNaFila.objects.get()
    assert tarefa.contexto == {"semente": "calcular bdi obra"}

    # Ainda na fila: o batimento nao faz nada.
    assert colher_fila() == 0
    rodada.refresh_from_db()
    assert rodada.situacao == RodadaDoRadar.Situacao.AGUARDANDO
    assert "/task_get/advanced/tarefa-1" in api.gets[0]

    # A SERP voltou: vira sinal, e a rodada posta o volume.
    api.responder("tarefa-1", result=RESPOSTA_SERP["tasks"][0]["result"])
    assert colher_fila() == 1
    rodada.refresh_from_db()
    assert rodada.fase == "volume"
    assert rodada.situacao == RodadaDoRadar.Situacao.AGUARDANDO
    assert SinalDeDemanda.objects.filter(texto="Como calcular o BDI?").exists()
    url_volume, corpo_volume = api.posts[-1]
    assert url_volume.endswith("/keywords_data/google_ads/search_volume/task_post")
    assert "como calcular o bdi" in corpo_volume[0]["keywords"]  # sem o "?"

    # O volume voltou: a rodada termina com pauta.
    api.responder(
        "tarefa-2",
        result=[
            {"keyword": "como calcular o bdi", "search_volume": 1900},
            {"keyword": "bdi tcu", "search_volume": 480},
        ],
    )
    colher_fila()
    rodada.refresh_from_db()
    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA
    assert rodada.custo_usd == Decimal("0.0606")
    assert SinalDeDemanda.objects.get(texto="Como calcular o BDI?").volume == 1900
    assert Topic.objects.filter(origin=Topic.Origin.RADAR).exists()


@pytest.mark.django_db
def test_tarefa_vencida_nao_prende_a_rodada(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import colher_fila, executar_rodada

    _config_com_fila(sementes="bdi", usar_volume=False)
    DataForSEOFalsa(monkeypatch)
    rodada = executar_rodada()
    TarefaNaFila.objects.update(criada_em=timezone.now() - timezone.timedelta(hours=25))

    colher_fila()

    rodada.refresh_from_db()
    assert TarefaNaFila.objects.get().situacao == TarefaNaFila.Situacao.EXPIRADA
    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA


@pytest.mark.django_db
def test_tarefa_que_falhou_vai_para_o_resumo_e_a_rodada_segue(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import colher_fila, executar_rodada

    _config_com_fila(sementes="bdi", usar_volume=False)
    api = DataForSEOFalsa(monkeypatch)
    rodada = executar_rodada()
    api.resultados["tarefa-1"] = {
        "id": "tarefa-1",
        "status_code": 40501,
        "status_message": "Invalid Field.",
    }

    colher_fila()

    rodada.refresh_from_db()
    assert TarefaNaFila.objects.get().situacao == TarefaNaFila.Situacao.FALHOU
    assert any("Invalid Field" in e for e in rodada.resumo["erros"])
    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA


@pytest.mark.django_db
def test_teto_e_conferido_antes_de_postar(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import executar_rodada

    _config_com_fila(sementes="a\nb\nc", teto_mensal_usd=Decimal("0.001"))
    api = DataForSEOFalsa(monkeypatch)

    rodada = executar_rodada()

    assert rodada.situacao == RodadaDoRadar.Situacao.PARADA_NO_TETO
    assert api.posts == []  # 3 x 0,0006 passaria de 0,001


@pytest.mark.django_db
def test_rodada_aguardando_nao_deixa_comecar_outra(ambiente, monkeypatch):  # noqa: F811
    from apps.radar.coleta import rodada_devida

    _, _, client = ambiente
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    RodadaDoRadar.objects.create(origem="agendada", situacao=RodadaDoRadar.Situacao.AGUARDANDO)
    enfileiradas = []
    monkeypatch.setattr("apps.radar.tasks.rodar_radar.delay", lambda: enfileiradas.append(1))

    assert not rodada_devida(config)
    resposta = client.post(reverse("radar:rodar_agora", urlconf="core.urls_tenants"), follow=True)
    assert "aguardando" in resposta.content.decode()
    assert enfileiradas == []


@pytest.mark.django_db
def test_busca_manual_continua_ao_vivo(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import busca_manual

    _config_com_fila()
    urls = []

    def post(url, json=None, auth=None, timeout=None):
        urls.append(url)
        return httpx.Response(200, json=RESPOSTA_SERP, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)

    busca = busca_manual("bdi obra")

    assert urls == ["https://api.dataforseo.com/v3/serp/google/organic/live/advanced"]
    assert busca.sinais.count() > 1


def test_palavra_para_volume_respeita_os_limites_do_google_ads():
    from apps.radar.provedores import palavra_para_volume

    assert palavra_para_volume("Como calcular o BDI?") == "como calcular o bdi"
    assert palavra_para_volume("preço (m²) / obra!") == "preço m² / obra"
    assert palavra_para_volume("uma frase " * 6) is None  # mais de 10 palavras
    assert palavra_para_volume("?!") is None


# ---------------------------------------------------------------------------
# Concorrentes: sitemap
# ---------------------------------------------------------------------------
ROBOTS = b"User-agent: *\nDisallow: /admin\nSitemap: https://concorrente.com.br/indice.xml\n"
INDICE = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://concorrente.com.br/posts.xml.gz</loc></sitemap>
</sitemapindex>"""
POSTS = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://concorrente.com.br/blog/como-calcular-bdi-de-obra/</loc>
       <lastmod>2026-08-01</lastmod></url>
  <url><loc>https://www.concorrente.com.br/blog/planilha-de-orcamento-2026.html</loc>
       <lastmod>2026-09-01</lastmod></url>
  <url><loc>https://concorrente.com.br/tag/orcamento-de-obra/</loc></url>
  <url><loc>https://concorrente.com.br/contato/</loc></url>
  <url><loc>https://outro-site.com/blog/nao-e-dele/</loc></url>
</urlset>"""


def _sitemaps_falsos(monkeypatch, paginas: dict[str, bytes]):
    from apps.knowledge import web

    baixados = []

    def baixar(url):
        baixados.append(url)
        if url not in paginas:
            raise web.PaginaIndisponivel(f"HTTP 404 em {url}")
        return paginas[url], url, "application/xml"

    monkeypatch.setattr(web, "baixar", baixar)
    return baixados


def test_titulo_sai_do_endereco_e_o_que_nao_e_conteudo_fica_de_fora():
    from apps.radar.concorrentes import titulo_da_url

    assert titulo_da_url("https://x.com/blog/como-calcular-bdi/") == "como calcular bdi"
    assert titulo_da_url("https://x.com/p/tabela_sinapi_2026.html") == "tabela sinapi 2026"
    assert titulo_da_url("https://x.com/blog/123456-reforma-de-banheiro") == "reforma de banheiro"
    assert titulo_da_url("https://x.com/tag/orcamento-de-obra/") is None
    assert titulo_da_url("https://x.com/quem-somos/") is None
    assert titulo_da_url("https://x.com/produto/") is None  # uma palavra so


def test_sitemap_pelo_robots_com_indice_e_gzip(monkeypatch):
    from apps.radar.concorrentes import paginas_do_sitemap

    _sitemaps_falsos(
        monkeypatch,
        {
            "https://concorrente.com.br/robots.txt": ROBOTS,
            "https://concorrente.com.br/indice.xml": INDICE,
            "https://concorrente.com.br/posts.xml.gz": gzip.compress(POSTS),
        },
    )

    paginas = paginas_do_sitemap("concorrente.com.br", limite=10)

    urls = [u for u, _ in paginas]
    # O mais recente primeiro; pagina de outro dominio fica de fora.
    assert urls[0] == "https://www.concorrente.com.br/blog/planilha-de-orcamento-2026.html"
    assert "https://outro-site.com/blog/nao-e-dele/" not in urls
    assert len(urls) == 4


def test_sitemap_com_entidade_maliciosa_e_ignorado(monkeypatch):
    from apps.radar.concorrentes import paginas_do_sitemap

    bomba = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<urlset><url><loc>&lol2;</loc></url></urlset>"""
    _sitemaps_falsos(monkeypatch, {"https://concorrente.com.br/sitemap.xml": bomba})

    assert paginas_do_sitemap("concorrente.com.br", limite=10) == []


@pytest.mark.django_db
def test_rodada_com_concorrentes_vira_sinal_sem_baixar_as_paginas(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import executar_rodada

    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.usar_serp = False
    config.usar_perguntas_do_site = False
    config.concorrentes = "https://www.Concorrente.com.br/ | Concorrente Obras\n\n"
    config.usar_concorrentes_conteudo = True
    config.save()
    baixados = _sitemaps_falsos(
        monkeypatch,
        {
            "https://concorrente.com.br/robots.txt": ROBOTS,
            "https://concorrente.com.br/indice.xml": INDICE,
            "https://concorrente.com.br/posts.xml.gz": POSTS,
        },
    )

    rodada = executar_rodada()

    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA
    textos = set(
        SinalDeDemanda.objects.filter(fonte=SinalDeDemanda.Fonte.CONCORRENTE_CONTEUDO).values_list(
            "texto", flat=True
        )
    )
    assert textos == {"como calcular bdi de obra", "planilha de orcamento 2026"}
    assert not any("/blog/" in u for u in baixados)  # so a lista, nunca a pagina
    chamada = ChamadaExterna.objects.get(provedor="web")
    assert chamada.finalidade == "concorrentes"
    assert chamada.consulta == "concorrente.com.br"


def test_lista_de_concorrentes_aceita_so_o_dominio():
    config = ConfiguracaoDoRadar(concorrentes="a.com.br\nhttps://www.b.com/ | Loja B\n  \n")
    assert config.lista_de_concorrentes == [
        {"dominio": "a.com.br", "nome": ""},
        {"dominio": "b.com", "nome": "Loja B"},
    ]


# ---------------------------------------------------------------------------
# Concorrentes: buscas (Labs) e avaliacoes
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_buscas_do_concorrente_chegam_com_volume_e_posicao(radar, monkeypatch):  # noqa: F811
    from apps.radar.concorrentes import colher_buscas

    contas = _com_dataforseo()
    config = ConfiguracaoDoRadar(concorrentes="concorrente.com.br", codigo_de_local=2076)
    enviados = []

    def post(url, json=None, auth=None, timeout=None):
        enviados.append((url, json))
        dados = {
            "status_code": 20000,
            "cost": 0.0152,
            "tasks": [
                {
                    "status_code": 20000,
                    "result": [
                        {
                            "items": [
                                {
                                    "keyword_data": {
                                        "keyword": "bdi para obra publica",
                                        "keyword_info": {"search_volume": 720},
                                    },
                                    "ranked_serp_element": {
                                        "serp_item": {
                                            "rank_group": 4,
                                            "url": "https://concorrente.com.br/bdi",
                                        }
                                    },
                                }
                            ]
                        }
                    ],
                }
            ],
        }
        return httpx.Response(200, json=dados, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)

    [sinal] = colher_buscas(config, contas, rodada=None, limite=50)

    assert enviados[0][0].endswith("/dataforseo_labs/google/ranked_keywords/live")
    assert enviados[0][1][0]["target"] == "concorrente.com.br"
    assert sinal.volume == 720
    assert sinal.extra["posicao"] == 4
    assert ChamadaExterna.objects.get().custo_usd == Decimal("0.0152")


@pytest.mark.django_db
def test_avaliacoes_so_reclamacao_e_pergunta_e_so_para_quem_tem_nome(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import colher_fila, executar_rodada

    _config_com_fila(
        usar_serp=False,
        usar_volume=False,
        concorrentes="semnome.com.br\nobras.com.br | Obras Ltda Curitiba",
        usar_avaliacoes=True,
    )
    api = DataForSEOFalsa(monkeypatch)

    rodada = executar_rodada()

    assert rodada.situacao == RodadaDoRadar.Situacao.AGUARDANDO
    [(url, corpo)] = api.posts
    assert url.endswith("/business_data/google/reviews/task_post")
    assert [c["keyword"] for c in corpo] == ["Obras Ltda Curitiba"]

    api.responder(
        "tarefa-1",
        result=[
            {
                "items": [
                    {
                        "review_text": "Orcamento veio sem o BDI, tive surpresa",
                        "rating": {"value": 2},
                    },
                    {"review_text": "Otimo atendimento", "rating": {"value": 5}},
                    {"review_text": "Voces fazem laudo de vistoria?", "rating": {"value": 5}},
                ]
            }
        ],
    )
    colher_fila()

    textos = set(
        SinalDeDemanda.objects.filter(fonte=SinalDeDemanda.Fonte.AVALIACAO).values_list(
            "texto", flat=True
        )
    )
    assert textos == {"Orcamento veio sem o BDI, tive surpresa", "Voces fazem laudo de vistoria?"}
    rodada.refresh_from_db()
    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA
    # So avaliacoes: reforcam grupos, mas nenhum vira pauta.
    assert GrupoDeDemanda.objects.exists()
    assert not GrupoDeDemanda.objects.filter(situacao=GrupoDeDemanda.Situacao.PAUTA).exists()


@pytest.mark.django_db
def test_tela_mostra_os_campos_de_concorrentes(ambiente):  # noqa: F811
    _, _, client = ambiente
    pagina = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    assert 'name="concorrentes"' in pagina
    assert 'name="modo_dataforseo"' in pagina
