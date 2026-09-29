"""Oportunidades: o que o publico procura e o site ainda nao oferece.

* crescimento compara o mesmo mes do ano anterior: tema sazonal nao e
  novidade; Mann-Kendall separa tendencia de ruido;
* c-TF-IDF escolhe os termos que distinguem o grupo, e nao os que estao em
  todos;
* tema perto do negocio NAO e oportunidade (e pauta); grupo so de dores nao e
  evidencia de nada;
* o LLM so descreve, e a falta dele nao impede nada;
* as decisoes da tela: testar com artigo, virar semente, arquivar.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.radar.models import (
    ConfiguracaoDoRadar,
    GrupoDeDemanda,
    Oportunidade,
    SinalDeDemanda,
)
from tests.test_fila_e_concorrentes import DataForSEOFalsa, _config_com_fila
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import radar  # noqa: F401


def _serie(valores_2025, valores_2026):
    return [[2025, m, v] for m, v in enumerate(valores_2025, 1)] + [
        [2026, m, v] for m, v in enumerate(valores_2026, 1)
    ]


# ---------------------------------------------------------------------------
# Crescimento
# ---------------------------------------------------------------------------
def test_tema_sazonal_nao_e_crescimento_e_subida_real_e():
    from apps.radar.oportunidades import crescimento

    verao = [900, 800, 500, 300, 200, 150, 150, 200, 300, 500, 700, 900]
    # Mesmo desenho nos dois anos: o pico de dezembro nao e novidade.
    sazonal = crescimento([tuple(x) for x in _serie(verao, verao[:9])])
    assert sazonal["yoy"] == 0
    assert not sazonal["significativo"]

    subindo = crescimento([tuple(x) for x in _serie(verao, [int(v * 1.6) for v in verao[:9]])])
    assert subindo["yoy"] == pytest.approx(0.6)
    assert subindo["significativo"]

    assert crescimento([(2026, m, 100) for m in range(1, 10)]) == {}  # pouco historico


def test_mann_kendall():
    from apps.radar.oportunidades import mann_kendall

    assert mann_kendall(list(range(12))) > 1.645
    assert mann_kendall([5, 3, 5, 3, 5, 3, 5, 3]) == pytest.approx(0, abs=0.5)
    assert mann_kendall([1, 2]) == 0


def test_termos_distintivos_deixam_de_fora_o_que_esta_em_tudo():
    from apps.radar.oportunidades import termos_distintivos

    termos = termos_distintivos(
        {
            "a": ["clinica de clareamento dental", "clareamento dental preco"],
            "b": ["clinica de implante dentario", "implante dentario dor"],
        },
        quantos=3,
    )
    assert termos["a"][0] in {"clareamento dental", "clareamento", "dental"}
    assert "clinica" not in termos["a"][:1]
    assert not any("implante" in t for t in termos["a"])


# ---------------------------------------------------------------------------
# Nota
# ---------------------------------------------------------------------------
def _sinal(texto, fonte=SinalDeDemanda.Fonte.PERGUNTA_RELACIONADA, **metricas):
    extra = {"metricas": {"2076": metricas}} if metricas else {}
    return SinalDeDemanda.objects.create(
        texto=texto, fonte=fonte, volume=metricas.get("volume"), extra=extra
    )


@pytest.mark.django_db
def test_so_vira_oportunidade_o_que_esta_longe_do_negocio(radar):  # noqa: F811
    from apps.radar.agrupamento import agrupar
    from apps.radar.oportunidades import atualizar_oportunidades

    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "gestao de carteira"
    config.dores = "equipe desmotivada"
    config.save()

    meses = _serie([100] * 12, [180] * 9)
    agrupar(
        [
            _sinal("equipe desmotivada", volume=300, cpc=4.0, competicao=80, meses=meses),
            _sinal("gestao de carteira clientes", volume=500, cpc=1.0, competicao=20, meses=[]),
            _sinal("insonia cronica", fonte=SinalDeDemanda.Fonte.DOR),
        ]
    )

    assert atualizar_oportunidades() == 1
    op = Oportunidade.objects.get()
    assert op.grupo.rotulo == "equipe desmotivada"
    assert op.cpc == 4.0
    assert op.crescimento["significativo"]
    assert op.parcelas["novidade"] == 1.0
    assert op.parcelas["publico"] == 1.0
    assert op.parcelas["comercial"] == pytest.approx(0.7 + 0.3 * 0.8)
    assert 0 < op.nota <= 100
    assert op.termos


@pytest.mark.django_db
def test_decisao_da_pessoa_sobrevive_a_reavaliacao(radar):  # noqa: F811
    from apps.radar.agrupamento import agrupar
    from apps.radar.oportunidades import atualizar_oportunidades

    agrupar([_sinal("equipe desmotivada", volume=300)])
    atualizar_oportunidades()
    op = Oportunidade.objects.get()
    op.situacao = Oportunidade.Situacao.ARQUIVADA
    op.save()

    atualizar_oportunidades()

    assert Oportunidade.objects.get().situacao == Oportunidade.Situacao.ARQUIVADA


# ---------------------------------------------------------------------------
# Descricao pelo modelo
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_descricao_pelo_modelo_e_adiada_sem_placa(radar, monkeypatch):  # noqa: F811
    from apps.content import inference
    from apps.ops.orchestrator import PassoAdiado
    from apps.radar.agrupamento import agrupar
    from apps.radar.oportunidades import atualizar_oportunidades, descrever_pendentes

    agrupar([_sinal("equipe desmotivada", volume=300)])
    atualizar_oportunidades()
    pedidos = []

    def sem_placa(**kwargs):
        raise PassoAdiado("placa ocupada", tentar_em_segundos=120)

    monkeypatch.setattr(inference, "executar_prompt", sem_placa)
    assert descrever_pendentes(nota_minima=0) == 0
    assert Oportunidade.objects.get().descricao_em is None

    class Resultado:
        texto = json.dumps(
            {
                "problema": "Gestores relatam equipe desmotivada.",
                "servico": "Diagnostico de clima.",
                "perguntas": ["Quem paga?", "Com que frequencia?"],
            }
        )

    def com_placa(**kwargs):
        pedidos.append(kwargs)
        return Resultado()

    monkeypatch.setattr(inference, "executar_prompt", com_placa)
    assert descrever_pendentes(nota_minima=0) == 1
    op = Oportunidade.objects.get()
    assert op.descricao["perguntas"] == ["Quem paga?", "Com que frequencia?"]
    assert pedidos[0]["key"] == "opportunity_brief"
    # Os sinais vao delimitados: sao texto de terceiros.
    assert "equipe desmotivada" in pedidos[0]["variaveis"]["sinais"]


# ---------------------------------------------------------------------------
# Rodada e tela
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rodada_busca_as_dores_tambem(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import executar_rodada

    _config_com_fila(sementes="bdi", dores="obra atrasada\nmedicao errada")
    api = DataForSEOFalsa(monkeypatch)

    rodada = executar_rodada()

    buscadas = {c["keyword"] for c in api.posts[0][1]}
    assert buscadas == {"bdi", "obra atrasada", "medicao errada"}
    assert rodada.resumo["dores"] == ["obra atrasada", "medicao errada"]
    assert SinalDeDemanda.objects.filter(fonte=SinalDeDemanda.Fonte.DOR).count() == 2


@pytest.mark.django_db
def test_tela_e_decisoes(ambiente, settings):  # noqa: F811
    from apps.content.models import Topic
    from apps.knowledge.embeddings import get_embedding_client
    from apps.radar.agrupamento import agrupar
    from apps.radar.oportunidades import atualizar_oportunidades

    settings.EMBEDDING_CLIENT = "tests.test_radar.EmbeddingPorPalavras"
    get_embedding_client.cache_clear()
    _, _, client = ambiente
    url = reverse("radar:oportunidades", urlconf="core.urls_tenants")

    # As dores ficam no Radar, junto das sementes.
    config = ConfiguracaoDoRadar.carregar()
    config.dores = "equipe desmotivada\nturnover alto"
    config.save()

    agrupar([_sinal("equipe desmotivada", volume=300), _sinal("turnover alto", volume=90)])
    atualizar_oportunidades()
    pagina = client.get(url).content.decode()
    assert "equipe desmotivada" in pagina and "Testar com um artigo" in pagina

    primeira, segunda = Oportunidade.objects.order_by("-nota")
    decidir = "radar:decidir_oportunidade"
    client.post(
        reverse(decidir, args=[primeira.pk], urlconf="core.urls_tenants"), {"decisao": "testar"}
    )
    client.post(
        reverse(decidir, args=[segunda.pk], urlconf="core.urls_tenants"), {"decisao": "semente"}
    )

    primeira.refresh_from_db()
    segunda.refresh_from_db()
    assert primeira.situacao == Oportunidade.Situacao.EM_TESTE
    assert Topic.objects.filter(title=primeira.grupo.rotulo).exists()
    assert segunda.situacao == Oportunidade.Situacao.ACOMPANHANDO
    assert segunda.grupo.rotulo in ConfiguracaoDoRadar.carregar().lista_de_sementes
    assert GrupoDeDemanda.objects.get(pk=primeira.grupo_id).situacao == "pauta"
    get_embedding_client.cache_clear()
