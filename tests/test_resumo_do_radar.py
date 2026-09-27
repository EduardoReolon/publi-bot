"""Rendimento das sementes (algoritmo) e o texto para colar noutra IA."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.radar.models import ConfiguracaoDoRadar, SinalDeDemanda
from tests.test_interface import ambiente  # noqa: F401


def _sinal(texto, semente, volume=None, fonte="paa"):
    return SinalDeDemanda.objects.create(
        texto=texto, fonte=fonte, volume=volume, extra={"semente": semente}
    )


@pytest.mark.django_db
def test_rendimento_mostra_qual_semente_traz_busca(ambiente):  # noqa: F811
    from apps.radar.resumo import rendimento_das_sementes

    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "Auditoria Comercial\nCross-sell\nMatriz RFM"
    config.save()
    _sinal("O que e auditoria comercial?", "Auditoria Comercial")
    _sinal("Quais sao as 4 grandes auditorias?", "Auditoria Comercial")
    _sinal("Up sell", "Cross-sell", 9900, fonte="relacionada")
    _sinal("Cross sell vs upsell", "Cross-sell", 170, fonte="relacionada")

    linhas = {r["semente"]: r for r in rendimento_das_sementes()}

    assert next(iter(linhas)) == "Cross-sell"
    assert linhas["Cross-sell"]["volume"] == 10070
    assert linhas["Cross-sell"]["melhor_sinal"] == "Up sell"
    assert (linhas["Auditoria Comercial"]["sinais"], linhas["Auditoria Comercial"]["volume"]) == (
        2,
        0,
    )
    assert linhas["Matriz RFM"]["sinais"] == 0  # configurada, ainda nao buscada


@pytest.mark.django_db
def test_texto_para_ia_traz_o_negocio_e_os_dados(ambiente):  # noqa: F811
    from apps.editorial.models import PerfilDoNegocio

    _, _, client = ambiente
    PerfilDoNegocio.objects.update_or_create(
        pk=1, defaults={"tema": "IA para PMEs", "oferta": "Analise RFM dos clientes"}
    )
    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "Cross-sell"
    config.save()
    _sinal("Up sell", "Cross-sell", 9900, fonte="relacionada")

    pagina = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()

    assert "Rendimento das sementes" in pagina and "Copiar para outra IA" in pagina
    assert "Oferta (porta de entrada): Analise RFM dos clientes" in pagina
    assert "- Cross-sell: 1 sinais, 1 com volume, volume somado 9900" in pagina
    assert "Up sell | 9900 | Cross-sell" in pagina
