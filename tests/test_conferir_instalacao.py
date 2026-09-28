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

    respostas = {"Brasil": None, "nao_existe": 404, "invalid": 0}
    monkeypatch.setattr(
        links_quebrados,
        "situacao_do_link",
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
