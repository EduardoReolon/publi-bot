"""Adaptadores de dados publicos, sobre respostas no formato das APIs, sem rede.

As respostas abaixo seguem o formato documentado de cada API (IBGE agregados
v3, CKAN e SGS do Banco Central, GHO OData da OMS). Se a instituicao mudar o
formato, quem avisa e `manage.py conferir_adaptadores`, que roda contra as
APIs de verdade.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.dados import adaptadores
from apps.dados.adaptadores import IBGE, OMS, BancoCentral, periodo_legivel, periodo_ordenavel
from tests.test_interface import ambiente  # noqa: F401

AGREGADOS = [
    {
        "id": "PNS",
        "nome": "Pesquisa Nacional de Saude",
        "agregados": [
            {"id": "4160", "nome": "Pessoas que tem plano de saude medico"},
            {"id": "4161", "nome": "Pessoas que avaliam a saude como boa"},
        ],
    },
    {"id": "CD", "nome": "Censo Demografico", "agregados": [{"id": "93", "nome": "Populacao"}]},
]

METADADOS_4160 = {
    "id": 4160,
    "nome": "Pessoas de 18 anos ou mais que tem plano de saude",
    "URL": "http://sidra.ibge.gov.br/tabela/4160",
    "pesquisa": "Pesquisa Nacional de Saude",
    "periodicidade": {"frequencia": "anual", "inicio": 2013, "fim": 2019},
    "nivelTerritorial": {"Administrativo": ["N1", "N2", "N3"], "Especial": [], "IBGE": []},
    "variaveis": [
        {"id": 1000, "nome": "Pessoas com plano de saude - percentual", "unidade": "%"},
        {"id": 1001, "nome": "Pessoas com plano de saude", "unidade": "Mil pessoas"},
    ],
    "classificacoes": [],
}

VALORES_4160 = [
    {
        "id": "1000",
        "variavel": "Pessoas com plano de saude - percentual",
        "unidade": "%",
        "resultados": [
            {
                "classificacoes": [
                    {"id": "2", "nome": "Sexo", "categoria": {"4": "Homens"}},
                ],
                "series": [
                    {
                        "localidade": {"id": "41", "nivel": {"id": "N3", "nome": "UF"}},
                        "serie": {"2019": "30.1"},
                    }
                ],
            },
            {
                "classificacoes": [
                    {"id": "2", "nome": "Sexo", "categoria": {"6794": "Total"}},
                ],
                "series": [
                    {
                        "localidade": {"id": "41", "nivel": {"id": "N3", "nome": "UF"}},
                        "serie": {"2013": "...", "2019": "32.4"},
                    }
                ],
            },
        ],
    }
]


@pytest.fixture
def respostas(monkeypatch):
    """Troca a rede por um dicionario url -> resposta, e guarda os pedidos."""
    pedidos = []
    tabela = {}

    def _get(self, url, params=None):
        pedidos.append((url, params or {}))
        for prefixo, resposta in tabela.items():
            if url.startswith(prefixo):
                return resposta
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(adaptadores._ComRede, "_get", _get)
    return SimpleNamespace(tabela=tabela, pedidos=pedidos)


def test_ibge_procura_pelo_nome_e_lista_as_variaveis(respostas):
    base = IBGE.BASE
    respostas.tabela[f"{base}/4160/metadados"] = METADADOS_4160
    respostas.tabela[base] = AGREGADOS

    achadas = IBGE().procurar("plano de saúde")

    assert [a.codigo for a in achadas] == ["4160/1000", "4160/1001"]
    primeira = achadas[0]
    assert primeira.unidade == "%" and primeira.periodicidade == "anual"
    assert primeira.recortes == ["Brasil", "estado"]
    assert primeira.url == "https://sidra.ibge.gov.br/tabela/4160"
    assert "Pesquisa Nacional de Saude" in primeira.descricao


def test_ibge_valor_do_estado_e_o_total_e_pula_ausente(respostas):
    respostas.tabela[f"{IBGE.BASE}/4160/periodos"] = VALORES_4160
    serie = SimpleNamespace(codigo="4160/1000", periodicidade="anual")

    valores = IBGE().valores(serie, local="Parana", ultimos=2)

    url, params = respostas.pedidos[-1]
    assert url.endswith("/4160/periodos/-2/variaveis/1000")
    assert params == {"localidades": "N3[41]"}
    # O total (e nao o recorte "Homens"), e o "..." (sem dado) fica de fora.
    assert [(v.periodo, v.valor, v.local) for v in valores] == [("2019", Decimal("32.4"), "Paraná")]


def test_ibge_tabela_sem_variavel_usa_a_primeira_e_le_o_link(respostas):
    respostas.tabela[f"{IBGE.BASE}/4160/metadados"] = METADADOS_4160
    respostas.tabela[f"{IBGE.BASE}/4160/periodos"] = VALORES_4160
    ibge = IBGE()

    ibge.valores(SimpleNamespace(codigo="4160", periodicidade=""), local="Brasil")

    assert respostas.pedidos[-1] == (
        f"{IBGE.BASE}/4160/periodos/-1/variaveis/1000",
        {"localidades": "N1[all]"},
    )
    assert ibge.codigo_do_link("https://sidra.ibge.gov.br/tabela/4752#resultado") == "4752"
    assert ibge.codigo_do_link("https://apisidra.ibge.gov.br/values/t/93/n1/all") == "93"
    assert ibge.codigo_do_link("https://www.ibge.gov.br/noticias") == ""
    assert ibge.valores(SimpleNamespace(codigo="4160", periodicidade=""), local="Atlantida") == []


def test_banco_central_procura_no_portal_e_le_o_sgs(respostas):
    respostas.tabela[BancoCentral.BUSCA] = {
        "success": True,
        "result": {
            "count": 2,
            "results": [
                {
                    "name": "433-indice-nacional-de-precos-ao-consumidor-amplo-ipca",
                    "title": "Indice nacional de precos ao consumidor-amplo (IPCA)",
                    "notes": "Variacao mensal do IPCA.",
                    "extras": [{"key": "periodicidade", "value": "MENSAL"}],
                    "resources": [
                        {
                            "url": "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados?formato=json"
                        }
                    ],
                },
                {"name": "sem-serie", "title": "Relatorio", "resources": [{"url": "x.pdf"}]},
            ],
        },
    }
    respostas.tabela["https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/"] = [
        {"data": "01/07/2026", "valor": "0.26"},
        {"data": "01/08/2026", "valor": "0.44"},
    ]
    bc = BancoCentral()

    [ipca] = bc.procurar("IPCA")
    assert ipca.codigo == "433" and ipca.periodicidade == "MENSAL"
    assert ipca.recortes == ["Brasil"]

    valores = bc.valores(SimpleNamespace(codigo="433", periodicidade="MENSAL"), ultimos=2)
    assert respostas.pedidos[-1][0].endswith("bcdata.sgs.433/dados/ultimos/2")
    assert [(v.periodo, v.valor) for v in valores] == [
        ("2026-08", Decimal("0.44")),
        ("2026-07", Decimal("0.26")),
    ]
    # O SGS nao recorta por estado.
    assert bc.valores(SimpleNamespace(codigo="433", periodicidade=""), local="SP") == []
    assert bc.codigo_do_link("https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados") == "11"


def test_oms_le_so_ambos_os_sexos_do_brasil(respostas):
    respostas.tabela[f"{OMS.BASE}/Indicator"] = {
        "value": [{"IndicatorCode": "NCD_BMI_30A", "IndicatorName": "Prevalence of obesity"}]
    }
    respostas.tabela[f"{OMS.BASE}/NCD_BMI_30A"] = {
        "value": [
            {"SpatialDim": "BRA", "TimeDim": 2016, "Dim1": "SEX_MLE", "NumericValue": 18.5},
            {"SpatialDim": "BRA", "TimeDim": 2016, "Dim1": "SEX_BTSX", "NumericValue": 22.1},
            {"SpatialDim": "BRA", "TimeDim": 2015, "Dim1": "SEX_BTSX", "NumericValue": 21.4},
        ]
    }
    oms = OMS()

    [obesidade] = oms.procurar("obesity")
    assert respostas.pedidos[-1][1] == {"$filter": "contains(IndicatorName,'obesity')"}
    valores = oms.valores(SimpleNamespace(codigo=obesidade.codigo, periodicidade=""))
    assert [(v.periodo, v.valor) for v in valores] == [("2016", Decimal("22.1"))]
    assert respostas.pedidos[-1][1] == {"$filter": "SpatialDim eq 'BRA'"}


def test_periodos_ordenam_como_texto_e_aparecem_legiveis():
    assert periodo_ordenavel("201908", "mensal") == "2019-08"
    assert periodo_ordenavel("201902", "trimestral") == "2019-T2"
    assert periodo_ordenavel("15/08/2024", "diaria") == "2024-08-15"
    assert periodo_ordenavel("01/08/2024", "") == "2024-08"
    assert periodo_ordenavel("01/01/2024", "anual") == "2024"
    assert periodo_legivel("2024-08") == "ago/2024"
    assert periodo_legivel("2024-T1") == "1º tri/2024"
    assert periodo_legivel("2024-08-15") == "15/08/2024"
    assert periodo_legivel("2019") == "2019"


@pytest.mark.django_db
def test_procurar_grava_sugeridas_e_valor_vem_do_adaptador(respostas, ambiente):  # noqa: F811
    from django.urls import reverse

    from apps.dados.catalogo import fato
    from apps.dados.models import Instituicao, Serie

    _, usuario, client = ambiente
    usuario.is_superuser = True
    usuario.save()
    ibge, _ = Instituicao.objects.get_or_create(
        sigla="IBGE", defaults={"nome": "IBGE", "adaptador": "ibge"}
    )
    ibge.adaptador = "ibge"
    ibge.save()
    respostas.tabela[f"{IBGE.BASE}/4160/metadados"] = METADADOS_4160
    respostas.tabela[f"{IBGE.BASE}/4160/periodos"] = VALORES_4160
    respostas.tabela[IBGE.BASE] = AGREGADOS

    client.post(
        reverse("dados:procurar", args=[ibge.pk], urlconf="core.urls_tenants"),
        {"termo": "plano de saude"},
    )
    serie = Serie.objects.get(instituicao=ibge, codigo="4160/1000")
    assert serie.situacao == Serie.Situacao.SUGERIDA and serie.origem == Serie.Origem.CATALOGO

    dado = fato(serie, "PR")
    assert dado["valor"] == "32,4" and dado["periodo"] == "2019" and dado["local"] == "Paraná"
    # Buscado agora: a proxima vez usa o gravado, sem rede.
    antes = len(respostas.pedidos)
    fato(serie, "PR")
    assert len(respostas.pedidos) == antes
