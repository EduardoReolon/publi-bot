"""Brechas primeiro enquanto o site nao tem autoridade; depois, so a nota."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.radar.dificuldade import Dificuldade, estrategia_efetiva, prioridade
from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda
from tests.test_interface import ambiente  # noqa: F401


def test_em_brechas_primeiro_a_dificuldade_pesa_na_ordem():
    brecha, dificil = Dificuldade(nota=10), Dificuldade(nota=80)

    assert prioridade(60, brecha, "brechas") > prioridade(80, dificil, "brechas")
    assert prioridade(80, dificil, "equilibrada") == 80
    # Sem medida fica um pouco abaixo da brecha, e nao some.
    assert prioridade(60, None, "brechas") == pytest.approx(54)


@pytest.mark.parametrize(
    ("escolhida", "veredito", "esperada"),
    [
        ("auto", "sem dados", "brechas"),
        ("auto", "links ajudariam", "brechas"),
        ("auto", "links nao sao prioridade", "equilibrada"),
        ("equilibrada", "sem dados", "equilibrada"),
        ("brechas", "links nao sao prioridade", "brechas"),
    ],
)
def test_a_automatica_segue_o_diagnostico_de_autoridade(escolhida, veredito, esperada):
    config = SimpleNamespace(estrategia_de_temas=escolhida)

    estrategia, motivo = estrategia_efetiva(config, SimpleNamespace(veredito=veredito))

    assert estrategia == esperada and motivo


@pytest.mark.django_db
def test_a_tela_explica_a_ordem_e_deixa_trocar(ambiente):  # noqa: F811
    _, _, client = ambiente
    GrupoDeDemanda.objects.create(rotulo="tema qualquer", nota=50)
    url = reverse("radar:radar", urlconf="core.urls_tenants")

    pagina = client.get(url).content.decode()
    assert "Ordem: brechas primeiro." in pagina and "Ver pela nota" in pagina
    assert "sem Search Console" in pagina

    config = ConfiguracaoDoRadar.carregar()
    config.estrategia_de_temas = "equilibrada"
    config.save()
    assert "Ordem: pela nota." in client.get(url).content.decode()
