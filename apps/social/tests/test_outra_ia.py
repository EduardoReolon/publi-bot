"""Segunda opiniao de uma IA grande: o pedido leva o que importa, a resposta
colada vira post, abordagem e evento — com previa — e o que veio dela fica
marcado para o comparativo."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.social import outra_ia, tasks
from apps.social.abordagens import garantir_abordagens
from apps.social.models import Abordagem, Destino, Post, Tema
from apps.social.tests.test_telas import _artigo_publicado

U = "core.urls_tenants"


def _resposta(artigo, tema) -> str:
    return f"""Achei a estrategia boa, mas falta conversa com quem ja trata.

**PROPOSTAS:**
- {outra_ia.codigo_do_tema(tema)} | Voce dorme 8 horas e acorda cansado? | tema com dor forte
- {outra_ia.codigo_do_artigo(artigo.pk)} | O exame que quase ninguem pede | artigo novo
- m-ffffff | codigo que nao existe | x

ABORDAGENS:
- Pergunta do consultorio: abra com a pergunta que o paciente mais faz na consulta, \
responda em uma frase e convide para o artigo.
- So: curta

## EVENTOS
- 2026-10-01 | Outubro Rosa | https://www.gov.br/saude/outubro-rosa | \
{outra_ia.codigo_do_artigo(artigo.pk)} | ligar cansaco a rotina de exames
- 2026-10-10 | Evento sem fonte | | {outra_ia.codigo_do_artigo(artigo.pk)} | x
COMENTARIOS:
Voces respondem os comentarios?
FIM
Espero ter ajudado!
"""


def _tema(artigo) -> Tema:
    return Tema.objects.create(
        titulo="Cansaco que nao passa com o sono",
        artigos=[str(artigo.pk)],
        artigo_principal=artigo.pk,
        nota=0.8,
        frases=[{"artigo_id": str(artigo.pk), "artigo_titulo": artigo.title, "frase": "x"}],
    )


@pytest.mark.django_db
def test_pedido_leva_negocio_placar_candidatos_e_pede_pesquisa_na_web(ambiente):
    artigo = _artigo_publicado()
    tema = _tema(artigo)
    garantir_abordagens()
    destino = Destino.objects.create(rede="instagram", nome="Insta", publico="Pacientes")
    Post.objects.create(
        destino=destino,
        motivo=Post.Motivo.HISTORICO,
        situacao=Post.Situacao.PUBLICADO,
        texto="Post antigo",
        extras={"gancho": "Post antigo que foi bem"},
        publicado_em=timezone.now() - timedelta(days=3),
        metricas={"alcance": 500, "salvos": 20},
        sucesso=True,
    )
    texto = outra_ia.pedido(destino)
    assert "PESQUISAR NA WEB" in texto and "nao invente" in texto
    assert outra_ia.codigo_do_tema(tema) in texto
    assert outra_ia.codigo_do_artigo(artigo.pk) in texto and "Cansaco que nao passa" in texto
    assert "Publico desta conta: Pacientes" in texto
    assert "E o meu caso" in texto  # abordagem com o placar
    assert "Post antigo que foi bem" in texto and "taxa 4.0%" in texto


@pytest.mark.django_db
def test_resposta_vira_previa_e_so_aplica_o_marcado(ambiente, monkeypatch):
    _, _, client = ambiente
    escritos = []
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: escritos.append(pk))
    artigo = _artigo_publicado()
    tema = _tema(artigo)
    destino = Destino.objects.create(rede="instagram", nome="Insta")

    leitura = outra_ia.ler(_resposta(artigo, tema))
    assert [p.alvo.codigo for p in leitura.propostas] == [
        outra_ia.codigo_do_tema(tema),
        outra_ia.codigo_do_artigo(artigo.pk),
    ]
    assert leitura.nao_achados and "m-ffffff" in leitura.nao_achados[0]
    assert [a.nome for a in leitura.abordagens] == ["Pergunta do consultorio"]  # "So: curta" fora
    assert len(leitura.eventos) == 1  # sem link: nao confirmado, fica de fora
    evento = leitura.eventos[0]
    assert evento.evento == "Outubro Rosa" and evento.alvo.artigo_id == str(artigo.pk)
    assert "respondem" in leitura.comentarios

    resposta = _resposta(artigo, tema)
    previa = client.post(
        reverse("social:revisar_resposta_ia", urlconf=U),
        {"destino": destino.pk, "resposta": resposta},
    ).content.decode()
    assert "Voce dorme 8 horas" in previa and "Outubro Rosa" in previa
    assert "Nada foi aplicado ainda" in previa
    assert not Post.objects.exists()

    with_commit = client.post(
        reverse("social:aplicar_resposta_ia", urlconf=U),
        {
            "destino": destino.pk,
            "resposta": resposta,
            "proposta": ["0"],  # so a do tema
            "abordagem": ["0"],
            "evento": ["0"],
        },
    )
    assert with_commit.status_code == 302
    posts = list(Post.objects.order_by("criado_em"))
    assert len(posts) == 2
    assert all(p.motivo == Post.Motivo.OUTRA_IA for p in posts)
    assert posts[0].tema == tema and "Voce dorme 8 horas" in posts[0].extras["ideia"]
    assert "Outubro Rosa" in posts[1].por_que and "gov.br" in posts[1].por_que
    nova = Abordagem.objects.get(nome="Pergunta do consultorio")
    assert nova.origem == Abordagem.Origem.OUTRA_IA and nova.redes == ["instagram"]
    assert "invente" in nova.instrucao


@pytest.mark.django_db
def test_resposta_sem_blocos_explica(ambiente):
    _, _, client = ambiente
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    resposta = client.post(
        reverse("social:revisar_resposta_ia", urlconf=U),
        {"destino": destino.pk, "resposta": "Ola! Que bom falar com voce."},
        follow=True,
    )
    assert "pode gerar" in resposta.content.decode()


@pytest.mark.django_db
def test_a_ideia_vai_para_quem_escreve(ambiente, monkeypatch):
    from apps.social import fontes, redacao

    artigo = _artigo_publicado()
    destino = Destino.objects.create(rede="linkedin", nome="Linkedin")
    post = Post.objects.create(
        destino=destino,
        artigo_id=artigo.pk,
        motivo=Post.Motivo.OUTRA_IA,
        extras={"ideia": "gancho sugerido: o exame que ninguem pede"},
    )
    pedidos = []

    def executar(chave, variaveis, **_):
        pedidos.append(variaveis["ajuste"])
        return '{"gancho": "Voce acorda cansado?", "texto": "Veja no artigo."}'

    monkeypatch.setattr(fontes, "executar", executar)
    redacao.escrever(post)
    post.refresh_from_db()
    assert "IDEIA SUGERIDA" in pedidos[0] and "o exame que ninguem pede" in pedidos[0]
    assert post.extras["ideia"].startswith("gancho sugerido")


@pytest.mark.django_db
def test_comparativo_da_outra_ia(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    da_ia = Abordagem.objects.create(nome="Da IA", instrucao="x", origem=Abordagem.Origem.OUTRA_IA)
    nossa = Abordagem.objects.create(nome="Nossa", instrucao="x")
    for motivo, abordagem, sucesso in [
        (Post.Motivo.OUTRA_IA, da_ia, True),
        (Post.Motivo.OUTRA_IA, da_ia, True),
        (Post.Motivo.NOVO, nossa, False),
        (Post.Motivo.NOVO, nossa, True),
    ]:
        Post.objects.create(
            destino=destino,
            motivo=motivo,
            abordagem=abordagem,
            situacao=Post.Situacao.PUBLICADO,
            sucesso=sucesso,
        )
    c = outra_ia.comparativo(destino)
    assert c["posts_ia"] == {"n": 2, "taxa": 100}
    assert c["posts_publibot"] == {"n": 2, "taxa": 50}
    assert c["abordagens_ia"]["taxa"] == 100 and c["abordagens_outras"]["taxa"] == 50
    assert c["tem_algo"]
