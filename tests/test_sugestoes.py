"""Sementes sugeridas e expansao em profundidade.

* KeyBERT + MMR tira os temas centrais da pagina do site, sem LLM, e sem
  repetir o que ja e semente;
* tema forte do radar, do negocio e longe de toda semente vira sugestao;
* o modelo sugere sementes e dores; sem placa, adia;
* nada entra sozinho: aceitar poe na configuracao, recusar nao volta;
* a rodada busca tambem os temas fortes (o segundo nivel da cauda longa);
* o contexto do site (pagina inicial) e guardado, com paginacao.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from django.urls import reverse

from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda, SementeSugerida
from tests.test_fila_e_concorrentes import DataForSEOFalsa, _config_com_fila
from tests.test_interface import ambiente  # noqa: F401
from tests.test_radar import EmbeddingPorPalavras, radar  # noqa: F401

PAGINA = (
    "Consultoria em gestao de carteira de clientes para empresas B2B. "
    "Aumente a retencao de clientes e reduza o churn com gestao de carteira. "
    "Diagnostico comercial, curva ABC de clientes e previsibilidade de receita. "
    "Fale conosco pelo WhatsApp."
)


def test_candidatos_sem_palavra_vazia_nas_pontas():
    from apps.radar.sugestoes import candidatos

    lista = candidatos(PAGINA)
    assert "gestao de carteira" in lista
    assert not any(c.startswith(("de ", "e ", "com ")) or c.endswith((" de", " e")) for c in lista)
    assert "whatsapp" not in lista


def test_mmr_troca_repeticao_por_diversidade():
    from apps.radar.sugestoes import mmr

    doc = np.array([1.0, 1.0, 0.0])
    cands = np.array([[1.0, 0.9, 0.0], [1.0, 0.9, 0.0], [0.0, 1.0, 0.1]])
    escolhidos = mmr(doc, cands, quantos=2, lambda_=0.5)
    assert escolhidos[0] in (0, 1)
    assert escolhidos[1] == 2  # o repetido perde para o diferente


@pytest.mark.django_db
def test_sugestoes_pela_pagina_sem_repetir_semente(radar):  # noqa: F811
    from apps.integrations.models import Site
    from apps.radar.sugestoes import sugerir_pela_pagina

    Site.objects.create(
        name="S", slug="s", base_url="https://s.exemplo.org", home_content_text=PAGINA
    )
    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "curva abc de clientes"
    config.save()

    assert sugerir_pela_pagina(quantas=6) > 0
    textos = set(SementeSugerida.objects.values_list("chave", flat=True))
    assert "curva abc de clientes" not in textos
    assert all(s.origem == "pagina" for s in SementeSugerida.objects.all())
    assert sugerir_pela_pagina(quantas=6) == 0  # a mesma sugestao nao entra duas vezes


@pytest.mark.django_db
def test_tema_forte_longe_das_sementes_vira_sugestao(radar):  # noqa: F811
    from apps.radar.sugestoes import sugerir_pelo_radar

    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "gestao de carteira"
    config.save()
    emb = EmbeddingPorPalavras()
    for rotulo in ("previsibilidade de receita", "gestao de carteira ativa"):
        GrupoDeDemanda.objects.create(
            rotulo=rotulo,
            centroide=emb.embed_query(rotulo),
            nota=70,
            volume_total=500,
            parcelas={"aderencia": 0.8},
        )

    assert sugerir_pelo_radar() == 1
    sugestao = SementeSugerida.objects.get()
    assert sugestao.texto == "previsibilidade de receita"
    assert sugestao.origem == "radar"
    assert sugestao.evidencia["volume"] == 500


@pytest.mark.django_db
def test_modelo_sugere_sementes_e_dores(radar, monkeypatch):  # noqa: F811
    from apps.content import inference
    from apps.editorial.models import PerfilDoNegocio
    from apps.integrations.models import Site
    from apps.ops.orchestrator import PassoAdiado
    from apps.radar.sugestoes import sugerir_pelo_modelo

    Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    PerfilDoNegocio.objects.update_or_create(pk=1, defaults={"tema": "clinica"})
    pedidos = []

    class Resultado:
        texto = json.dumps(
            {"sementes": ["clareamento dental"], "dores": ["dente amarelado", "sensibilidade"]}
        )

    def executar(**kwargs):
        pedidos.append(kwargs)
        return Resultado()

    monkeypatch.setattr(inference, "executar_prompt", executar)
    assert sugerir_pelo_modelo() == 3
    assert pedidos[0]["key"] == "seed_suggestion"
    assert set(SementeSugerida.objects.values_list("tipo", flat=True)) == {"semente", "dor"}

    def ocupado(**kwargs):
        raise PassoAdiado("placa ocupada")

    monkeypatch.setattr(inference, "executar_prompt", ocupado)
    with pytest.raises(PassoAdiado):
        sugerir_pelo_modelo()


@pytest.mark.django_db
def test_aceitar_poe_na_configuracao_e_recusar_nao_volta(ambiente):  # noqa: F811
    _, _, client = ambiente
    semente = SementeSugerida.objects.create(
        texto="clareamento dental", chave="clareamento dental", tipo="semente", origem="pagina"
    )
    dor = SementeSugerida.objects.create(
        texto="dente amarelado", chave="dente amarelado", tipo="dor", origem="modelo"
    )
    outra = SementeSugerida.objects.create(
        texto="odontologia", chave="odontologia", tipo="semente", origem="pagina"
    )
    # Ficam na Configuracao, ao lado do campo de sementes.
    url = reverse("radar:configuracao", urlconf="core.urls_tenants")
    assert "clareamento dental" in client.get(url).content.decode()

    decidir = "radar:decidir_semente_sugerida"
    for item, decisao in ((semente, "aceitar"), (dor, "aceitar"), (outra, "recusar")):
        resposta = client.post(
            reverse(decidir, args=[item.pk], urlconf="core.urls_tenants"),
            {"decisao": decisao, "voltar": "https://fora.com/"},
        )
        assert resposta.url == url + "#sementes-sugeridas"

    config = ConfiguracaoDoRadar.carregar()
    assert config.lista_de_sementes == ["clareamento dental"]
    assert config.lista_de_dores == ["dente amarelado"]
    outra.refresh_from_db()
    assert outra.situacao == "recusada"


@pytest.mark.django_db
def test_rodada_busca_tambem_os_temas_fortes(radar, monkeypatch):  # noqa: F811
    from apps.radar.coleta import executar_rodada

    _config_com_fila(sementes="bdi")
    from apps.radar.models import SinalDeDemanda

    forte = GrupoDeDemanda.objects.create(rotulo="Como calcular o BDI de obra publica?", nota=80)
    SinalDeDemanda.objects.create(texto=forte.rotulo, fonte="paa", grupo=forte)
    so_semente = GrupoDeDemanda.objects.create(rotulo="orcamento", nota=85)
    SinalDeDemanda.objects.create(texto="orcamento", fonte="semente", grupo=so_semente)
    GrupoDeDemanda.objects.create(rotulo="tema descartado", nota=90, situacao="descartado")
    api = DataForSEOFalsa(monkeypatch)

    rodada = executar_rodada()

    buscadas = {c["keyword"] for c in api.posts[0][1]}
    assert buscadas == {"bdi", "Como calcular o BDI de obra publica?"}
    assert rodada.resumo["expansoes"] == ["Como calcular o BDI de obra publica?"]


@pytest.mark.django_db
def test_contexto_do_site_guarda_a_pagina_e_pagina_o_cursor(radar, monkeypatch):  # noqa: F811
    from apps.integrations import client
    from apps.integrations.models import Site, SitePost
    from apps.integrations.tasks import atualizar_contexto

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    paginas = {
        "": {
            "site_title": "S",
            "home_content_text": PAGINA,
            "published_posts": [{"remote_id": 1, "title": "Churn", "url": "https://s/1"}],
            "next_cursor": "p2",
        },
        "p2": {
            "site_title": "S",
            "published_posts": [{"remote_id": 2, "title": "LTV", "url": "https://s/2"}],
            "next_cursor": None,
        },
    }

    def seo_context(self, *, limite=100, cursor="", publicados_apos=""):
        return paginas[cursor]

    monkeypatch.setattr(client.SiteClient, "__init__", lambda self, site: None)
    monkeypatch.setattr(client.SiteClient, "seo_context", seo_context)

    assert atualizar_contexto(site) == 2
    site.refresh_from_db()
    assert site.home_content_text.startswith("Consultoria")
    assert site.context_synced_at is not None
    assert SitePost.objects.count() == 2
