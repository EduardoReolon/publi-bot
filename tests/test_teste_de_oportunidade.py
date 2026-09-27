"""Oportunidade testada com artigo: o resultado volta, e a validada entra no Negocio.

* sem artigo no ar, aguardando; conversao ou cliques na chamada, validada;
  60 dias sem busca nem chamada, sem tracao;
* "Validar" acrescenta a frente ao Negocio (so com a pessoa confirmando), e a
  frente passa a contar no "perto do tema do site".
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Article, Topic
from apps.radar.models import (
    ColetaDoConsole,
    GrupoDeDemanda,
    LinhaDoConsole,
    Oportunidade,
)
from tests.test_interface import ambiente  # noqa: F401


def _em_teste(rotulo="previsao de demanda com ia"):
    pauta = Topic.objects.create(title=rotulo)
    grupo = GrupoDeDemanda.objects.create(rotulo=rotulo, pauta=pauta)
    return Oportunidade.objects.create(grupo=grupo, situacao=Oportunidade.Situacao.EM_TESTE)


def _publicar(oportunidade, *, dias, remote_id="r1"):
    return Article.objects.create(
        title=oportunidade.grupo.rotulo,
        topic=oportunidade.grupo.pauta,
        remote_id=remote_id,
        status=Article.Status.PUBLISHED,
        published_at=timezone.now() - timedelta(days=dias),
        published_url=f"https://exemplo.com.br/{remote_id}/",
    )


def _site():
    from apps.integrations.models import Site

    return Site.objects.first() or Site.objects.create(
        name="S", slug="s", base_url="https://exemplo.com.br"
    )


@pytest.mark.django_db
def test_vereditos(ambiente):  # noqa: F811
    from apps.integrations.insights import gravar
    from apps.radar.teste import resultado

    oportunidade = _em_teste()
    assert resultado(oportunidade).veredito == "aguardando"

    artigo = _publicar(oportunidade, dias=10)
    assert resultado(oportunidade).veredito == "aguardando"

    artigo.published_at = timezone.now() - timedelta(days=70)
    artigo.save()
    coleta = ColetaDoConsole.objects.create(
        propriedade="x", inicio=timezone.localdate(), fim=timezone.localdate()
    )
    LinhaDoConsole.objects.create(
        coleta=coleta,
        consulta="previsao de demanda",
        pagina=artigo.published_url,
        impressoes=20,
        cliques=1,
        posicao=30,
    )
    sem = resultado(oportunidade)
    assert sem.veredito == "sem_tracao" and sem.impressoes == 20

    gravar(
        _site(),
        {
            "conversions": [
                {
                    "id": "c1",
                    "date": timezone.localdate().isoformat(),
                    "journey": [{"remote_id": "r1", "engaged_seconds": 90}],
                }
            ]
        },
    )
    validada = resultado(oportunidade)
    assert validada.veredito == "validada" and validada.conversoes == 1


@pytest.mark.django_db
def test_validar_acrescenta_a_frente_ao_negocio(ambiente):  # noqa: F811
    from apps.editorial.models import PerfilDoNegocio
    from apps.radar.agrupamento import _texto_do_negocio

    _, _, client = ambiente
    PerfilDoNegocio.objects.update_or_create(
        pk=1, defaults={"oferta": "Analise RFM dos clientes", "frentes": "Diagnostico de dados"}
    )
    oportunidade = _em_teste()
    _publicar(oportunidade, dias=40)

    pagina = client.get(
        reverse("radar:oportunidades", urlconf="core.urls_tenants") + "?ver=em_teste"
    ).content.decode()
    assert "Resultado do teste" in pagina and "Validar: virar frente do negocio" in pagina

    url = reverse("radar:decidir_oportunidade", args=[oportunidade.pk], urlconf="core.urls_tenants")
    client.post(url, {"decisao": "validar"})
    client.post(url, {"decisao": "validar"})  # de novo: nao repete a frente

    oportunidade.refresh_from_db()
    assert oportunidade.situacao == Oportunidade.Situacao.VALIDADA
    perfil = PerfilDoNegocio.carregar()
    assert perfil.frentes.splitlines() == ["Diagnostico de dados", "previsao de demanda com ia"]
    assert "previsao de demanda com ia" in _texto_do_negocio()
