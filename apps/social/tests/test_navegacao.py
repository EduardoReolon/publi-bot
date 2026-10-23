"""A conta escolhida na barra vale para todas as abas; Configurar em partes;
personagens e cenarios fixos da conta no pedido e na geracao de imagens."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.social import clareza, fontes, identidade
from apps.social.models import Abordagem, Comentario, Destino, ElementoVisual, Post

U = "core.urls_tenants"


@pytest.mark.django_db
def test_barra_de_contas_vale_para_todas_as_abas(ambiente):
    _, _, client = ambiente
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    linkedin = Destino.objects.create(rede="linkedin", nome="Lk")
    p1 = Post.objects.create(destino=insta, motivo="novo", situacao="rascunho", texto="do insta")
    p2 = Post.objects.create(destino=linkedin, motivo="novo", situacao="rascunho", texto="do lk")
    Comentario.objects.create(post=p1, id_remoto="1", texto="pergunta do insta?")
    Comentario.objects.create(post=p2, id_remoto="2", texto="pergunta do lk?")
    inicio = reverse("social:inicio", urlconf=U)

    tudo = client.get(inicio, {"aba": "revisar", "destino": ""}).content.decode()
    assert "do insta" in tudo and "do lk" in tudo
    so_insta = client.get(inicio, {"aba": "revisar", "destino": insta.pk}).content.decode()
    assert "do insta" in so_insta and "do lk" not in so_insta
    # Lembrada na sessao: a proxima aba ja vem filtrada.
    comentarios = client.get(inicio, {"aba": "comentarios"}).content.decode()
    assert "pergunta do insta" in comentarios and "pergunta do lk" not in comentarios
    # Telas de uma conta so seguem a mesma escolha.
    estrategia = client.get(reverse("social:estrategia", urlconf=U)).context
    assert estrategia["destino"] == insta and estrategia["contagens"]["so_uma"]

    # Configurar: a conta da barra; abordagens da rede dela; para todas.
    conta = client.get(inicio, {"aba": "configurar"}).content.decode()
    assert "Publico e voz" in conta and "Ritmo" in conta and "Lk" in conta
    Abordagem.objects.create(nome="So LinkedIn", instrucao="x", redes=["linkedin"])
    textos = client.get(inicio, {"aba": "configurar", "parte": "textos"}).content.decode()
    assert "So LinkedIn" not in textos and "Abordagens" in textos
    assert "regras" in client.get(inicio, {"aba": "configurar", "parte": "todas"}).content.decode()


@pytest.mark.django_db
def test_personagens_fixos_no_pedido_e_na_geracao(ambiente, monkeypatch):
    _, _, client = ambiente
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    url = reverse("social:identidade", args=[destino.pk], urlconf=U)
    client.post(
        url,
        {
            "acao": "elemento",
            "nome": "Gugu",
            "tipo": "personagem",
            "descricao": "gorila de terno azul-marinho e gravata amarela",
        },
    )
    client.post(
        url,
        {
            "acao": "visual",
            "estilo_das_imagens": "quadrinhos",
            "descricao_do_estilo": "traco grosso",
            "chamada_final": "Mande para alguem | link na bio",
            "cor_fundo": "#101010",
            "voz": "JBFqnCBsd6RMkjVDRZzb",
        },
    )
    destino.refresh_from_db()
    assert destino.estilo_das_imagens == "quadrinhos" and destino.cores == {"fundo": "#101010"}
    assert destino.voz == "JBFqnCBsd6RMkjVDRZzb"  # a voz da narracao, na Identidade visual
    assert ElementoVisual.objects.get().nome == "Gugu"
    pagina = client.get(url).content.decode()
    assert "Gugu" in pagina and "Copiar o prompt da referencia local" in pagina

    post = Post(
        destino=destino,
        extras={
            "laminas": [
                {"titulo": "Caixa vazio?", "texto": "", "visual": "Gugu olhando o caixa vazio"}
            ]
        },
    )
    assert "Gugu (personagem): gorila de terno" in clareza.instrucao(post, laminas=True)
    texto = identidade.pedido(post)
    assert "FIXOS da conta" in texto and "gravata amarela" in texto
    assert identidade.expandir("Gugu olhando o caixa", [ElementoVisual.objects.get()]).endswith(
        "Gugu: gorila de terno azul-marinho e gravata amarela"
    )

    cenas = []
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda chave, v, json_schema=None: cenas.append(v["cena"]) or json.dumps({"prompt": "x"}),
    )
    monkeypatch.setattr(fontes, "gerar_imagem", lambda prompt, tamanho="": b"")
    monkeypatch.setattr(identidade.laminas, "guardar_imagem_propria", lambda *a: "c.jpg")
    post.save()
    identidade.gerar_imagens(post)
    assert "gravata amarela" in cenas[0]
