"""Cialdini nas redes: o principal reveza, quem escreve recebe a lista honesta,
o post diz quais usou (e o algoritmo confirma) e o quadro mede cada um."""

from __future__ import annotations

import json

import pytest

from apps.social import fontes, persuasao, redacao
from apps.social.models import Destino, Post
from apps.social.tests.test_clareza import LAMINAS_CLARAS
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401


@pytest.mark.django_db
def test_principal_reveza_e_so_o_que_o_material_permite(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    post = Post.objects.create(destino=destino, motivo="novo")
    assert "prova_social" not in persuasao.candidatos(post, {"achados": []})
    assert "prova_social" in persuasao.candidatos(post, {"achados": ["38% dos adultos"]})
    primeiro = persuasao.principal_da_vez(post, {})
    Post.objects.create(destino=destino, motivo="novo", extras={"principio": primeiro})
    assert persuasao.principal_da_vez(post, {}) != primeiro
    texto = persuasao.instrucao(Post(destino=destino, extras={"principio": "escassez"}))
    assert "PRINCIPAL deste post e ESCASSEZ" in texto and "so hoje" in texto


@pytest.mark.django_db
def test_post_registra_os_principios_e_o_quadro_mede(
    ambiente,
    modulo,  # noqa: F811
    settings,
    tmp_path,
    monkeypatch,
):
    settings.MEDIA_ROOT = tmp_path
    pedidos = []

    def executar(chave, variaveis, json_schema=None):
        if chave == "social_revisor":
            return json.dumps({"entendi": "ok", "nota": 9})
        pedidos.append(variaveis)
        return json.dumps(
            {
                "principios": ["afinidade", "inventado"],
                "gancho": "Cansado?",
                "texto": "Comente aqui se acontece com voce.",
                "laminas": LAMINAS_CLARAS,
            }
        )

    monkeypatch.setattr(fontes, "executar", executar)
    destino = _destino("instagram")
    post = Post.objects.create(destino=destino, artigo_id=ARTIGO.id, motivo="novo")
    redacao.escrever(post)
    post.refresh_from_db()
    assert "PERSUASAO (Cialdini" in pedidos[0]["ajuste"]
    usados = set(post.extras["principios"])
    # declarado (afinidade) + principal + detectados: numero (prova social),
    # pedido de comentario (compromisso); o inventado sai.
    assert {"afinidade", "prova_social", "compromisso", post.extras["principio"]} <= usados
    assert "inventado" not in usados
    assert any(principal for _nome, principal in post.principios_usados)

    for n, alcance in enumerate((100, 200, 300, 400, 500, 600)):
        Post.objects.create(
            destino=destino,
            motivo="novo",
            situacao="publicado",
            metricas={"alcance": 1000, "salvos": alcance},
            cliques=alcance,
            extras={"principios": ["autoridade"] if n % 2 else []},
        )
    linha = next(x for x in persuasao.quadro(destino) if x["chave"] == "autoridade")
    assert linha["com"] == 3 and linha["sem"] >= 3 and linha["diferenca"] is not None
