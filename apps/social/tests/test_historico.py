"""Conta que ja existe: o passado (posts, resultado, comentarios), o gasto com
anuncios (API e planilha), o diagnostico e o aviso de leitura parada. As APIs
sobre respostas simuladas, no formato das documentacoes da Meta."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.social import anuncios, diagnostico, historico, painel
from apps.social.models import Anuncio, Comentario, Destino, Post
from apps.social.tests.test_apis import Rede, _conectado

U = "core.urls_tenants"


def _media(i: int, **extra) -> dict:
    return {
        "id": f"m{i}",
        "caption": f"Legenda numero {i} sobre cansaco\n#saude",
        "media_type": "CAROUSEL_ALBUM" if i % 2 else "IMAGE",
        "permalink": f"https://instagram.com/p/{i}/",
        "timestamp": (timezone.now() - timedelta(days=3 * i)).isoformat(),
        "like_count": 10 + i,
        "comments_count": 1 if i == 1 else 0,
        **extra,
    }


def _rede_do_instagram(pedidos_de_insights: list | None = None) -> Rede:
    def insights(pedido):
        if pedidos_de_insights is not None:
            pedidos_de_insights.append(pedido.url.path)
        if "/m3/" in pedido.url.path:  # post de antes da conta virar profissional
            return httpx.Response(400, json={"error": {"message": "media posted before"}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"name": "reach", "values": [{"value": 200}]},
                    {"name": "saved", "values": [{"value": 6}]},
                    {"name": "shares", "values": [{"value": 2}]},
                ]
            },
        )

    return Rede(
        {
            # A segunda pagina vem pelo endereco "next" (que ja traz o acesso).
            "GET graph.facebook.com/v21.0/ig1/media": lambda p: httpx.Response(
                200,
                json=(
                    {"data": [_media(3)]}
                    if "after" in str(p.url)
                    else {
                        "data": [_media(1), _media(2)],
                        "paging": {"next": "https://graph.facebook.com/v21.0/ig1/media?after=x"},
                    }
                ),
            ),
            "GET graph.facebook.com/v21.0/m1/insights": insights,
            "GET graph.facebook.com/v21.0/m2/insights": insights,
            "GET graph.facebook.com/v21.0/m3/insights": insights,
            "GET graph.facebook.com/v21.0/m1/comments": httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "c1",
                            "text": "Quanto tempo dura o tratamento?",
                            "username": "ana",
                            "timestamp": "2026-01-02T10:00:00+0000",
                            "replies": {"data": [{"username": "clinica"}]},
                        },
                        {
                            "id": "c2",
                            "text": "Como faco para marcar?",
                            "username": "bia",
                            "timestamp": "2026-01-02T11:00:00+0000",
                        },
                    ]
                },
            ),
            "GET graph.facebook.com/v21.0/ig1": httpx.Response(200, json={"followers_count": 850}),
        }
    )


@pytest.mark.django_db
def test_importa_o_passado_em_paginas_sem_duplicar(ambiente):
    rede = _rede_do_instagram()
    destino = _conectado("instagram", "ig1", conta_nome="@clinica (Clinica)")

    feito = historico.importar(destino, http=rede.cliente())

    assert feito["posts"] == 3 and feito["novos"] == 3 and feito["sem_detalhe"] == 1
    posts = {p.id_remoto: p for p in destino.posts.all()}
    assert all(p.motivo == Post.Motivo.HISTORICO for p in posts.values())
    assert posts["m1"].metricas["alcance"] == 200 and posts["m1"].metricas["salvos"] == 6
    assert posts["m3"].metricas == {"curtidas": 13, "comentarios": 0}  # sem insights: a lista
    assert posts["m1"].extras["formato"] == "CAROUSEL_ALBUM"
    assert posts["m1"].gancho == "Legenda numero 1 sobre cansaco"

    # Comentarios antigos ficam so para o diagnostico: nenhuma Pergunta nova.
    lidos = {c.id_remoto: c for c in Comentario.objects.filter(post=posts["m1"])}
    assert lidos["c1"].respondido_em is not None  # o dono respondeu na rede
    assert lidos["c2"].respondido_em is None and lidos["c2"].pergunta_id is None

    destino.refresh_from_db()
    assert destino.seguidores == 850 and destino.sincronizado_em is not None
    assert feito["faltam"] == 0

    # De novo: nada duplica.
    assert historico.importar(destino, http=rede.cliente())["novos"] == 0
    assert destino.posts.count() == 3


@pytest.mark.django_db
def test_importado_nao_entra_na_bio_nem_nos_pendentes(ambiente):
    _, _, client = ambiente
    destino = _conectado("instagram", "ig1", conta_nome="@clinica")
    historico.importar(destino, http=_rede_do_instagram().cliente())
    bio = client.get(reverse("social:bio", args=[destino.chave_publica], urlconf=U))
    assert "Legenda numero" not in bio.content.decode()
    from apps.social.views import _contagens

    assert _contagens()["comentarios"] == 0


# -- Anuncios ------------------------------------------------------------------------------
@pytest.mark.django_db
def test_gasto_pela_api_liga_ao_post_impulsionado(ambiente):
    destino = _conectado("instagram", "ig1", conta_nome="@clinica", anuncios_conta_id="act_9")
    post = Post.objects.create(
        destino=destino,
        motivo=Post.Motivo.HISTORICO,
        situacao=Post.Situacao.PUBLICADO,
        id_remoto="m1",
        texto="Legenda",
        publicado_em=timezone.now() - timedelta(days=10),
    )
    rede = Rede(
        {
            "GET graph.facebook.com/v21.0/act_9/insights": httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "ad_id": "a1",
                            "ad_name": "Publicacao do Instagram: Legenda",
                            "spend": "35.50",
                            "reach": "4000",
                            "impressions": "5200",
                            "inline_link_clicks": "71",
                            "date_start": "2026-01-03",
                            "date_stop": "2026-01-06",
                        },
                        {
                            "ad_id": "a2",
                            "ad_name": "Anuncio sem post",
                            "spend": "10",
                            "date_start": "2026-02-01",
                        },
                    ]
                },
            ),
            "GET graph.facebook.com/v21.0/": httpx.Response(
                200,
                json={
                    "a1": {"creative": {"effective_instagram_media_id": "m1"}},
                    "a2": {"creative": {}},
                },
            ),
        }
    )

    assert anuncios.sincronizar(destino, http=rede.cliente()) == 2

    post.refresh_from_db()
    assert post.impulsionado and post.custo_impulso == Decimal("35.50")
    assert Anuncio.objects.get(id_remoto="a1").cliques == 71
    assert Anuncio.objects.get(id_remoto="a2").post is None
    destino.refresh_from_db()
    assert destino.anuncios_sincronizados_em is not None and destino.anuncios_erro == ""
    # O pedido de gasto e "o periodo todo", por anuncio.
    pedido = next(p for p in rede.pedidos if "insights" in p.url.path)
    assert pedido.url.params["date_preset"] == "maximum" and pedido.url.params["level"] == "ad"


@pytest.mark.django_db
def test_api_de_anuncios_recusada_fica_gravada_e_avisa(ambiente):
    destino = _conectado("instagram", "ig1", anuncios_conta_id="act_9")
    rede = Rede(
        {
            "GET graph.facebook.com/v21.0/act_9/insights": httpx.Response(
                403, json={"error": {"message": "(#200) Requires ads_read permission"}}
            )
        }
    )
    assert anuncios.sincronizar(destino, http=rede.cliente()) == 0
    destino.refresh_from_db()
    assert "ads_read" in destino.anuncios_erro
    aviso = painel.leitura_parada(destino)
    assert "gasto com anuncios sem atualizar" in aviso and "planilha" in aviso
    assert aviso in painel.alertas()


def test_numeros_da_planilha():
    assert anuncios.numero("R$ 1.234,56") == Decimal("1234.56")
    assert anuncios.numero("1,234.56") == Decimal("1234.56")
    assert anuncios.numero("35,5") == Decimal("35.5")
    assert anuncios.numero("12") == Decimal("12")
    assert anuncios.numero("-") is None and anuncios.numero("") is None


PLANILHA = (
    "Nome da campanha;Nome do anúncio;Alcance;Impressões;Valor usado (BRL);"
    "Cliques no link;Início dos relatórios;Término dos relatórios\n"
    "Impulso;Publicação do Instagram: Voce acorda cansado mesmo depois de dormir;"
    "3.200;4.100;42,90;55;2026-01-03;2026-01-09\n"
    "Impulso;Publicação do Instagram: Outro post que nao existe aqui;100;120;5,00;;"
    "2026-02-01;2026-02-02\n"
    ";;;;47,90;;;\n"
)


@pytest.mark.django_db
def test_planilha_do_gerenciador_liga_pelo_comeco_da_legenda(ambiente):
    _, _, client = ambiente
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    post = Post.objects.create(
        destino=destino,
        motivo=Post.Motivo.HISTORICO,
        situacao=Post.Situacao.PUBLICADO,
        texto="Voce acorda cansado mesmo depois de dormir oito horas?\n\nVeja o que pode ser.",
        publicado_em=timezone.now() - timedelta(days=60),
    )
    arquivo = SimpleUploadedFile("relatorio.csv", PLANILHA.encode("utf-8-sig"))
    resposta = client.post(
        reverse("social:acao_no_diagnostico", urlconf=U),
        {"destino": destino.pk, "acao": "planilha", "planilha": arquivo},
    )
    assert resposta.status_code == 302
    assert destino.anuncios.count() == 2  # a linha de total fica de fora
    post.refresh_from_db()
    assert post.impulsionado and post.custo_impulso == Decimal("42.90")

    # Mandar de novo o mesmo arquivo nao duplica.
    anuncios.importar_planilha(destino, PLANILHA.encode())
    assert destino.anuncios.count() == 2


def test_planilha_sem_as_colunas_explica():
    destino = Destino(rede="instagram", nome="x")
    with pytest.raises(anuncios.PlanilhaInvalida, match="Valor usado"):
        anuncios.importar_planilha(destino, b"Campanha,Alcance\nA,10\n")


# -- Diagnostico ------------------------------------------------------------------------------
def _posts_com_resultado(destino, n=12):
    agora = timezone.now()
    for i in range(n):
        carrossel = i % 2 == 0
        Post.objects.create(
            destino=destino,
            motivo=Post.Motivo.HISTORICO,
            situacao=Post.Situacao.PUBLICADO,
            texto=f"Post {i}" + (" #a #b" if carrossel else ""),
            id_remoto=f"m{i}",
            publicado_em=agora - timedelta(days=4 * i + 1),
            extras={"formato": "CAROUSEL_ALBUM" if carrossel else "IMAGE", "gancho": f"Post {i}"},
            # Carrossel engaja o triplo.
            metricas={"alcance": 1000, "salvos": 60 if carrossel else 20, "comentarios": 0},
        )


@pytest.mark.django_db
def test_diagnostico_aponta_formato_e_desperdicio_no_impulso(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta", seguidores=900)
    _posts_com_resultado(destino)
    fraco = destino.posts.get(id_remoto="m1")  # imagem: abaixo da mediana
    Anuncio.objects.create(
        destino=destino,
        id_remoto="a1",
        nome="Impulso",
        gasto=Decimal("80"),
        cliques=40,
        alcance=8000,
        post=fraco,
        origem=Anuncio.Origem.PLANILHA,
    )
    d = diagnostico.montar(destino)

    assert d.suficiente and d.medida == "taxa"
    assert d.grupos["Formato"]["carrossel"]["n"] == 6
    assert any("carrossel rende 3.0x mais que imagem unica" in p.texto for p in d.bons)
    assert any("ja iam mal no organico" in p.texto for p in d.ruins)
    assert d.anuncios["custo_por_clique"] == "R$ 2,00"
    texto = diagnostico.como_texto(d)
    assert "O que esta bom:" in texto and "Anuncios: 1 anuncios" in texto


@pytest.mark.django_db
def test_diagnostico_com_poucos_posts_nao_inventa(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    _posts_com_resultado(destino, n=3)
    d = diagnostico.montar(destino)
    assert not d.suficiente and d.bons == [] and d.ruins == []


@pytest.mark.django_db
def test_tela_do_diagnostico(ambiente):
    _, _, client = ambiente
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    _posts_com_resultado(destino)
    pagina = client.get(
        reverse("social:diagnostico", urlconf=U) + f"?destino={destino.pk}"
    ).content.decode()
    assert "O que esta bom" in pagina and "Copiar o diagnostico" in pagina
    assert "Pela planilha" in pagina and "Exportar dados da tabela" in pagina


# -- Leitura parada -------------------------------------------------------------------------
@pytest.mark.django_db
def test_leitura_parada_vira_aviso_no_painel_e_nas_redes(ambiente):
    _, _, client = ambiente
    destino = _conectado("instagram", "ig1")
    destino.sincronizado_em = timezone.now() - timedelta(days=4)
    destino.erro_de_sincronia = "Instagram (seguidores): HTTP 500"
    destino.save()
    aviso = painel.leitura_parada(destino)
    assert "4 dias sem atualizar sozinho" in aviso and "HTTP 500" in aviso
    assert painel.pendencias()["redes"] == 1

    pagina = client.get(reverse("accounts:painel", urlconf=U)).content.decode()
    assert "4 dias sem atualizar sozinho" in pagina
    redes = client.get(reverse("social:estrategia", urlconf=U)).content.decode()
    assert "A leitura automatica de uma conta parou" in redes

    destino.sincronizado_em = timezone.now()
    destino.save()
    assert painel.leitura_parada(destino) == ""


@pytest.mark.django_db
def test_quem_entrega_o_passado_e_ja_postei_nao_duplica(ambiente):
    assert historico.suporta(Destino(rede="instagram", nome="i"))
    assert not historico.suporta(Destino(rede="linkedin", nome="l", autor="pessoa"))
    assert historico.suporta(Destino(rede="linkedin", nome="l", autor="organizacao"))
    assert historico.suporta(Destino(rede="gmn", nome="g"))

    destino = _conectado("instagram", "ig1", conta_nome="@clinica")
    # Postado pelo "Ja postei" (sem id na rede), com o link que o app copia.
    Post.objects.create(
        destino=destino,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.PUBLICADO,
        url_remota="https://instagram.com/p/2?igsh=abc",
        publicado_em=timezone.now(),
    )
    historico.importar(destino, http=_rede_do_instagram().cliente())
    assert destino.posts.filter(motivo=Post.Motivo.HISTORICO).count() == 2
