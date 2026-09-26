"""Radar de pautas: coleta, custo, agrupamento e pautas sugeridas.

O que estes testes guardam:

* toda chamada externa vai para o livro-caixa, com o custo que o PROVEDOR
  informou — inclusive as que falham;
* o teto e conferido ANTES da chamada, e o maximo da instalacao limita o do
  tenant;
* o buscador gratuito cai para o pago quando falha, e a comparacao por
  amostragem registra quanto um cobre do outro;
* uma rodada vira pautas SUGERIDAS com a evidencia junto, e o teto pode
  para-la no meio sem perder o que ja foi colhido;
* a busca manual espera decisao, e o custo fica registrado seja qual for.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import httpx
import numpy as np
import pytest
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.knowledge.embeddings import EmbeddingClient
from apps.radar.models import (
    BuscaManual,
    ChamadaExterna,
    ComparacaoDeBusca,
    ConfiguracaoDoRadar,
    ContasExternas,
    GrupoDeDemanda,
    RodadaDoRadar,
    SinalDeDemanda,
)
from tests.test_interface import ambiente  # noqa: F401

DIM = 1024


class EmbeddingPorPalavras(EmbeddingClient):
    """Frases com palavras em comum ficam perto: basta para testar grupos."""

    model_name = "por-palavras"
    dimensions = DIM

    def _vetor(self, texto):
        vetor = np.zeros(DIM, dtype=np.float32)
        for palavra in texto.lower().replace("?", "").split():
            if len(palavra) > 2:
                vetor[int(hashlib.md5(palavra.encode()).hexdigest(), 16) % DIM] += 1  # noqa: S324
        norma = np.linalg.norm(vetor)
        return (vetor / norma if norma else vetor).tolist()

    def embed_query(self, texto):
        return self._vetor(texto)

    def embed_passage(self, textos):
        return [self._vetor(t) for t in textos]

    def contar_tokens(self, texto):
        return max(1, len(texto) // 4)


@pytest.fixture
def radar(tenant_factory, settings):
    settings.EMBEDDING_CLIENT = "tests.test_radar.EmbeddingPorPalavras"
    settings.RADAR_DISTANCIA_DO_GRUPO = 0.5
    settings.RADAR_NOTA_MINIMA = 0
    settings.RADAR_TETO_MAXIMO_USD = 20
    settings.SEARXNG_URL = ""
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    tenant = tenant_factory("radar")
    with schema_context(tenant.schema_name):
        yield tenant
    get_embedding_client.cache_clear()


def _com_dataforseo():
    from apps.inference.security import cifrar

    contas = ContasExternas.carregar()
    contas.dataforseo_login = "api@exemplo.com"
    contas.dataforseo_senha_ciphertext = cifrar("senha")
    contas.save()
    return contas


RESPOSTA_SERP = {
    "status_code": 20000,
    "cost": 0.002,
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {
                    "items": [
                        {"type": "organic", "url": "https://a.com.br/bdi", "title": "BDI"},
                        {"type": "organic", "url": "https://www.b.com/x/", "title": "Orcamento"},
                        {
                            "type": "people_also_ask",
                            "items": [
                                {
                                    "type": "people_also_ask_element",
                                    "title": "Como calcular o BDI?",
                                },
                                {
                                    "type": "people_also_ask_element",
                                    "title": "Qual BDI usar em obra publica?",
                                },
                            ],
                        },
                        {"type": "related_searches", "items": ["bdi tcu", "planilha bdi excel"]},
                    ]
                }
            ],
        }
    ],
}

RESPOSTA_VOLUME = {
    "status_code": 20000,
    "cost": 0.09,
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {"keyword": "como calcular o bdi?", "search_volume": 1900},
                {"keyword": "bdi tcu", "search_volume": 480},
            ],
        }
    ],
}


def _responder_post(monkeypatch, respostas: dict, chamadas: list | None = None):
    def post(url, json=None, auth=None, timeout=None):
        if chamadas is not None:
            chamadas.append(url)
        for trecho, corpo in respostas.items():
            if trecho in url:
                return httpx.Response(200, json=corpo, request=httpx.Request("POST", url))
        raise AssertionError(f"POST inesperado: {url}")

    monkeypatch.setattr(httpx, "post", post)


# ---------------------------------------------------------------------------
# DataForSEO
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_serp_da_dataforseo_vira_perguntas_relacionadas_e_custo(radar, monkeypatch):
    from apps.radar.provedores import buscar_dataforseo

    _responder_post(monkeypatch, {"/serp/": RESPOSTA_SERP})
    resultado = buscar_dataforseo(
        "bdi obra",
        config=ConfiguracaoDoRadar.carregar(),
        contas=_com_dataforseo(),
        finalidade="radar",
    )

    assert resultado.perguntas == ["Como calcular o BDI?", "Qual BDI usar em obra publica?"]
    assert resultado.relacionadas == ["bdi tcu", "planilha bdi excel"]
    assert len(resultado.resultados) == 2
    chamada = ChamadaExterna.objects.get()
    assert chamada.custo_usd == Decimal("0.002")
    assert chamada.provedor == "dataforseo"


@pytest.mark.django_db
def test_erro_no_corpo_da_dataforseo_e_falha_e_fica_registrado(radar, monkeypatch):
    """A DataForSEO responde HTTP 200 com o erro no corpo. Olhar so o status
    faria um saldo zerado passar por resultado vazio."""
    from apps.radar.provedores import ProvedorIndisponivel, buscar_dataforseo

    _responder_post(
        monkeypatch,
        {"/serp/": {"status_code": 40200, "status_message": "Payment Required.", "tasks": []}},
    )

    with pytest.raises(ProvedorIndisponivel, match="Payment"):
        buscar_dataforseo(
            "x", config=ConfiguracaoDoRadar.carregar(), contas=_com_dataforseo(), finalidade="radar"
        )

    chamada = ChamadaExterna.objects.get()
    assert not chamada.sucesso
    assert "Payment" in chamada.erro


@pytest.mark.django_db
def test_sem_conta_nao_chama_ninguem(radar, monkeypatch):
    from apps.radar.provedores import ProvedorIndisponivel, buscar_dataforseo

    _responder_post(monkeypatch, {})
    with pytest.raises(ProvedorIndisponivel, match="nao esta configurada"):
        buscar_dataforseo(
            "x",
            config=ConfiguracaoDoRadar.carregar(),
            contas=ContasExternas.carregar(),
            finalidade="radar",
        )


@pytest.mark.django_db
def test_volume_numa_chamada_so_para_todas_as_palavras(radar, monkeypatch):
    from apps.radar.provedores import volume_dataforseo

    chamadas = []
    _responder_post(monkeypatch, {"/search_volume/": RESPOSTA_VOLUME}, chamadas)

    volumes = volume_dataforseo(
        ["Como calcular o BDI?", "bdi tcu", "bdi tcu"],
        config=ConfiguracaoDoRadar.carregar(),
        contas=_com_dataforseo(),
        finalidade="radar",
    )

    assert volumes == {"como calcular o bdi?": 1900, "bdi tcu": 480}
    assert len(chamadas) == 1


# ---------------------------------------------------------------------------
# Teto
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_teto_e_conferido_antes_da_chamada(radar, monkeypatch):
    from apps.radar.custos import TetoAtingido
    from apps.radar.provedores import buscar_dataforseo

    chamadas = []
    _responder_post(monkeypatch, {"/serp/": RESPOSTA_SERP}, chamadas)
    config = ConfiguracaoDoRadar.carregar()
    config.teto_mensal_usd = Decimal("0.001")
    config.save()

    with pytest.raises(TetoAtingido):
        buscar_dataforseo("x", config=config, contas=_com_dataforseo(), finalidade="radar")
    assert chamadas == []


@pytest.mark.django_db
def test_o_maximo_da_instalacao_limita_o_teto_do_tenant(radar, settings):
    from apps.radar.custos import teto_efetivo
    from apps.radar.forms import ConfiguracaoForm

    settings.RADAR_TETO_MAXIMO_USD = 5
    config = ConfiguracaoDoRadar.carregar()
    config.teto_mensal_usd = Decimal("50")

    assert teto_efetivo(config) == Decimal("5")
    form = ConfiguracaoForm(
        {
            "intensidade": "minimo",
            "buscador": "searxng",
            "taxa_de_comparacao": 0,
            "codigo_de_local": 2076,
            "codigo_de_idioma": "pt",
            "teto_mensal_usd": "50",
        },
        instance=config,
    )
    assert not form.is_valid()
    assert "teto_mensal_usd" in form.errors


# ---------------------------------------------------------------------------
# Buscador gratuito, recurso ao pago e comparacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_gratuito_fora_do_ar_cai_para_o_pago(radar, monkeypatch):
    from apps.radar.provedores import buscar

    _com_dataforseo()
    contas = ContasExternas.carregar()
    contas.searxng_url = "https://searx.exemplo.com"
    contas.save()

    def get(url, params=None, timeout=None):
        raise httpx.ConnectError("recusado")

    monkeypatch.setattr(httpx, "get", get)
    _responder_post(monkeypatch, {"/serp/": RESPOSTA_SERP})

    resultado = buscar("bdi", finalidade="radar")

    assert resultado.provedor == "dataforseo"
    provedores = set(ChamadaExterna.objects.values_list("provedor", "sucesso"))
    assert ("searxng", False) in provedores
    assert ("dataforseo", True) in provedores


@pytest.mark.django_db
def test_comparacao_registra_quanto_o_gratuito_cobre_do_pago(radar, monkeypatch):
    from apps.radar.provedores import buscar

    _com_dataforseo()
    contas = ContasExternas.carregar()
    contas.searxng_url = "https://searx.exemplo.com"
    contas.save()
    config = ConfiguracaoDoRadar.carregar()
    config.taxa_de_comparacao = 100
    config.save()

    def get(url, params=None, timeout=None):
        corpo = {
            "results": [
                {"url": "https://a.com.br/bdi/", "title": "BDI"},
                {"url": "https://b.com/outra-pagina", "title": "Outra?"},
                {"url": "https://c.org/", "title": "C"},
            ],
            "suggestions": ["bdi planilha"],
        }
        return httpx.Response(200, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    _responder_post(monkeypatch, {"/serp/": RESPOSTA_SERP})

    resultado = buscar("bdi", finalidade="radar")

    assert resultado.provedor == "searxng"
    # Pergunta do pago entra na lista, ja que foi paga.
    assert "Como calcular o BDI?" in resultado.perguntas
    comparacao = ComparacaoDeBusca.objects.get()
    assert comparacao.sobreposicao_urls == 0.5  # a.com.br/bdi sim, b.com/x nao
    assert comparacao.sobreposicao_dominios == 1.0  # a.com.br e b.com nos dois
    assert comparacao.so_no_gratuito == ["c.org"]


# ---------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------
def _fingir_provedores(monkeypatch, *, perguntas, volumes=None):
    from apps.radar import coleta
    from apps.radar.provedores import ResultadoDeBusca

    def buscar(consulta, *, finalidade):
        from apps.radar import custos

        custos.registrar(
            provedor="dataforseo",
            endpoint="serp",
            finalidade=finalidade,
            consulta=consulta,
            custo="0.002",
        )
        return ResultadoDeBusca(provedor="dataforseo", perguntas=list(perguntas))

    def volume(palavras, **kwargs):
        return {p.lower(): v for p, v in (volumes or {}).items()}

    monkeypatch.setattr(coleta, "buscar", buscar)
    monkeypatch.setattr(coleta, "volume_dataforseo", volume)


@pytest.mark.django_db
def test_rodada_vira_pauta_sugerida_com_evidencia(radar, monkeypatch):
    from apps.content.models import Topic
    from apps.radar.coleta import executar_rodada

    _com_dataforseo()
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.sementes = "calcular bdi obra"
    config.save()
    _fingir_provedores(
        monkeypatch,
        perguntas=["Como calcular o BDI da obra?", "Qual o BDI de obra publica?"],
        volumes={"Como calcular o BDI da obra?": 1900},
    )

    rodada = executar_rodada()

    assert rodada.situacao == RodadaDoRadar.Situacao.CONCLUIDA
    assert rodada.custo_usd == Decimal("0.002")
    pauta = Topic.objects.get(origin=Topic.Origin.RADAR)
    assert pauta.status == Topic.Status.SUGGESTED
    assert pauta.title == "Como calcular o BDI da obra?"  # o de maior volume
    assert {s["texto"] for s in pauta.evidence["sinais"]} >= {"Como calcular o BDI da obra?"}
    assert pauta.demand_score > 0
    assert GrupoDeDemanda.objects.filter(pauta=pauta).exists()


@pytest.mark.django_db
def test_sinal_repetido_nao_entra_duas_vezes(radar, monkeypatch):
    from apps.radar.coleta import executar_rodada

    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.sementes = "bdi"
    config.save()
    _fingir_provedores(monkeypatch, perguntas=["Como calcular o BDI?"])

    executar_rodada()
    executar_rodada()

    assert SinalDeDemanda.objects.filter(texto__iexact="Como calcular o BDI?").count() == 1


@pytest.mark.django_db
def test_teto_para_a_rodada_sem_perder_o_colhido(radar, monkeypatch):
    from apps.radar import coleta
    from apps.radar.coleta import executar_rodada
    from apps.radar.custos import TetoAtingido
    from apps.radar.provedores import ResultadoDeBusca

    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.sementes = "primeira\nsegunda"
    config.save()
    chamadas = []

    def buscar(consulta, *, finalidade):
        chamadas.append(consulta)
        if len(chamadas) > 1:
            raise TetoAtingido("teto")
        return ResultadoDeBusca(provedor="dataforseo", perguntas=["Pergunta colhida antes?"])

    monkeypatch.setattr(coleta, "buscar", buscar)

    rodada = executar_rodada()

    assert rodada.situacao == RodadaDoRadar.Situacao.PARADA_NO_TETO
    assert SinalDeDemanda.objects.filter(texto="Pergunta colhida antes?").exists()


@pytest.mark.django_db
def test_perguntas_dos_visitantes_entram_como_sinal(radar, monkeypatch):
    from apps.content.models import Question
    from apps.integrations.models import Site
    from apps.radar.coleta import executar_rodada

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    Question.objects.create(
        site=site,
        remote_id="1",
        question_text="Posso orcar reforma pequena na planilha?",
        submitted_at=timezone.now(),
        retention_until=timezone.now() + timezone.timedelta(days=30),
    )
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.usar_serp = False
    config.save()

    executar_rodada()

    assert SinalDeDemanda.objects.filter(fonte=SinalDeDemanda.Fonte.PERGUNTA_DO_SITE).exists()


def test_intensidade_decide_se_a_rodada_e_devida():
    from apps.radar.coleta import rodada_devida

    agora = timezone.now()
    config = ConfiguracaoDoRadar(intensidade="desligado")
    assert not rodada_devida(config, agora)

    config.intensidade = "normal"  # 2 por semana: a cada 3,5 dias
    config.ultima_rodada_em = agora - timezone.timedelta(days=3)
    assert not rodada_devida(config, agora)
    config.ultima_rodada_em = agora - timezone.timedelta(days=4)
    assert rodada_devida(config, agora)


# ---------------------------------------------------------------------------
# Busca manual
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_busca_manual_espera_decisao_e_guardar_agrupa(radar, monkeypatch):
    from apps.radar.coleta import busca_manual, decidir_busca

    _fingir_provedores(monkeypatch, perguntas=["Quanto custa higienizar tapete?"])

    busca = busca_manual("higienizacao de tapete")

    assert busca.sinais.count() == 2
    assert set(busca.sinais.values_list("situacao", flat=True)) == {"pendente"}
    assert ChamadaExterna.objects.filter(finalidade="manual").exists()

    decidir_busca(busca, guardar=True)
    busca.refresh_from_db()
    assert busca.decisao == BuscaManual.Decisao.GUARDADA
    assert all(s.grupo_id for s in busca.sinais.all())


@pytest.mark.django_db
def test_descartar_mantem_o_custo(radar, monkeypatch):
    from apps.radar.coleta import busca_manual, decidir_busca

    _fingir_provedores(monkeypatch, perguntas=["Algo?"])
    busca = busca_manual("tema qualquer")

    decidir_busca(busca, guardar=False)

    assert set(busca.sinais.values_list("situacao", flat=True)) == {"descartado"}
    assert ChamadaExterna.objects.filter(finalidade="manual").count() == 1


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_tela_do_radar_e_contas_cifradas(ambiente, settings):  # noqa: F811
    settings.EMBEDDING_CLIENT = "tests.test_radar.EmbeddingPorPalavras"
    _, _, client = ambiente
    url = reverse("radar:radar", urlconf="core.urls_tenants")

    assert client.get(url).status_code == 200

    client.post(
        reverse("radar:salvar_contas", urlconf="core.urls_tenants"),
        {"dataforseo_login": "api@exemplo.com", "dataforseo_senha": "segredo-da-api"},
    )
    contas = ContasExternas.carregar()
    assert contas.tem_dataforseo
    assert b"segredo-da-api" not in bytes(contas.dataforseo_senha_ciphertext)
    assert "segredo-da-api" not in client.get(url).content.decode()

    # Campo vazio mantem a senha.
    client.post(
        reverse("radar:salvar_contas", urlconf="core.urls_tenants"),
        {"dataforseo_login": "api@exemplo.com", "dataforseo_senha": ""},
    )
    assert ContasExternas.carregar().tem_dataforseo


@pytest.mark.django_db
def test_tick_so_roda_onde_esta_devido(radar, monkeypatch):
    from apps.radar import coleta
    from apps.radar.tasks import _rodar_se_devido

    rodadas = []
    monkeypatch.setattr(coleta, "executar_rodada", lambda **k: rodadas.append(1))

    assert _rodar_se_devido() == 0  # desligado
    config = ConfiguracaoDoRadar.carregar()
    config.intensidade = "minimo"
    config.save()
    assert _rodar_se_devido() == 1
