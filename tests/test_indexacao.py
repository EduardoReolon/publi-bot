"""O artigo publicado esta no Google? (URL Inspection do Search Console)

* a resposta do Google vira um resumo curto no artigo, com o link da inspecao;
* quem conferir: 1 dia depois de publicar; fora do indice, todo dia no
  primeiro mes e depois por semana; no indice, uma vez por mes;
* sem acesso, a rodada para no primeiro erro (os outros dariam o mesmo);
* a revisao mostra o estado e o botao do Search Console; sem Search Console
  configurado, nada aparece.
"""

from __future__ import annotations

import datetime
import json

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Article
from apps.radar import indexacao
from apps.radar.models import ConfiguracaoDoRadar
from tests.test_interface import ambiente  # noqa: F401
from tests.test_search_console import conta, tenant  # noqa: F401

FORA = {
    "inspectionResultLink": "https://search.google.com/search-console/inspect?x=1",
    "indexStatusResult": {
        "verdict": "NEUTRAL",
        "coverageState": "Discovered - currently not indexed",
        "sitemap": [],
    },
}
DENTRO = {
    "inspectionResultLink": "https://search.google.com/search-console/inspect?x=2",
    "indexStatusResult": {
        "verdict": "PASS",
        "coverageState": "Submitted and indexed",
        "lastCrawlTime": "2026-10-01T10:00:00Z",
        "sitemap": ["https://exemplo.com.br/sitemap.xml"],
    },
}


def _google(monkeypatch, resultado, *, status=200, pedidos=None):
    def post(url, **kwargs):
        if "oauth2" in url:
            corpo = {"access_token": "t", "expires_in": 3600}
            return httpx.Response(200, json=corpo, request=httpx.Request("POST", url))
        if pedidos is not None:
            pedidos.append(kwargs["json"])
        return httpx.Response(
            status,
            content=json.dumps({"inspectionResult": resultado}),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)


def _no_ar(dias=3, url="https://exemplo.com.br/a/", **campos):
    return Article.objects.create(
        title="A",
        status=Article.Status.PUBLISHED,
        published_url=url,
        published_at=timezone.now() - datetime.timedelta(days=dias),
        **campos,
    )


@pytest.mark.django_db
def test_resposta_vira_resumo_com_o_link(conta, tenant, monkeypatch):  # noqa: F811
    pedidos = []
    _google(monkeypatch, FORA, pedidos=pedidos)
    artigo = _no_ar()

    dados = indexacao.conferir(artigo)

    assert pedidos == [
        {"inspectionUrl": artigo.published_url, "siteUrl": "sc-domain:exemplo.com.br"}
    ]
    assert dados["indexada"] is False and dados["no_sitemap"] is False
    assert dados["situacao"] == "Discovered - currently not indexed"
    assert dados["link"].endswith("x=1")
    artigo.refresh_from_db()
    assert artigo.indexacao_conferida_em is not None


@pytest.mark.django_db
def test_quem_conferir_e_quando(conta, tenant):  # noqa: F811
    agora = timezone.now()
    ontem = agora - datetime.timedelta(days=1, hours=1)
    recem = _no_ar(dias=0)
    nunca = _no_ar(url="https://exemplo.com.br/n/")
    fora_hoje = _no_ar(
        url="https://exemplo.com.br/f/", indexacao={"indexada": False}, indexacao_conferida_em=agora
    )
    fora_ontem = _no_ar(
        url="https://exemplo.com.br/o/", indexacao={"indexada": False}, indexacao_conferida_em=ontem
    )
    velho_fora = _no_ar(
        dias=60,
        url="https://exemplo.com.br/v/",
        indexacao={"indexada": False},
        indexacao_conferida_em=ontem,
    )
    dentro = _no_ar(
        url="https://exemplo.com.br/d/", indexacao={"indexada": True}, indexacao_conferida_em=ontem
    )
    dentro_mes = _no_ar(
        dias=90,
        url="https://exemplo.com.br/m/",
        indexacao={"indexada": True},
        indexacao_conferida_em=agora - datetime.timedelta(days=31),
    )

    vez = set(indexacao.a_conferir())

    assert {nunca, fora_ontem, dentro_mes} <= vez
    assert not vez & {recem, fora_hoje, velho_fora, dentro}


@pytest.mark.django_db
def test_sem_acesso_a_rodada_para_no_primeiro(conta, tenant, monkeypatch):  # noqa: F811
    pedidos = []
    _google(monkeypatch, {}, status=403, pedidos=pedidos)
    _no_ar()
    _no_ar(url="https://exemplo.com.br/b/")

    assert indexacao.conferir_pendentes() == 0
    assert len(pedidos) == 1
    assert "publibot@projeto" in Article.objects.exclude(indexacao={}).get().indexacao["erro"]


@pytest.mark.django_db
def test_sem_search_console_nao_confere(tenant, settings):  # noqa: F811
    from apps.radar import search_console

    settings.GSC_CONTA_DE_SERVICO_ARQUIVO = ""
    search_console.conta_de_servico.cache_clear()
    _no_ar()
    assert indexacao.conferir_pendentes() == 0


@pytest.mark.django_db
def test_revisao_mostra_e_confere_agora(ambiente, conta, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    artigo = _no_ar()
    pagina = reverse("content:revisar", args=[artigo.pk], urlconf="core.urls_tenants")
    assert "No Google:" not in client.get(pagina).content.decode()

    config = ConfiguracaoDoRadar.carregar()
    config.propriedade_search_console = "sc-domain:exemplo.com.br"
    config.save()
    _google(monkeypatch, FORA)
    client.post(
        reverse("content:conferir_indexacao", args=[artigo.pk], urlconf="core.urls_tenants")
    )
    html = client.get(pagina).content.decode()
    assert "Discovered - currently not indexed" in html
    assert "inspect?x=1" in html and "nenhum sitemap" in html

    _google(monkeypatch, DENTRO)
    client.post(
        reverse("content:conferir_indexacao", args=[artigo.pk], urlconf="core.urls_tenants")
    )
    html = client.get(pagina).content.decode()
    assert "indexado" in html and "inspect?x=2" not in html


@pytest.mark.django_db
def test_painel_avisa_quem_devia_estar_no_google(ambiente, conta, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    config = ConfiguracaoDoRadar.carregar()
    config.propriedade_search_console = "sc-domain:exemplo.com.br"
    config.save()
    fora = {"indexada": False, "situacao": "Crawled - currently not indexed"}
    atrasado = _no_ar(dias=10, url="https://exemplo.com.br/x/", indexacao=fora)
    recente = _no_ar(dias=2, url="https://exemplo.com.br/y/", indexacao=fora)
    _no_ar(dias=10, url="https://exemplo.com.br/z/", indexacao={"indexada": True, "situacao": "ok"})

    assert list(indexacao.fora_do_google()) == [atrasado]
    painel = client.get("/").content.decode()
    assert "Fora do Google" in painel and "?indexacao=fora" in painel

    lista = client.get(
        reverse("content:artigos", urlconf="core.urls_tenants") + "?indexacao=fora"
    ).content.decode()
    assert str(atrasado.pk) in lista and str(recente.pk) not in lista

    recente.indexacao = {"erro": "sem acesso a sc-domain:exemplo.com.br."}
    recente.indexacao_conferida_em = timezone.now()
    recente.save()
    painel = client.get("/").content.decode()
    assert "Nao foi possivel conferir a indexacao" in painel
