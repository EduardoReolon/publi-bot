"""Publico do impulso: salvo, ligado ao post impulsionado, resultado somado por
publico, sugerido de volta pelo foco, e pedido/resposta da outra IA."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.social import anuncios, estrategia, fontes, publicos
from apps.social.models import Anuncio, Destino, Post, PublicoDeImpulso

U = "core.urls_tenants"

RESPOSTA = """Boa ideia impulsionar.

**PUBLICOS:**
- Pais cansados de Curitiba | Curitiba +20km | 28-45 | Paternidade; Cafe; Corrida | quem compra
  (o melhor primeiro)
- Amplo Sul | PR, SC | 25-55 | Bem-estar; Sono | alcance barato
COMENTARIOS:
Deixe o Advantage+ ligado.
FIM
"""


def _impulsionado(destino, publico, foco, gasto, cliques, alcance, n):
    post = Post.objects.create(
        destino=destino, motivo="novo", situacao="publicado", extras={"foco": foco}
    )
    Anuncio.objects.create(
        destino=destino,
        id_remoto=f"ad{n}",
        gasto=gasto,
        cliques=cliques,
        alcance=alcance,
        post=post,
        publico=publico,
        origem="api",
    )
    return post


@pytest.mark.django_db
def test_resposta_vira_publico_salvo_e_o_melhor_volta_como_sugestao(ambiente, monkeypatch):
    monkeypatch.setattr(fontes, "negocio", lambda: {"regioes": ["Curitiba"], "tema": "sono"})
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    lidos, comentarios = publicos.ler(RESPOSTA)
    assert [p["nome"] for p in lidos] == ["Pais cansados de Curitiba", "Amplo Sul"]
    assert "Interesses: Paternidade; Cafe; Corrida" in lidos[0]["descricao"]
    assert "Advantage+" in comentarios
    caro, barato = publicos.salvar(destino, lidos)
    assert caro.origem == PublicoDeImpulso.Origem.OUTRA_IA
    # O mesmo nome de novo: reaproveita, nao duplica.
    assert publicos.salvar(destino, lidos[:1]) == [caro]
    assert destino.publicos.count() == 2

    _impulsionado(destino, caro, "site", Decimal("40"), 10, 2000, 1)
    _impulsionado(destino, barato, "site", Decimal("40"), 40, 8000, 2)
    linha = publicos.melhor(destino, "site")
    assert linha["publico"] == barato and linha["custo_por_clique"] == Decimal("1.00")
    assert "Amplo Sul" in estrategia._publico_sugerido(destino, "Sono", "site")

    post = Post.objects.create(
        destino=destino,
        motivo="novo",
        texto="Voce dorme e acorda cansado?",
        extras={"foco": "alcance", "laminas": [{"titulo": "Cansaco", "texto": "Sono ruim"}]},
    )
    texto = publicos.pedido(post)
    assert "Curitiba" in texto and "Alcance (quem nao segue)" in texto
    assert "Lamina 1: Cansaco" in texto and "Amplo Sul" in texto and "por clique" in texto


@pytest.mark.django_db
def test_telas_do_publico(ambiente, monkeypatch):
    monkeypatch.setattr(fontes, "negocio", lambda: {"regioes": ["Curitiba"]})
    _, _, client = ambiente
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    post = Post.objects.create(destino=destino, motivo="novo", situacao="publicado")
    url = reverse("social:publicos", urlconf=U)

    pagina = client.get(url, {"destino": destino.pk, "post": post.pk}).content.decode()
    assert "Copiar para outra IA" in pagina and "ads_management" not in pagina
    client.post(
        url, {"destino": destino.pk, "post": post.pk, "acao": "resposta", "resposta": RESPOSTA}
    )
    publico = destino.publicos.get(nome="Amplo Sul")

    acao = reverse("social:acao_na_estrategia", urlconf=U)
    client.post(
        acao,
        {"acao": "impulso", "post": post.pk, "valor": "30", "publico": publico.pk, "dias": "3"},
    )
    # Um segundo impulso, outro valor e outra data: o formulario continua ali.
    assert (
        "Registrar outro impulso"
        in client.get(reverse("social:inicio", urlconf=U), {"aba": "publicados"}).content.decode()
    )
    client.post(
        acao, {"acao": "impulso", "post": post.pk, "valor": "12,50", "inicio": "2026-10-20"}
    )
    post.refresh_from_db()
    assert post.impulsionado and post.custo_impulso == Decimal("42.50")
    primeiro, segundo = post.anuncios.order_by("gasto").reverse()
    assert primeiro.publico == publico and primeiro.dias == 3
    assert segundo.inicio.isoformat() == "2026-10-20" and segundo.publico is None
    assert "Amplo Sul" in client.get(url, {"destino": destino.pk}).content.decode()

    client.post(acao, {"acao": "publico_do_impulso", "anuncio": segundo.pk, "publico": publico.pk})
    segundo.refresh_from_db()
    assert segundo.publico == publico
    client.post(acao, {"acao": "apagar_impulso", "anuncio": segundo.pk})
    post.refresh_from_db()
    assert post.custo_impulso == Decimal("30") and post.anuncios.count() == 1

    client.post(url, {"destino": destino.pk, "acao": "apagar", "publico": publico.pk})
    publico.refresh_from_db()
    assert not publico.ativo


@pytest.mark.django_db
def test_impulso_lido_da_meta_substitui_o_digitado_no_mesmo_periodo(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    publico = PublicoDeImpulso.objects.create(destino=destino, nome="Pais")
    post = Post.objects.create(
        destino=destino, motivo="novo", situacao="publicado", id_remoto="media1"
    )
    hoje = date(2026, 10, 10)
    anuncios.registrar(post, Decimal("30"), inicio=hoje, dias=3, publico=publico)
    anuncios.registrar(post, Decimal("50"), inicio=hoje + timedelta(days=20))
    Anuncio.objects.create(
        destino=destino,
        id_remoto="ad9",
        media_id="media1",
        gasto=Decimal("29.80"),
        cliques=12,
        alcance=900,
        inicio=hoje + timedelta(days=1),
        fim=hoje + timedelta(days=3),
        origem="api",
    )
    anuncios.vincular(destino)
    post.refresh_from_db()
    lidos = {a.origem: a for a in post.anuncios.all()}
    assert set(lidos) == {"api", "manual"} and lidos["manual"].gasto == Decimal("50")
    assert lidos["api"].publico == publico  # o publico escolhido passou para o lido
    assert post.custo_impulso == Decimal("79.80")
    assert publicos.resultado(publico)["custo_por_clique"] == Decimal("2.48")
