"""Regioes: mais de uma cidade ou estado, com o volume somado.

* sem regiao, o pais inteiro; com regioes, cada busca e feita em cada uma;
* o volume de cada regiao fica guardado no sinal, com o historico mensal e o
  custo por clique, e o volume do sinal e a SOMA;
* regiao dentro de outra (Curitiba e Parana) e recusada: contaria duas vezes;
* a lista de locais vem da DataForSEO e o seletor procura sem acento.
"""

from __future__ import annotations

import httpx
import pytest
from django.urls import reverse

from apps.radar.models import ConfiguracaoDoRadar, LocalDisponivel, SinalDeDemanda, TarefaNaFila
from tests.test_fila_e_concorrentes import DataForSEOFalsa, _config_com_fila
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import RESPOSTA_SERP, radar  # noqa: F401

CURITIBA = {"codigo": 1001773, "nome": "Curitiba, Parana", "tipo": "cidade"}
LONDRINA = {"codigo": 1001780, "nome": "Londrina, Parana", "tipo": "cidade"}


def test_sem_regiao_e_o_pais_inteiro():
    config = ConfiguracaoDoRadar(codigo_de_local=2076)
    assert config.locais() == [(2076, "")]
    config.regioes = [CURITIBA, LONDRINA]
    assert config.locais() == [(1001773, "Curitiba, Parana"), (1001780, "Londrina, Parana")]
    assert config.local_principal == 1001773


@pytest.mark.django_db
def test_rodada_em_duas_cidades_soma_o_volume(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import colher_fila, executar_rodada

    _config_com_fila(sementes="dermatologista", regioes=[CURITIBA, LONDRINA])
    api = DataForSEOFalsa(monkeypatch)

    executar_rodada()

    # Uma tarefa de busca por semente EM CADA cidade.
    [(_url, corpo)] = api.posts
    assert sorted(c["location_code"] for c in corpo) == [1001773, 1001780]
    assert {t.contexto["local"] for t in TarefaNaFila.objects.all()} == {1001773, 1001780}

    for task_id in ("tarefa-1", "tarefa-2"):
        api.responder(task_id, result=RESPOSTA_SERP["tasks"][0]["result"])
    colher_fila()

    # Volume: uma tarefa por cidade, com dois anos de historico pedidos.
    url_volume, corpo_volume = api.posts[-1]
    assert url_volume.endswith("/search_volume/task_post")
    assert sorted(c["location_code"] for c in corpo_volume) == [1001773, 1001780]
    assert all(c["date_from"] for c in corpo_volume)

    def resultado(volume, cpc):
        meses = [{"year": 2026, "month": m, "search_volume": volume} for m in (7, 8)]
        return [
            {
                "keyword": "como calcular o bdi",
                "search_volume": volume,
                "cpc": cpc,
                "competition_index": 30,
                "monthly_searches": meses,
            }
        ]

    api.responder("tarefa-3", result=resultado(1000, 2.5))
    api.responder("tarefa-4", result=resultado(300, 1.0))
    colher_fila()

    sinal = SinalDeDemanda.objects.get(texto="Como calcular o BDI?")
    assert sinal.volume == 1300
    por_local = sinal.extra["metricas"]
    assert set(por_local) == {"1001773", "1001780"}
    assert por_local["1001773"]["cpc"] == 2.5
    assert por_local["1001780"]["meses"] == [[2026, 7, 300], [2026, 8, 300]]


@pytest.mark.django_db
def test_regiao_dentro_de_outra_e_recusada(radar):  # noqa: F811
    from apps.radar.forms import ConfiguracaoForm

    LocalDisponivel.objects.create(codigo=20099, nome="Parana", tipo="State", pais="BR")
    LocalDisponivel.objects.create(
        codigo=1001773, nome="Curitiba, Parana", tipo="City", pai=20099, pais="BR"
    )
    dados = {
        "intensidade": "minimo",
        "buscador": "dataforseo",
        "modo_dataforseo": "fila",
        "taxa_de_comparacao": 0,
        "codigo_de_local": 2076,
        "codigo_de_idioma": "pt",
        "teto_mensal_usd": "2",
        "fontes_por_pauta": 3,
    }
    import json

    form = ConfiguracaoForm(
        {**dados, "regioes": json.dumps([CURITIBA, {"codigo": 20099, "nome": "Parana"}])},
        instance=ConfiguracaoDoRadar.carregar(),
    )
    assert not form.is_valid()
    assert "contado duas" in form.errors["regioes"][0]

    form = ConfiguracaoForm(
        {**dados, "regioes": json.dumps([CURITIBA, CURITIBA, LONDRINA])},
        instance=ConfiguracaoDoRadar.carregar(),
    )
    assert form.is_valid(), form.errors
    assert [r["codigo"] for r in form.cleaned_data["regioes"]] == [1001773, 1001780]


@pytest.mark.django_db
def test_lista_de_locais_e_procura_sem_acento(radar, monkeypatch):  # noqa: F811
    from apps.radar.locais import atualizar_lista, procurar
    from tests.test_radar import _com_dataforseo

    itens = [
        {
            "location_code": 2076,
            "location_name": "Brazil",
            "location_type": "Country",
            "country_iso_code": "BR",
        },
        {
            "location_code": 20099,
            "location_name": "State of Parana,Brazil",
            "location_code_parent": 2076,
            "location_type": "State",
            "country_iso_code": "BR",
        },
        {
            "location_code": 1001773,
            "location_name": "Curitiba,State of Parana,Brazil",
            "location_code_parent": 20099,
            "location_type": "City",
            "country_iso_code": "BR",
        },
        {
            "location_code": 9999,
            "location_name": "80000-000,Brazil",
            "location_type": "Postal Code",
            "country_iso_code": "BR",
        },
    ]

    def get(url, auth=None, timeout=None):
        assert url.endswith("/keywords_data/google_ads/locations/br")
        corpo = {"status_code": 20000, "cost": 0, "tasks": [{"result": itens}]}
        return httpx.Response(200, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)

    assert atualizar_lista(_com_dataforseo()) == 3  # o CEP fica de fora
    assert LocalDisponivel.objects.get(codigo=1001773).nome == "Curitiba, Parana"
    assert procurar("paraná")[0] == {"codigo": 20099, "nome": "Parana", "tipo": "estado"}
    assert [x["codigo"] for x in procurar("curit")] == [1001773]


@pytest.mark.django_db
def test_tela_tem_o_seletor_e_a_busca_responde(ambiente):  # noqa: F811
    _, _, client = ambiente
    LocalDisponivel.objects.create(
        codigo=1001773, nome="Curitiba, Parana", tipo="City", busca="curitiba, parana"
    )
    pagina = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    assert 'id="seletor-regioes"' in pagina

    resposta = client.get(
        reverse("radar:procurar_locais", urlconf="core.urls_tenants"), {"q": "Curitiba"}
    )
    assert resposta.json()["locais"][0]["codigo"] == 1001773
