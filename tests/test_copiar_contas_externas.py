"""Copiar as contas externas de um tenant para outro, sem mostrar as chaves."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django_tenants.utils import schema_context

from apps.inference.security import cifrar, decifrar
from apps.radar.models import ContasExternas


@pytest.mark.django_db
def test_copia_cifrado_e_nao_mostra(tenant_factory, capsys):
    tenant_factory("origem_a")
    tenant_factory("destino_b")
    with schema_context("origem_a"):
        contas = ContasExternas.carregar()
        contas.dataforseo_login = "eu@exemplo.org"
        contas.dataforseo_senha_ciphertext = cifrar("senha-secreta-1")
        contas.youtube_chave_ciphertext = cifrar("chave-youtube-2")
        contas.save()
    with schema_context("destino_b"):
        destino = ContasExternas.carregar()
        destino.openalex_chave_ciphertext = cifrar("openalex-do-b")
        destino.save()

    call_command("copiar_contas_externas", de="origem_a", para="destino_b")
    saida = capsys.readouterr().out
    assert "senha-secreta-1" not in saida and "chave-youtube-2" not in saida
    assert "copiado: YouTube: chave" in saida

    with schema_context("destino_b"):
        destino = ContasExternas.carregar()
        assert destino.dataforseo_login == "eu@exemplo.org"
        assert decifrar(destino.youtube_chave_ciphertext) == "chave-youtube-2"
        assert decifrar(destino.openalex_chave_ciphertext) == "openalex-do-b"  # nao apagou

    call_command("copiar_contas_externas", de="origem_a", mostrar=True)
    assert "chave-youtube-2" in capsys.readouterr().out
