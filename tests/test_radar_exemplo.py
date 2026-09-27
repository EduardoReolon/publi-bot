"""O comando de dados de exemplo do radar: cria, recusa repetir e apaga tudo."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.radar.models import (
    ConcorrenteSugerido,
    GrupoDeDemanda,
    Oportunidade,
    SementeSugerida,
    SinalDeDemanda,
    SugestaoDeAtualizacao,
)
from tests.test_radar import radar  # noqa: F401


@pytest.mark.django_db
def test_cria_e_apaga_so_o_que_e_de_exemplo(radar):  # noqa: F811
    real = SinalDeDemanda.objects.create(texto="tema de verdade", fonte="paa")

    call_command("radar_exemplo")

    assert SinalDeDemanda.objects.filter(extra__exemplo=True).count() == 11
    assert GrupoDeDemanda.objects.exists()
    assert Oportunidade.objects.exists()
    assert ConcorrenteSugerido.objects.count() == 1
    assert SementeSugerida.objects.count() == 2
    assert SugestaoDeAtualizacao.objects.count() == 1
    with pytest.raises(CommandError, match="Ja ha"):
        call_command("radar_exemplo")

    call_command("radar_exemplo", apagar=True)

    assert list(SinalDeDemanda.objects.all()) == [real]
    assert not Oportunidade.objects.exists()
    assert not ConcorrenteSugerido.objects.exists()
    assert not SementeSugerida.objects.exists()
    assert not SugestaoDeAtualizacao.objects.exists()


@pytest.mark.django_db
def test_tema_de_exemplo_nunca_vira_busca(radar):  # noqa: F811
    from apps.radar.coleta import _temas_para_expandir

    call_command("radar_exemplo")

    assert _temas_para_expandir() == []
