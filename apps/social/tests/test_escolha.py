"""Qual artigo vai para qual conta (regras), quando (agenda) e com qual
abordagem (sorteio que aprende com o resultado)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.social import escolha, experimentos, fontes
from apps.social.abordagens import garantir_abordagens
from apps.social.models import Abordagem, ConfiguracaoSocial, Destino, Post
from apps.social.tests.test_escrita import ARTIGO, VetoresFalsos


@pytest.fixture
def nucleo(monkeypatch):
    artigos = {ARTIGO.id: ARTIGO}
    sinais = {"quase_la": False, "conversoes": 0}
    monkeypatch.setattr(fontes, "artigos_no_ar", lambda desde=None: list(artigos))
    monkeypatch.setattr(fontes, "artigo", lambda pk: artigos.get(str(pk)))
    monkeypatch.setattr(fontes, "sinais", lambda artigo: dict(sinais))
    monkeypatch.setattr(fontes, "vetores", VetoresFalsos())
    monkeypatch.setattr(fontes, "negocio", lambda: {"dores": [], "regioes": ["Curitiba"]})
    escritos = []
    from apps.social import tasks

    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: escritos.append(pk))
    return artigos, sinais, escritos


def _quando(dia: int, hora: str) -> datetime:
    h, m = (int(x) for x in hora.split(":"))
    return timezone.make_aware(datetime(2026, 10, dia, h, m))


@pytest.mark.django_db
def test_agenda_respeita_dias_horarios_e_teto(ambiente):
    destino = Destino.objects.create(
        rede="linkedin", nome="Perfil", dias=[0, 2], horarios=["08:30", "18:00"], teto_semanal=2
    )
    # Segunda-feira 05/10/2026, 09:00: o proximo e segunda as 18:00.
    segunda = _quando(5, "09:00")
    primeiro = escolha.proximo_horario(destino, segunda)
    assert primeiro == _quando(5, "18:00")
    Post.objects.create(
        destino=destino, motivo="novo", situacao=Post.Situacao.APROVADO, agendado_para=primeiro
    )
    segundo = escolha.proximo_horario(destino, segunda)
    assert segundo == _quando(7, "08:30")  # quarta
    Post.objects.create(
        destino=destino, motivo="novo", situacao=Post.Situacao.APROVADO, agendado_para=segundo
    )
    # Teto de 2 na semana: so na segunda seguinte.
    assert escolha.proximo_horario(destino, segunda) == _quando(12, "08:30")


@pytest.mark.django_db
def test_rodada_sugere_artigo_novo_uma_vez_por_conta(
    ambiente, nucleo, django_capture_on_commit_callbacks
):
    _artigos, sinais, escritos = nucleo
    garantir_abordagens()
    config = ConfiguracaoSocial.carregar()
    linkedin = Destino.objects.create(rede="linkedin", nome="Perfil", publico="adultos cansados")
    Destino.objects.create(rede="gmn", nome="Google", ligado=False)

    assert escolha.rodada() == 0  # "sugerir sozinho" desligado
    config.ligado = True
    config.save()
    with django_capture_on_commit_callbacks(execute=True):
        assert escolha.rodada() == 1
    post = Post.objects.get()
    assert post.destino == linkedin and post.motivo == Post.Motivo.NOVO
    assert "Artigo novo" in post.por_que and "dado" in post.por_que
    assert escritos == [str(post.pk)]
    # Ja esperando revisao: nao empilha; e o mesmo artigo nao volta tao cedo.
    assert escolha.rodada() == 0
    post.situacao = Post.Situacao.PUBLICADO
    post.save()
    assert escolha.rodada() == 0

    # Converte, e o ultimo post foi ha mais de 3 meses: volta, com o motivo.
    Post.objects.filter(pk=post.pk).update(criado_em=timezone.now() - timedelta(days=120))
    sinais["conversoes"] = 2
    assert escolha.rodada() == 1
    assert Post.objects.latest("criado_em").motivo == Post.Motivo.CONVERTE


@pytest.mark.django_db
def test_duas_versoes_com_abordagens_diferentes(ambiente, nucleo):
    garantir_abordagens()
    config = ConfiguracaoSocial.carregar()
    config.variantes = 2
    config.save()
    destino = Destino.objects.create(rede="instagram", nome="Insta")

    a, b = escolha.levar_as_redes(ARTIGO.id)

    assert a.abordagem != b.abordagem and b.variante_de == a
    assert escolha.levar_as_redes(ARTIGO.id) == []  # espacamento do mesmo artigo
    assert destino.posts.count() == 2


@pytest.mark.django_db
def test_sorteio_favorece_quem_funciona_sem_parar_de_testar(ambiente):
    garantir_abordagens()
    destino = Destino.objects.create(rede="linkedin", nome="Perfil")
    boa = Abordagem.objects.get(nome="Nossa, que incrivel")
    ruim = Abordagem.objects.get(nome="Mito ou verdade")
    for _ in range(8):
        Post.objects.create(destino=destino, motivo="novo", abordagem=boa, sucesso=True)
        Post.objects.create(destino=destino, motivo="novo", abordagem=ruim, sucesso=False)
    rng = random.Random(7)  # noqa: S311 - sorteio de teste, nao segredo
    vezes = {}
    for _ in range(300):
        [escolhida] = experimentos.escolher(destino, rng=rng)
        vezes[escolhida.nome] = vezes.get(escolhida.nome, 0) + 1
    assert vezes.get("Nossa, que incrivel", 0) > vezes.get("Mito ou verdade", 0) * 5
    # As que nunca foram medidas continuam aparecendo.
    assert len(vezes) >= 4
    quadro = {linha["abordagem"].nome: linha for linha in experimentos.quadro(destino)}
    assert quadro["Nossa, que incrivel"]["taxa"] == 100
    assert quadro["Mito ou verdade"]["taxa"] == 0
    assert quadro["E o meu caso"]["taxa"] is None


@pytest.mark.django_db
def test_funcionou_e_acima_da_mediana_da_propria_conta(ambiente):
    destino = Destino.objects.create(rede="linkedin", nome="Perfil")
    antigo = timezone.now() - timedelta(days=10)
    posts = [
        Post.objects.create(
            destino=destino,
            motivo="novo",
            situacao=Post.Situacao.PUBLICADO,
            publicado_em=antigo,
            cliques=cliques,
        )
        for cliques in (2, 10, 30)
    ]
    recente = Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao=Post.Situacao.PUBLICADO,
        publicado_em=timezone.now(),
        cliques=99,
    )

    experimentos.avaliar(destino)

    assert [Post.objects.get(pk=p.pk).sucesso for p in posts] == [False, False, True]
    assert Post.objects.get(pk=recente.pk).sucesso is None  # menos de 7 dias no ar


@pytest.mark.django_db
def test_descartado_nao_volta_sozinho_mas_volta_a_pedido(ambiente, nucleo):
    garantir_abordagens()
    config = ConfiguracaoSocial.carregar()
    config.ligado = True
    config.save()
    destino = Destino.objects.create(rede="linkedin", nome="Perfil")
    Post.objects.create(
        destino=destino, artigo_id=ARTIGO.id, motivo="novo", situacao=Post.Situacao.DESCARTADO
    )
    assert escolha.rodada() == 0
    assert len(escolha.levar_as_redes(ARTIGO.id)) == 1
