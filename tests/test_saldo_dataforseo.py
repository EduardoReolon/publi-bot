"""O saldo da DataForSEO no cartao de gasto do radar."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from django.urls import reverse

from apps.inference.security import cifrar
from apps.radar import provedores
from apps.radar.models import ContasExternas
from tests.test_interface import ambiente  # noqa: F401


def _conta():
    contas = ContasExternas.carregar()
    contas.dataforseo_login = "login"
    contas.dataforseo_senha_ciphertext = cifrar("senha")
    contas.save()
    return contas


def _responde(saldo):
    def get(url, **kwargs):
        return httpx.Response(
            200,
            json={"tasks": [{"status_code": 20000, "result": [{"money": {"balance": saldo}}]}]},
            request=httpx.Request("GET", url),
        )

    return get


@pytest.mark.django_db
def test_le_e_mostra_o_saldo(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    _conta()
    # O conftest troca a leitura do saldo por um nada; aqui vale a de verdade.
    monkeypatch.undo()
    monkeypatch.setattr(provedores.httpx, "get", _responde(0.8554))

    client.post(reverse("radar:atualizar_saldo", urlconf="core.urls_tenants"))

    assert ContasExternas.carregar().saldo_dataforseo == Decimal("0.8554")
    pagina = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    assert "saldo DataForSEO: US$ 0,86" in pagina or "saldo DataForSEO: US$ 0.86" in pagina


@pytest.mark.django_db
def test_falha_na_leitura_nao_quebra_nada(ambiente, monkeypatch):  # noqa: F811
    _conta()
    monkeypatch.undo()  # a leitura de verdade, e nao a do conftest

    def recusa(url, **kwargs):
        return httpx.Response(401, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(provedores.httpx, "get", recusa)
    provedores.atualizar_saldo_dataforseo()

    assert ContasExternas.carregar().saldo_dataforseo is None
