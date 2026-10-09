"""As telas e os enderecos publicos, e a porta (fontes.py) contra o nucleo de
verdade: um artigo publicado vira post pelo botao do proprio artigo."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.social import fontes
from apps.social.models import Abordagem, Destino, Post

U = "core.urls_tenants"


def _artigo_publicado():
    from apps.content.models import Article, ArticleSection

    artigo = Article.objects.create(
        title="Cansaco que nao passa",
        slug="cansaco",
        status=Article.Status.PUBLISHED,
        remote_id="r1",
        published_url="https://site.exemplo.org/cansaco/",
        published_at=timezone.now() - timedelta(days=1),
        meta_description="Quando o cansaco merece exame.",
        body_markdown=(
            "Voce acorda cansado mesmo depois de dormir oito horas por noite.\n\n"
            "## Os numeros\n\nEm um estudo com 2.300 adultos, 38% relataram cansaco "
            "persistente [Silva et al., 2024](https://revista.org/x).\n\n"
            "## Referências\n\n1. Silva et al."
        ),
    )
    ArticleSection.objects.create(
        article=artigo,
        order=1,
        heading="Os numeros",
        body_markdown="Em um estudo com 2.300 adultos, 38% relataram cansaco persistente.",
    )
    return artigo


@pytest.mark.django_db
def test_a_porta_le_o_artigo_do_nucleo(ambiente):
    artigo = _artigo_publicado()
    lido = fontes.artigo(artigo.pk)
    assert lido.titulo == "Cansaco que nao passa" and lido.url.endswith("/cansaco/")
    assert lido.secoes == [("Os numeros", lido.secoes[0][1])]
    assert any("2.300 adultos" in f for f in lido.frases)
    assert "Silva et al., 2024" in lido.texto and "revista.org" not in lido.texto
    assert "Referências" not in lido.texto
    assert fontes.artigos_no_ar() == [str(artigo.pk)]
    assert fontes.sinais(lido) == {"quase_la": False, "conversoes": 0}


@pytest.mark.django_db
def test_do_artigo_as_redes_e_as_abas(ambiente, monkeypatch, django_capture_on_commit_callbacks):
    from apps.social import tasks

    _, _, client = ambiente
    escritos = []
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: escritos.append(pk))
    artigo = _artigo_publicado()
    pagina = client.get(reverse("content:revisar", args=[artigo.pk], urlconf=U)).content.decode()
    assert "Levar as redes" in pagina

    # Sem conta cadastrada: manda cadastrar.
    resposta = client.post(reverse("social:levar_as_redes", args=[artigo.pk], urlconf=U))
    assert "aba=configurar" in resposta["Location"]

    client.post(
        reverse("social:novo_destino", urlconf=U),
        {
            "rede": "linkedin",
            "nome": "Perfil",
            "ligado": "on",
            "aprovacao": "sempre",
            "teto_semanal": 3,
            "horarios": "8:30, 18:00",
            "dias": ["0", "2"],
        },
    )
    destino = Destino.objects.get()
    assert destino.horarios == ["08:30", "18:00"] and destino.dias == [0, 2]

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("social:levar_as_redes", args=[artigo.pk], urlconf=U))
    post = Post.objects.get()
    assert escritos == [str(post.pk)] and post.motivo == Post.Motivo.PEDIDO
    assert Abordagem.objects.count() >= 6  # as sementes

    for aba in ("revisar", "agenda", "publicados", "comentarios", "funciona", "configurar"):
        resposta = client.get(reverse("social:inicio", urlconf=U) + f"?aba={aba}")
        assert resposta.status_code == 200, aba
    menu = client.get(reverse("social:inicio", urlconf=U)).content.decode()
    assert "Redes" in menu and "Escrevendo" in menu


@pytest.mark.django_db
def test_revisar_aprovar_e_postar_a_mao(ambiente):
    _, usuario, client = ambiente
    destino = Destino.objects.create(rede="linkedin", nome="Perfil")
    post = Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao=Post.Situacao.RASCUNHO,
        texto="Antes",
        artigo_titulo="Cansaco",
        artigo_url="https://site.exemplo.org/cansaco/",
        extras={"primeiro_comentario": "Leia:", "link": "https://painel/r/x/"},
    )
    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)

    client.post(acao, {"acao": "salvar", "texto": "Depois", "primeiro_comentario": "O artigo:"})
    post.refresh_from_db()
    assert post.texto == "Depois" and post.extras["primeiro_comentario"] == "O artigo:"

    client.post(acao, {"acao": "aprovar", "quando": "2030-01-07T09:15"})
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.APROVADO and post.aprovado_por == usuario
    assert timezone.localtime(post.agendado_para).strftime("%d/%m %H:%M") == "07/01 09:15"
    agenda = client.get(reverse("social:inicio", urlconf=U) + "?aba=agenda").content.decode()
    assert "Copiar texto" in agenda and "Ja postei" in agenda

    client.post(acao, {"acao": "ja_postei", "url": "https://linkedin.com/feed/update/1/"})
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.PUBLICADO and post.url_remota.endswith("/1/")


@pytest.mark.django_db
def test_enderecos_publicos_sem_login(ambiente, settings, tmp_path):
    from django.test import Client

    _, _, autenticado = ambiente
    settings.MEDIA_ROOT = tmp_path
    anonimo = Client(HTTP_HOST=autenticado.defaults["HTTP_HOST"])
    destino = Destino.objects.create(rede="instagram", nome="Insta", conta_nome="@clinica")
    post = Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao=Post.Situacao.PUBLICADO,
        artigo_titulo="Cansaco",
        artigo_url="https://site.exemplo.org/cansaco/",
        publicado_em=timezone.now(),
        abordagem=Abordagem.objects.create(nome="Lista", instrucao="x"),
    )

    resposta = anonimo.get(reverse("social:clique", args=[post.chave_publica], urlconf=U))
    assert resposta.status_code == 302
    destino_do_link = urlsplit(resposta["Location"])
    assert destino_do_link.path == "/cansaco/"
    assert parse_qs(destino_do_link.query)["utm_source"] == ["instagram"]
    post.refresh_from_db()
    assert post.cliques == 1
    assert anonimo.get(reverse("social:clique", args=["nao-existe"], urlconf=U)).status_code == 404

    imagem = reverse("social:imagem", args=[post.chave_publica, 1], urlconf=U)
    assert anonimo.get(imagem).status_code == 404
    default_storage.save(f"social/{post.pk}/1.png", ContentFile(b"\x89PNG"))
    assert anonimo.get(imagem)["Content-Type"] == "image/png"

    bio = anonimo.get(
        reverse("social:bio", args=[destino.chave_publica], urlconf=U)
    ).content.decode()
    assert "Cansaco" in bio and f"/redes/r/{post.chave_publica}/" in bio

    # O resto exige login.
    assert anonimo.get(reverse("social:inicio", urlconf=U)).status_code == 302


@pytest.mark.django_db
def test_conectar_e_voltar_da_rede(ambiente, monkeypatch, settings):
    from apps.social.redes import instagram

    _, _, client = ambiente
    settings.SOCIAL_META_APP_ID = ""
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    resposta = client.get(reverse("social:conectar", args=[destino.pk], urlconf=U), follow=True)
    assert "SOCIAL_META_APP_ID" in resposta.content.decode()

    retorno = reverse("social:retorno", urlconf=U)
    resposta = client.get(retorno, {"state": "falso", "code": "x"}, follow=True)
    assert "venceu" in resposta.content.decode()

    monkeypatch.setattr(
        instagram.OAuthInstagram,
        "trocar_codigo",
        lambda self, d, c, r: {"access_token": "tok", "expira_em": ""},
    )
    monkeypatch.setattr(
        instagram.OAuthInstagram, "contas", lambda self, d: [("1", "@a (A)"), ("2", "@b (B)")]
    )
    from core.retorno_oauth import assinar

    estado = assinar(connection.schema_name, "/redes/conectar/retorno/", destino=str(destino.pk))
    pagina = client.get(retorno, {"state": estado, "code": "x"}).content.decode()
    assert "@a (A)" in pagina and "@b (B)" in pagina
    client.post(
        reverse("social:escolher_conta", args=[destino.pk], urlconf=U),
        {"conta": "2", "nome_2": "@b (B)"},
    )
    destino.refresh_from_db()
    assert destino.conectado and destino.conta_nome == "@b (B)"

    # Conta que a rede nao ofereceu nao entra.
    client.post(reverse("social:escolher_conta", args=[destino.pk], urlconf=U), {"conta": "9"})
    destino.refresh_from_db()
    assert destino.conta_id == "2"

    # Reconectar (o acesso vence) mantem a conta, sem perguntar; "trocar" pergunta.
    estado = assinar(connection.schema_name, "/redes/conectar/retorno/", destino=str(destino.pk))
    client.get(retorno, {"state": estado, "code": "x"})
    destino.refresh_from_db()
    assert destino.conta_id == "2"
    estado = assinar(
        connection.schema_name, "/redes/conectar/retorno/", destino=str(destino.pk), trocar=True
    )
    pagina = client.get(retorno, {"state": estado, "code": "x"}).content.decode()
    assert "Qual conta?" in pagina and "a atual" in pagina
