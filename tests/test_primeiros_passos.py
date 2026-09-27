"""Pedidos para outra IA e a leitura da resposta colada.

* o leitor aceita os enfeites comuns (negrito, titulo, acento, cerca de codigo)
  e para no FIM;
* Negocio: a resposta do pedido 1 preenche o formulario sem salvar; a do
  pedido 2 vira Sementes sugeridas;
* pauta: a resposta da intencao muda titulo, tipo e orientacao;
* o texto do radar leva os concorrentes que a tela mostra, e pede a analise.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.resposta_ia import itens, ler_blocos
from tests.test_interface import ambiente  # noqa: F401

RESPOSTA_DO_NEGOCIO = """\
Claro! Segue:

```
**TEMA:** IA e dados para pequenas e medias empresas
## Público
Donos de PME sem equipe de dados
**OFERTA**: Descubro quem sao seus melhores clientes e quem esta sumindo
DORES:
- clientes pararam de comprar
1. nao sei quem compra mais
FRENTES:
• Diagnostico de dados
FIM
```
Espero ter ajudado!
"""


def test_leitor_aceita_enfeites_e_para_no_fim():
    blocos = ler_blocos(RESPOSTA_DO_NEGOCIO, ["TEMA", "PUBLICO", "OFERTA", "DORES", "FRENTES"])

    assert blocos["TEMA"] == "IA e dados para pequenas e medias empresas"
    assert blocos["PUBLICO"] == "Donos de PME sem equipe de dados"
    assert blocos["OFERTA"].startswith("Descubro quem sao")
    assert itens(blocos["DORES"]) == ["clientes pararam de comprar", "nao sei quem compra mais"]
    assert itens(blocos["FRENTES"]) == ["Diagnostico de dados"]


def test_frase_comum_nao_vira_rotulo():
    # "Tema" no meio de uma frase, sem dois pontos nem titulo, nao abre bloco.
    assert ler_blocos("Tema interessante este\nTEMA: certo", ["TEMA"]) == {"TEMA": "certo"}


@pytest.mark.django_db
def test_pedido_1_preenche_sem_salvar(ambiente):  # noqa: F811
    from apps.editorial.models import PerfilDoNegocio
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    config = ConfiguracaoDoRadar.carregar()
    config.dores = "clientes pararam de comprar"
    config.save()
    url = reverse("editorial:negocio", urlconf="core.urls_tenants")

    pagina = client.get(url).content.decode()
    assert "Primeiros passos" in pagina and "Copiar pedido 1" in pagina

    resposta = client.post(url, {"acao": "colar_negocio", "resposta": RESPOSTA_DO_NEGOCIO})
    html = resposta.content.decode()

    assert resposta.status_code == 200
    assert "Nada foi salvo ainda" in html
    assert "IA e dados para pequenas e medias empresas" in html
    # Dores somam as existentes, sem repetir.
    assert "clientes pararam de comprar\nnao sei quem compra mais" in html
    assert PerfilDoNegocio.carregar().tema == ""


@pytest.mark.django_db
def test_pedido_2_vira_sementes_sugeridas(ambiente):  # noqa: F811
    from apps.radar.models import SementeSugerida

    _, _, client = ambiente
    resposta = """\
PILARES:
- Conhecer seus clientes: matriz rfm; curva abc
SEMENTES:
- *clientes inativos
- segmentacao de clientes
- *clientes inativos
FIM
"""
    client.post(
        reverse("editorial:negocio", urlconf="core.urls_tenants"),
        {"acao": "colar_sementes", "resposta": resposta},
    )

    sugestoes = {s.texto: s for s in SementeSugerida.objects.all()}
    assert set(sugestoes) == {
        "clientes inativos",
        "segmentacao de clientes",
        "Conhecer seus clientes",
    }
    assert sugestoes["clientes inativos"].origem == "outra_ia"
    assert sugestoes["clientes inativos"].evidencia["prioritaria"] is True
    assert "matriz rfm" in sugestoes["Conhecer seus clientes"].evidencia["pilar"]


@pytest.mark.django_db
def test_intencao_atualiza_a_pauta(ambiente):  # noqa: F811
    from apps.content.models import Topic

    _, _, client = ambiente
    pauta = Topic.objects.create(
        title="Up sell", evidence={"sinais": [{"texto": "Up sell", "volume": 9900}]}
    )
    url = reverse("content:intencao_da_pauta", args=[pauta.pk], urlconf="core.urls_tenants")

    pagina = client.get(url).content.decode()
    assert "Up sell (9900 buscas/mes)" in pagina

    client.post(
        url,
        {
            "resposta": """\
**TITULO:** Upsell: para quais clientes oferecer mais (e quando)
INTENCAO: informacional
TIPO: guia
ORIENTACAO:
- entender o que e upsell
- evitar tom de vendedor
FIM"""
        },
    )

    pauta.refresh_from_db()
    assert pauta.title == "Upsell: para quais clientes oferecer mais (e quando)"
    assert pauta.content_type == "guia"
    assert pauta.briefing.startswith("Intencao de busca: informacional.")
    assert "- evitar tom de vendedor" in pauta.briefing

    pauta.status = Topic.Status.USED
    pauta.save()
    client.post(url, {"resposta": "TITULO: outro\nFIM"})
    pauta.refresh_from_db()
    assert pauta.title.startswith("Upsell:")


@pytest.mark.django_db
def test_texto_do_radar_leva_os_concorrentes_da_tela(ambiente):  # noqa: F811
    from apps.radar.models import ConcorrenteSugerido
    from apps.radar.resumo import texto_para_ia

    ConcorrenteSugerido.objects.create(
        dominio="salesforce.com",
        consultas={"ciclo de vendas": 2, "cross-sell": 4},
        exemplos=[
            {"url": "https://salesforce.com/x", "titulo": "5 dicas", "consulta": "cross-sell"}
        ],
    )
    ConcorrenteSugerido.objects.create(dominio="uma-vez.com", consultas={"a": 1})

    texto = texto_para_ia()

    assert "- salesforce.com | 2 | 2 | 5 dicas [busca: cross-sell]" in texto
    assert "uma-vez.com" not in texto
    assert "concorrente de NEGOCIO" in texto and "possivel PARCEIRO" in texto
