"""O passado no LinkedIn e no Google (API), o historico e o gasto pela planilha
exportada de cada rede. APIs sobre respostas simuladas, no formato das
documentacoes do LinkedIn e do Google."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.social import anuncios, diagnostico, historico, planilhas
from apps.social.models import Comentario, Destino, Post
from apps.social.tests.test_apis import Rede, _conectado

U = "core.urls_tenants"


# -- LinkedIn (pagina) ---------------------------------------------------------------------
def _post_do_linkedin(i: int) -> dict:
    return {
        "id": f"urn:li:share:{i}",
        "commentary": f"Post {i} sobre \\#saude e \\(prevencao\\)",
        "publishedAt": int(datetime(2026, 1, i + 1, 12, tzinfo=UTC).timestamp() * 1000),
        "lifecycleState": "PUBLISHED",
        "content": {"media": {"id": f"urn:li:image:{i}"}} if i % 2 else {},
    }


def _rede_do_linkedin() -> Rede:
    def posts(pedido):
        inicio = int(pedido.url.params["start"])
        return httpx.Response(
            200,
            json={"elements": [_post_do_linkedin(i) for i in range(inicio, min(inicio + 50, 3))]},
        )

    def estatisticas(pedido):
        assert pedido.url.params["shares"].startswith("List(urn:li:share:")
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "totalShareStatistics": {
                            "uniqueImpressionsCount": 400,
                            "impressionCount": 650,
                            "likeCount": 12,
                            "commentCount": 1,
                            "shareCount": 2,
                            "clickCount": 30,
                        }
                    }
                ]
            },
        )

    comentario = {
        "elements": [
            {
                "$URN": "urn:li:comment:(urn:li:share:0,9)",
                "message": {"text": "Qual a idade certa para o exame?"},
                "actor": "urn:li:person:x",
                "created": {"time": 1767268800000},
            }
        ]
    }
    return Rede(
        {
            "GET api.linkedin.com/rest/posts": posts,
            "GET api.linkedin.com/rest/organizationalEntityShareStatistics": estatisticas,
            "GET api.linkedin.com/rest/socialActions/": httpx.Response(200, json=comentario),
            "GET api.linkedin.com/rest/networkSizes/": httpx.Response(
                200, json={"firstDegreeSize": 321}
            ),
        }
    )


@pytest.mark.django_db
def test_pagina_do_linkedin_importa_posts_numeros_e_comentarios(ambiente):
    pagina = _conectado("linkedin", "urn:li:organization:5", autor="organizacao")
    pessoal = _conectado("linkedin", "urn:li:person:1", autor="pessoa")
    assert historico.suporta(pagina) and not historico.suporta(pessoal)

    rede = _rede_do_linkedin()
    feito = historico.importar(pagina, http=rede.cliente())
    assert feito["novos"] == 3 and feito["faltam"] == 0
    post = pagina.posts.get(id_remoto="urn:li:share:1")
    assert post.texto == "Post 1 sobre #saude e (prevencao)"  # sem as barras do LinkedIn
    assert post.extras["formato"] == "IMAGE"
    assert pagina.posts.get(id_remoto="urn:li:share:0").extras["formato"] == "TEXT"
    assert post.metricas["alcance"] == 400 and post.metricas["cliques_na_rede"] == 30
    assert post.url_remota == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    assert Comentario.objects.filter(post__destino=pagina).count() == 3
    pagina.refresh_from_db()
    assert pagina.seguidores == 321


# -- Google ------------------------------------------------------------------------------------
def _meses_atras(n: int) -> tuple[int, int]:
    hoje = timezone.localdate()
    total = hoje.year * 12 + hoje.month - 1 - n
    return total // 12, total % 12 + 1


def _rede_do_google() -> Rede:
    posts, valores = [], []
    for n in range(0, 9):
        ano, mes = _meses_atras(n)
        com_post = n % 2 == 1
        if com_post:
            posts.append(
                {
                    "name": f"accounts/1/locations/9/localPosts/{n}",
                    "summary": f"Novidade do mes {mes}",
                    "createTime": f"{ano:04d}-{mes:02d}-10T15:00:00Z",
                    "searchUrl": f"https://local.google.com/post/{n}",
                    "state": "LIVE",
                }
            )
        valores.append(
            {"date": {"year": ano, "month": mes, "day": 5}, "value": "50" if com_post else "10"}
        )
    desempenho = {
        "multiDailyMetricTimeSeries": [
            {
                "dailyMetricTimeSeries": [
                    {"dailyMetric": "CALL_CLICKS", "timeSeries": {"datedValues": valores}},
                    {
                        "dailyMetric": "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
                        "timeSeries": {"datedValues": valores},
                    },
                    {
                        "dailyMetric": "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
                        "timeSeries": {"datedValues": valores},
                    },
                ]
            }
        ]
    }
    avaliacoes = {
        "averageRating": 4.6,
        "totalReviewCount": 3,
        "reviews": [
            {"starRating": "FIVE", "reviewReply": {"comment": "Obrigado!"}},
            {"starRating": "FIVE"},
            {"starRating": "TWO", "comment": "Demorou"},
        ],
    }
    return Rede(
        {
            "GET mybusiness.googleapis.com/v4/accounts/1/locations/9/localPosts": httpx.Response(
                200, json={"localPosts": posts}
            ),
            "GET mybusiness.googleapis.com/v4/accounts/1/locations/9/reviews": httpx.Response(
                200, json=avaliacoes
            ),
            "GET businessprofileperformance.googleapis.com/v1/locations/9:fetch": httpx.Response(
                200, json=desempenho
            ),
        }
    )


@pytest.mark.django_db
def test_google_importa_posts_e_o_resultado_da_conta_mes_a_mes(ambiente):
    destino = _conectado("gmn", "accounts/1/locations/9", autor="local")
    assert historico.suporta(destino)
    rede = _rede_do_google()
    feito = historico.importar(destino, http=rede.cliente())
    assert feito["novos"] == 4

    destino.refresh_from_db()
    meses = destino.desempenho["meses"]
    assert len(meses) == 9 and meses[-1]["ligacoes"] == 10
    assert meses[-2]["visualizacoes"] == 100  # as metricas de impressao somam
    assert destino.desempenho["avaliacoes"]["baixas_sem_resposta"] == 1
    pedido = next(p for p in rede.pedidos if "fetchMultiDailyMetricsTimeSeries" in str(p.url))
    assert "CALL_CLICKS" in pedido.url.params.get_list("dailyMetrics")

    d = diagnostico.montar(destino)
    assert sum(m["posts"] for m in d.conta["meses"]) == 4
    assert any("meses com post rende 5.0x" in p.texto for p in d.conta["bons"])
    assert any("1 a 3 estrelas" in p.texto for p in d.conta["ruins"])
    assert "No Google" in diagnostico.como_texto(d)

    _, _, client = ambiente
    pagina = client.get(
        reverse("social:diagnostico", urlconf=U), {"destino": destino.pk}
    ).content.decode()
    assert "A conta no Google, mes a mes" in pagina and "Google Ads" in pagina


# -- Planilha de posts --------------------------------------------------------------------------
def _xlsx(abas: dict[str, list[list]]) -> bytes:
    livro = openpyxl.Workbook()
    livro.remove(livro.active)
    for nome, linhas in abas.items():
        aba = livro.create_sheet(nome)
        for linha in linhas:
            aba.append(linha)
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


TOP_POSTS = _xlsx(
    {
        "DISCOVERY": [["Overall Performance", "1/1/2026 - 12/31/2026"], ["Impressions", 900]],
        "TOP POSTS": [
            ["Maximum of 50 posts available to include in this list"],
            [],
            [
                "Post URL",
                "Post publish date",
                "Engagements",
                None,
                "Post URL",
                "Post publish date",
                "Impressions",
            ],
            [
                "https://www.linkedin.com/feed/update/urn:li:activity:111",
                "3/14/2026",
                40,
                None,
                "https://www.linkedin.com/feed/update/urn:li:activity:111",
                "3/14/2026",
                2000,
            ],
            [
                "https://www.linkedin.com/feed/update/urn:li:activity:222",
                "3/2/2026",
                5,
                None,
                "https://www.linkedin.com/feed/update/urn:li:activity:333",
                "2/20/2026",
                700,
            ],
        ],
    }
)
SHARES = (
    "Date,ShareLink,ShareCommentary,SharedUrl,MediaUrl,Visibility\n"
    "2026-03-14 10:02:11,https://www.linkedin.com/feed/update/urn%3Ali%3Ashare%3A9,"
    '"Tres sinais de que o cansaco nao e normal",,,MEMBER_NETWORK\n'
)


@pytest.mark.django_db
def test_planilhas_do_linkedin_pessoal_juntam_texto_e_numeros(ambiente):
    _, _, client = ambiente
    destino = Destino.objects.create(rede="linkedin", nome="Eu", autor="pessoa")
    arquivo = SimpleUploadedFile("Content_2026.xlsx", TOP_POSTS)
    resposta = client.post(
        reverse("social:acao_no_diagnostico", urlconf=U),
        {"destino": destino.pk, "acao": "planilha_de_posts", "planilha": arquivo},
    )
    assert resposta.status_code == 302
    assert destino.posts.count() == 3  # 111 aparece nas duas tabelas: um post so
    post = destino.posts.get(url_remota__contains="activity:111")
    assert post.metricas == {"engajamentos": 40, "impressoes": 2000}
    assert timezone.localtime(post.publicado_em).date().isoformat() == "2026-03-14"

    # O texto vem do Shares.csv (outro id): junta pelo dia.
    feito = historico.importar_planilha(destino, SHARES.encode())
    assert feito == {"linhas": 1, "novos": 0, "atualizados": 1}
    post.refresh_from_db()
    assert post.texto.startswith("Tres sinais") and post.extras["gancho"]

    # Mandar de novo nao duplica.
    historico.importar_planilha(destino, TOP_POSTS)
    assert destino.posts.count() == 3
    # Sem curtidas separadas, os engajamentos medem o post.
    assert diagnostico._interacoes(post) == 40.0

    pagina = client.get(
        reverse("social:diagnostico", urlconf=U), {"destino": destino.pk}
    ).content.decode()
    assert "Pelo arquivo exportado da rede" in pagina and "Shares" in pagina


def test_planilha_sem_colunas_de_post_explica():
    destino = Destino(rede="linkedin", nome="x")
    with pytest.raises(planilhas.PlanilhaInvalida, match="colunas de post"):
        historico.importar_planilha(destino, b"Nome,Idade\nA,10\n")


# -- Gasto pela planilha: LinkedIn e Google Ads ---------------------------------------------------
LINKEDIN_ADS = (
    "Ad Performance Report (in UTC)\n"
    "Report Start: 1/1/2026, 12:00 AM\n"
    "\n"
    "Start Date (in UTC),Campaign Name,Ad ID,Ad Introduction Text,Total Spent,Impressions,"
    "Clicks\n"
    '1/15/2026,Promo,777,"Tres sinais de que o cansaco nao e normal",120.50,9000,85\n'
)
GOOGLE_ADS = (
    "Relatorio de campanhas\n"
    '"1 de janeiro de 2026 - 30 de setembro de 2026"\n'
    "Campanha,Status da campanha,Custo,Custo / conv.,Impr.,Cliques,Conversoes\n"
    'Busca local,Ativada,"1.234,56","12,00",50000,900,"40,00"\n'
    'Total: conta,,"1.234,56",,50000,900,40\n'
)


@pytest.mark.django_db
def test_planilhas_de_anuncio_do_linkedin_e_do_google(ambiente):
    linkedin = Destino.objects.create(rede="linkedin", nome="Pagina", autor="organizacao")
    post = Post.objects.create(
        destino=linkedin,
        motivo=Post.Motivo.HISTORICO,
        situacao=Post.Situacao.PUBLICADO,
        texto="Tres sinais de que o cansaco nao e normal\n\nVeja o artigo.",
        publicado_em=timezone.now(),
    )
    assert anuncios.importar_planilha(linkedin, LINKEDIN_ADS.encode()) == {
        "anuncios": 1,
        "ligados": 1,
    }
    anuncio = linkedin.anuncios.get()
    assert anuncio.id_remoto == "777" and anuncio.gasto == Decimal("120.50")
    assert anuncio.inicio.isoformat() == "2026-01-15" and anuncio.cliques == 85
    post.refresh_from_db()
    assert post.impulsionado and post.custo_impulso == Decimal("120.50")

    google = Destino.objects.create(rede="gmn", nome="Local", autor="local")
    assert anuncios.importar_planilha(google, GOOGLE_ADS.encode("latin-1"))["anuncios"] == 1
    campanha = google.anuncios.get()  # a linha "Total" fica de fora
    assert campanha.nome == "Busca local" and campanha.gasto == Decimal("1234.56")
    assert campanha.impressoes == 50000 and campanha.resultados == 40 and campanha.post is None
    assert "Google Ads" in anuncios.como_exportar("gmn")[0]


def test_colunas_e_datas_das_planilhas():
    mapa = planilhas.mapear(
        ["Custo / conv.", "Custo", "Cliques (todos)", "Cliques no link"],
        {
            "gasto": ("custo",),
            "cliques": ("cliques no link", "cliques"),
        },
    )
    assert mapa == {"gasto": 1, "cliques": 3}
    assert planilhas.data("3/14/2026", mes_primeiro=True).isoformat() == "2026-03-14"
    assert planilhas.data("14/03/2026").isoformat() == "2026-03-14"
    assert planilhas.data("3/14/2026").isoformat() == "2026-03-14"  # ordem trocada se corrige
    assert planilhas.momento("03/10/2025 14:30").hour == 14
    assert planilhas.mes_primeiro(["3/2/2026", "3/14/2026"], False) is True
    assert planilhas.mes_primeiro(["14/3/2026"], True) is False
