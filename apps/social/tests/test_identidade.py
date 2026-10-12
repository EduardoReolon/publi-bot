"""Identidade visual: fundos padrao por papel e tipo, imagem enviada por
lamina e o pedido de imagens para uma IA de imagem."""

from __future__ import annotations

import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.social import identidade, laminas
from apps.social.models import FundoDeLamina, Post
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401

U = "core.urls_tenants"
LAMINAS = [
    {"titulo": "Voce acorda cansado?", "texto": "Mesmo dormindo 8 horas"},
    {"titulo": "40% dos adultos", "texto": "relatam cansaco frequente"},
    {"titulo": "1. Observe o sono", "texto": "anote por uma semana"},
    {"titulo": "Cuidado com cafe", "texto": "perto da hora de dormir"},
]


def _png(cor: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1080, 1350), cor).save(buffer, format="PNG")
    return buffer.getvalue()


def _fundo(destino, papel, cor, etiqueta="", **campos):
    fundo = FundoDeLamina(destino=destino, papel=papel, etiqueta=etiqueta, **campos)
    fundo.arquivo.save(f"{cor}.png", ContentFile(_png(cor)), save=False)
    fundo.save()
    return fundo


def _cor(post, n):
    imagem = Image.open(default_storage.open(post.imagens[n - 1]["caminho"])).convert("RGB")
    return imagem.getpixel((5, 5))


@pytest.mark.django_db
def test_fundo_de_cada_lamina_pelo_papel_e_pelo_tipo(ambiente, modulo, settings, tmp_path):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    destino = _destino("instagram", chamada_final="Link na bio")
    capa = _fundo(destino, "capa", "red", cor_texto="#000000")
    dado = _fundo(destino, "meio", "green", "dado")
    lista = _fundo(destino, "meio", "blue", "lista")
    geral = _fundo(destino, "meio", "yellow")
    final = _fundo(destino, "final", "purple")
    fundos = list(destino.fundos.all())
    itens = laminas.com_a_chamada(LAMINAS, "Link na bio")
    total = len(itens)
    escolhidos = [laminas.escolher_fundo(fundos, n, total, i) for n, i in enumerate(itens, 1)]
    assert escolhidos == [capa, dado, lista, geral, final]

    post = Post.objects.create(
        destino=destino,
        artigo_id=ARTIGO.id,
        motivo="novo",
        situacao="rascunho",
        extras={"laminas": LAMINAS},
    )
    laminas.preparar_imagens(post, ARTIGO)
    post.refresh_from_db()
    # Os fundos padrao entram sem escurecer (ja vem desenhados para o texto).
    assert _cor(post, 1) == (255, 0, 0) and _cor(post, 2) == (0, 128, 0)
    assert _cor(post, 5) == (128, 0, 128)
    assert laminas.dados_do_editor(post, ARTIGO)["cores"]["texto"] == "#000000"

    texto = identidade.pedido(post)
    assert "Gere 0 imagem" in texto and "nao gere" in texto


@pytest.mark.django_db
def test_pedido_de_imagens_e_imagem_enviada_por_lamina(ambiente, modulo, settings, tmp_path):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    destino = _destino(
        "instagram",
        estilo_das_imagens="misto",
        descricao_do_estilo="ilustracao plana, tons de azul e areia",
        cenarios_de_foto="ambiente e equipamentos",
    )
    _fundo(destino, "final", "purple")
    post = Post.objects.create(
        destino=destino,
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo="novo",
        situacao="rascunho",
        extras={"laminas": LAMINAS[:2]},
    )
    texto = identidade.pedido(post)
    assert "NAO escreva nenhum texto" in texto and "tons de azul e areia" in texto
    assert "Foto realista so quando: ambiente e equipamentos" in texto
    assert "Lamina 2 (meio): uma cena que mostre a ideia de: 40% dos adultos" in texto
    assert "Lamina 3" not in texto and "As laminas 3 usam o fundo padrao" in texto

    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    imagem = SimpleUploadedFile("cena.png", _png("red"), content_type="image/png")
    client.post(acao, {"acao": "fundo_da_lamina", "n": "2", "imagem": imagem})
    post.refresh_from_db()
    assert post.extras["fundos"]["2"].endswith("fundo-2.jpg")
    vermelho, verde, _ = _cor(post, 2)
    assert vermelho > verde  # a cena enviada, escurecida para o texto
    estado = {x["n"]: x["fundo"] for x in identidade.situacao_das_laminas(post)}
    assert estado[2] == "enviado" and estado[3].startswith("padrao")
    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Copiar o pedido de imagens" in pagina

    client.post(acao, {"acao": "tirar_fundo_da_lamina", "n": "2"})
    post.refresh_from_db()
    assert "2" not in post.extras["fundos"]


@pytest.mark.django_db
def test_tela_de_identidade_envia_e_tira_fundos(ambiente, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    destino = _destino("instagram")
    url = reverse("social:identidade", args=[destino.pk], urlconf=U)
    assert "Fundos padrao" in client.get(url).content.decode()
    client.post(
        url,
        {
            "papel": "meio",
            "etiqueta": "dado",
            "cor_texto": "#111111",
            "cor_destaque": "azul",  # invalida: fica vazia
            "arquivo": SimpleUploadedFile("f.png", _png("green"), content_type="image/png"),
        },
    )
    fundo = destino.fundos.get()
    assert (fundo.papel, fundo.etiqueta, fundo.cor_texto, fundo.cor_destaque) == (
        "meio",
        "dado",
        "#111111",
        "",
    )
    assert (
        client.get(reverse("social:fundo_privado", args=[fundo.pk], urlconf=U)).status_code == 200
    )
    client.post(url, {"acao": "apagar", "fundo": fundo.pk})
    assert not destino.fundos.exists()
