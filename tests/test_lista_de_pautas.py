"""A lista de pautas abre no que da trabalho, na ordem do menor esforco."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.content.models import Article, Topic
from tests.test_interface import ambiente  # noqa: F401

U = "core.urls_tenants"


@pytest.mark.django_db
def test_a_fazer_esconde_publicadas_e_rejeitadas_e_ordena_por_esforco(ambiente):  # noqa: F811
    _, _, client = ambiente
    publicada = Topic.objects.create(title="Pauta publicada", status=Topic.Status.USED)
    Article.objects.create(title="x", topic=publicada, status=Article.Status.PUBLISHED)
    Topic.objects.create(title="Pauta rejeitada", status=Topic.Status.REJECTED)
    Topic.objects.create(title="Pauta para gerar")
    revisar = Topic.objects.create(title="Pauta para revisar", status=Topic.Status.USED)
    Article.objects.create(title="y", topic=revisar, status=Article.Status.PENDING_REVIEW)
    Topic.objects.create(title="Pauta sem fonte", status=Topic.Status.WAITING_SOURCES)

    tela = client.get(reverse("content:pautas", urlconf=U)).content.decode()
    assert "Pauta publicada" not in tela and "Pauta rejeitada" not in tela
    ordem = [tela.index(t) for t in ("Pauta para revisar", "Pauta para gerar", "Pauta sem fonte")]
    assert ordem == sorted(ordem)
    assert "Revisar e aprovar" in tela and "Gerar com um clique" in tela

    concluidas = client.get(reverse("content:pautas", urlconf=U) + "?ver=concluidas")
    assert "Pauta publicada" in concluidas.content.decode()
    rejeitadas = client.get(reverse("content:pautas", urlconf=U) + "?ver=rejeitadas")
    assert "Pauta rejeitada" in rejeitadas.content.decode()
