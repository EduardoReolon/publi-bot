"""O comando que confere o ambiente: OK, FALHA com dica, ou nao configurado."""

from __future__ import annotations

import io

import httpx
import pytest
from django.core.management import call_command
from django.utils import timezone


@pytest.mark.django_db
def test_relata_cada_parte_e_sai_com_erro_quando_algo_falha(tenant_factory, monkeypatch):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    from django_tenants.utils import schema_context

    from apps.content.services import garantir_prompts_padrao
    from apps.ops import broker
    from core.celery import app

    tenant = tenant_factory("conferencia")
    with schema_context(tenant.schema_name):
        garantir_prompts_padrao()
        from apps.inference.security import cifrar
        from apps.radar.models import ContasExternas

        contas = ContasExternas.carregar()
        contas.youtube_chave_ciphertext = cifrar("chave-errada")
        contas.save()

    intervalo = IntervalSchedule.objects.create(every=5, period="minutes")
    PeriodicTask.objects.create(name="t", task="x", interval=intervalo, last_run_at=timezone.now())
    monkeypatch.setattr(broker, "mensagens_pendentes", lambda fila: 0)
    monkeypatch.setattr(app.control, "ping", lambda timeout: [{"w@x": {"ok": "pong"}}])

    def recusa(url, **kwargs):
        return httpx.Response(
            400,
            json={"error": {"errors": [{"reason": "keyInvalid"}]}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", recusa)

    from apps.radar import links_quebrados

    respostas = {"Brasil": 200, "nao_existe": 404, "invalid": 0}
    monkeypatch.setattr(
        links_quebrados,
        "codigo_http",
        lambda url: next(v for k, v in respostas.items() if k in url),
    )

    saida = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("conferir_instalacao", schema=tenant.schema_name, stdout=saida)
    texto = saida.getvalue()
    assert "OK    worker do Celery: 1 respondendo" in texto
    assert "OK    beat (agendamentos)" in texto
    assert "OK    prompts" in texto
    assert "FALHA YouTube: HTTP 400 keyInvalid" in texto
    assert "--    DataForSEO: nao configurado" in texto
    assert "--    site" in texto
    assert "OK    links quebrados (verificador): 3 casos" in texto


@pytest.mark.django_db
def test_completo_passa_pelas_funcoes_de_uso_e_confere_o_formato(tenant_factory, monkeypatch):
    from django_tenants.utils import schema_context

    from apps.content.services import garantir_prompts_padrao
    from apps.knowledge import academicos, videos, web
    from apps.radar import links_quebrados, provedores, youtube
    from apps.radar.provedores import ItemDeBusca, ResultadoDeBusca

    tenant = tenant_factory("conferencia_completa")
    with schema_context(tenant.schema_name):
        garantir_prompts_padrao()
        from apps.inference.security import cifrar
        from apps.radar.models import ConfiguracaoDoRadar, ContasExternas

        contas = ContasExternas.carregar()
        contas.dataforseo_login = "login"
        contas.youtube_chave_ciphertext = cifrar("chave")
        contas.save()
        config = ConfiguracaoDoRadar.carregar()
        config.artigos_cientificos = True
        config.save()

    ok = httpx.Response(200, json={"tasks": [{"status_code": 20000, "result": [{}]}]})
    monkeypatch.setattr(httpx, "get", lambda url, **kw: ok)
    monkeypatch.setattr(links_quebrados, "codigo_http", lambda url: 200)
    monkeypatch.setattr(web, "texto_da_pagina", lambda url: "texto " * 100)
    # Organico sem titulo: o formato que o codigo nao aceita.
    monkeypatch.setattr(
        provedores,
        "buscar_dataforseo",
        lambda consulta, **kw: ResultadoDeBusca("dataforseo", [ItemDeBusca("https://a.com/", "")]),
    )
    monkeypatch.setattr(
        provedores, "metricas_dataforseo", lambda palavras, **kw: {palavras[0]: {"volume": 90}}
    )
    monkeypatch.setattr(youtube, "buscar_videos", lambda c, **kw: [{"id": "v1", "titulo": "t"}])
    monkeypatch.setattr(youtube, "comentarios", lambda v, **kw: [{"texto": "oi", "curtidas": 1}])
    monkeypatch.setattr(videos, "buscar_legenda", lambda v: [(0.0, "ola")])
    monkeypatch.setattr(
        academicos,
        "buscar_openalex",
        lambda c, **kw: [academicos.Trabalho(titulo="Estudo", doi="10.1/x")],
    )

    saida = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("conferir_instalacao", schema=tenant.schema_name, completo=True, stdout=saida)
    texto = saida.getvalue()
    assert "OK    leitura de pagina (fontes)" in texto
    assert "FALHA DataForSEO: busca no Google: a resposta nao veio" in texto
    assert "OK    DataForSEO: volume de busca: 'como fazer bolo de cenoura': 90" in texto
    assert "OK    YouTube: videos, comentarios e legenda: video v1: 1 comentarios" in texto
    assert "OK    OpenAlex e Unpaywall: artigos: 1 artigos" in texto
    assert "--    Search Console: cliques e posicoes: nao configurado" in texto


@pytest.mark.django_db
def test_sem_completo_nao_faz_chamada_de_uso(tenant_factory, monkeypatch):
    from apps.radar import provedores

    tenant = tenant_factory("conferencia_simples")
    monkeypatch.setattr(
        provedores, "buscar_dataforseo", lambda *a, **kw: pytest.fail("chamou a busca paga")
    )
    saida = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("conferir_instalacao", schema=tenant.schema_name, stdout=saida)
    assert "busca no Google" not in saida.getvalue()
