"""O comando de teste da busca semantica: so le, e mostra tempo, temas e titulos."""

from __future__ import annotations

import httpx
from django.core.management import call_command

from tests.test_interface import ambiente  # noqa: F401


def test_mostra_temas_e_titulos(ambiente, monkeypatch, capsys):  # noqa: F811
    pedidos = []

    def get(url, params, timeout):
        pedidos.append(params)
        corpo = {
            "meta": {"count": 1},
            "results": [
                {
                    "display_name": "Service recovery and satisfaction",
                    "publication_year": 2000,
                    "cited_by_count": 900,
                    "language": "en",
                    "relevance_score": 0.87,
                    "primary_topic": {
                        "display_name": "Customer Service Quality and Loyalty",
                        "subfield": {"display_name": "Marketing"},
                        "field": {"display_name": "Business, Management and Accounting"},
                    },
                }
            ],
        }
        return httpx.Response(200, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr("time.sleep", lambda s: None)
    call_command("testar_busca_semantica", texto=["service recovery"])
    saida = capsys.readouterr().out
    assert pedidos[0]["search.semantic"] == "service recovery"
    assert pedidos[1]["search"] == "service recovery"
    assert "Customer Service Quality and Loyalty" in saida and "[0.87]" in saida
    assert "ms" in saida
