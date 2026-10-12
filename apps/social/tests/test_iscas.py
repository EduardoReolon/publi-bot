"""Iscas: 'comente QUERO e receba...' — reconhecer o pedido nos tres modos,
escolher a isca do post, garantir a chamada no texto, responder o comentario
com o link rastreado, painel e ideias da outra IA."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.social import comentarios, escolha, iscas, parametros
from apps.social.models import Comentario, ConfiguracaoSocial, Destino, Isca, Post
from apps.social.redes.base import ComentarioLido

U = "core.urls_tenants"


def _isca(**campos) -> Isca:
    return Isca.objects.create(
        **{
            "nome": "Conversa gratis de 15 minutos",
            "palavra": "QUERO",
            "resposta": "{nome}, que bom! Agende aqui: {link}",
            "link": "https://agenda.exemplo.com/x",
            **campos,
        }
    )


@pytest.mark.django_db
def test_reconhecer_nos_tres_modos(ambiente):
    aproximado = _isca()
    assert iscas.reconhecer(aproximado, "Quero!!! 🙏") == "sim"
    assert iscas.reconhecer(aproximado, "qeuro") == "sim"  # erro de digitacao
    assert iscas.reconhecer(aproximado, "me manda por favor") == "sim"
    assert iscas.reconhecer(aproximado, "Sim") == "talvez"
    assert iscas.reconhecer(aproximado, "🙋‍♀️") == "talvez"
    assert iscas.reconhecer(aproximado, "@fulana olha isso") == "nao"
    assert iscas.reconhecer(aproximado, "Muito bom o conteudo, parabens") == "nao"

    palavra = _isca(nome="Guia", palavra="FOOD", modo=Isca.Modo.PALAVRA)
    assert iscas.reconhecer(palavra, "food 😍") == "sim"
    assert iscas.reconhecer(palavra, "fod") == "nao"
    assert iscas.reconhecer(palavra, "quero") == "nao"

    todos = _isca(nome="Aula", modo=Isca.Modo.TODOS)
    assert iscas.reconhecer(todos, "qualquer coisa") == "sim"


@pytest.mark.django_db
def test_isca_entra_em_parte_dos_posts_de_engajamento(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    assert iscas.escolher(destino, "engajamento") is None  # nenhuma cadastrada
    a = _isca(nome="A")
    b = _isca(nome="B", criado_em=timezone.now())
    assert iscas.escolher(destino, "alcance") is None
    primeira = iscas.escolher(destino, "engajamento")
    assert primeira == a
    Post.objects.create(destino=destino, motivo="novo", isca=a, extras={"foco": "engajamento"})
    # 50% por padrao: depois de um com isca, o proximo vai sem.
    assert iscas.escolher(destino, "engajamento") is None
    Post.objects.create(destino=destino, motivo="novo", extras={"foco": "engajamento"})
    assert iscas.escolher(destino, "engajamento") == b  # reveza
    config = ConfiguracaoSocial.carregar()
    config.parametros = {"iscas_por_cento": 0}
    config.save()
    assert parametros.valor("iscas_por_cento") == 0
    assert iscas.escolher(destino, "engajamento") is None

    post = Post(destino=destino, isca=a)
    assert "QUERO" in iscas.instrucao(post)
    assert iscas.garantir_chamada(post, "Texto do post.").endswith(
        "Comente QUERO que eu te mando: A."
    )
    assert iscas.garantir_chamada(post, "Comente quero aqui!") == "Comente quero aqui!"


class _Rede:
    def __init__(self, lidos):
        self.lidos, self.respostas = lidos, []
        dono = self

        class Publicador:
            def __init__(self, destino, http=None):
                pass

            def ler_comentarios(self, post):
                return dono.lidos

            def responder(self, post, id_do_comentario, texto):
                dono.respostas.append((id_do_comentario, texto))
                return f"r-{id_do_comentario}"

        self.publicador = Publicador


@pytest.mark.django_db
def test_comentario_que_pede_recebe_a_resposta_com_o_link(ambiente, monkeypatch):
    _, _, client = ambiente
    rede = _Rede(
        [
            ComentarioLido("c1", "QUERO!", autor="Ana Souza"),
            ComentarioLido("c2", "sim", autor="Bia"),
            ComentarioLido("c3", "Que legal", autor="Caio"),
        ]
    )
    falsa = SimpleNamespace(publicador=rede.publicador)
    monkeypatch.setattr(comentarios, "rede", lambda codigo: falsa)
    monkeypatch.setattr("apps.social.redes.rede", lambda codigo: falsa)
    destino = Destino.objects.create(rede="instagram", nome="Insta", credenciais=b"x", conta_id="1")
    isca = _isca()
    Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao="publicado",
        publicado_em=timezone.now(),
        id_remoto="m1",
        isca=isca,
        extras={"foco": "engajamento"},
    )
    assert iscas.responder() == 1
    [(id_remoto, texto)] = rede.respostas
    assert id_remoto == "c1" and texto.startswith("Ana, que bom!")
    assert f"/redes/i/{isca.chave_publica}/" in texto
    assert Comentario.objects.get(id_remoto="c2").pedido == "talvez"

    # O talvez espera no painel; um clique responde.
    pagina = client.get(reverse("social:iscas", urlconf=U)).content.decode()
    assert "Esperando voce" in pagina and "Conversa gratis" in pagina
    talvez = Comentario.objects.get(id_remoto="c2")
    client.post(reverse("social:iscas", urlconf=U), {"acao": "responder", "comentario": talvez.pk})
    assert len(rede.respostas) == 2
    linha = iscas.painel()[0]
    assert linha["posts"] == 1 and linha["pedidos"] == 2 and linha["respondidos"] == 2

    # O link rastreado conta e segue para a entrega.
    resposta = client.get(f"/redes/i/{isca.chave_publica}/")
    assert resposta.status_code == 302 and resposta["Location"] == isca.link
    isca.refresh_from_db()
    assert isca.cliques == 1


@pytest.mark.django_db
def test_ideias_da_outra_ia_entram_paradas_e_a_proposta_cita_a_isca(ambiente, monkeypatch):
    _, _, client = ambiente
    assert "ISCAS:" in iscas.pedido()
    resposta = """Pensei em duas.

ISCAS:
- Checklist do sono | sono! | 10 perguntas para levar a consulta. | {nome}, aqui: {link}
- Aula ao vivo mensal | AULA | 30 minutos, perguntas abertas.
COMENTARIOS:
Prepare o PDF antes.
FIM
"""
    client.post(reverse("social:iscas", urlconf=U), {"acao": "resposta_ia", "resposta": resposta})
    checklist = Isca.objects.get(nome="Checklist do sono")
    assert not checklist.ativa and checklist.palavra == "SONO"
    assert checklist.origem == Isca.Origem.OUTRA_IA
    assert Isca.objects.count() == 2

    # A outra IA cita a isca pelo nome na proposta (5o campo).
    checklist.ativa = True
    checklist.save()
    criados = []
    monkeypatch.setattr(escolha, "sugerir", lambda *a, **k: criados.append(k.get("isca")) or [])
    from apps.social import outra_ia
    from apps.social.tests.test_outra_ia import _tema
    from apps.social.tests.test_telas import _artigo_publicado

    tema = _tema(_artigo_publicado())
    leitura = outra_ia.ler(
        f"PROPOSTAS:\n- {outra_ia.codigo_do_tema(tema)} | gancho | porque | engajamento "
        "| checklist do sono\nFIM\n"
    )
    assert leitura.propostas[0].isca == checklist
