"""Perfil do negocio: a referencia do que o PubliBot mede.

* a tela salva tema, publico, oferta, valores e as dores (guardadas no radar);
* o publico vira o padrao do planejamento; o tema e a oferta, a referencia do
  "perto do tema do site"; a oferta, a da chamada.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.editorial.models import PerfilDoNegocio
from tests.test_interface import ambiente  # noqa: F401


@pytest.mark.django_db
def test_tela_salva_o_negocio_e_as_dores(ambiente):  # noqa: F811
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    url = reverse("editorial:negocio", urlconf="core.urls_tenants")
    assert "Como o PubliBot usa cada informacao" in client.get(url).content.decode()

    # Passo 1: so o basico, do jeito da pessoa.
    client.post(url, {"acao": "basico", "oferta": "Confiro notas", "publico": "Donos de obra"})
    assert PerfilDoNegocio.carregar().oferta == "Confiro notas"

    resposta = client.post(
        url,
        {
            "acao": "salvar",
            "tema": "Custos de obra",
            "publico": "Donos de obra sem formacao tecnica",
            "oferta": "Engenheiro confere suas notas pelo WhatsApp",
            "dores": "obra estourou o orcamento\npaguei caro no cimento",
        },
    )
    assert resposta.status_code == 302
    client.post(
        url,
        {
            "acao": "valores",
            "valor_da_conversao": "150",
            "investimento_publibot": "300",
            "investimento_anuncios": "900",
            "cotacao_do_dolar": "5.40",
        },
    )
    perfil = PerfilDoNegocio.carregar()
    assert perfil.tema == "Custos de obra" and perfil.valor_da_conversao == 150
    assert ConfiguracaoDoRadar.carregar().lista_de_dores == [
        "obra estourou o orcamento",
        "paguei caro no cimento",
    ]


@pytest.mark.django_db
def test_cada_campo_chega_onde_e_usado(ambiente):  # noqa: F811
    from apps.content.chamada import texto_da_oferta
    from apps.content.flows import _publico_padrao
    from apps.radar.agrupamento import _texto_do_negocio

    PerfilDoNegocio.objects.update_or_create(
        pk=1,
        defaults={"tema": "Custos de obra", "publico": "Donos de obra", "oferta": "Planilha"},
    )

    assert _publico_padrao(None) == "Donos de obra"
    assert _texto_do_negocio().startswith("Custos de obra. Planilha")
    assert texto_da_oferta() == "Planilha"
