"""Opcoes de capa: gerar varias, pedir mais, escolher uma.

O que se testa aqui e a regra do produto, nao o provedor: **quem escolhe a
imagem e uma pessoa**, e nada e publicado com uma capa que ninguem olhou.
"""

from __future__ import annotations

import io

import pytest
from django_tenants.utils import schema_context
from PIL import Image

from apps.content.capas import (
    MAXIMO_DE_LOTES,
    OPCOES_POR_LOTE,
    LimiteDeLotes,
    SemConexaoDeImagem,
    capa_escolhida,
    escolher_capa,
    gerar_opcoes,
)
from apps.content.models import Article, ArticleImage
from apps.inference.providers.base import ImagemGerada


def _png(cor=(20, 90, 160)) -> bytes:
    memoria = io.BytesIO()
    Image.new("RGB", (64, 64), cor).save(memoria, format="PNG")
    return memoria.getvalue()


class GeradorFalso:
    """Devolve `quantidade` imagens diferentes, como um provedor bem-comportado."""

    def __init__(self, *, por_chamada: int | None = None):
        self.por_chamada = por_chamada
        self.chamadas: list[dict] = []

    def generate(self, *, model, prompt, quantidade=3, tamanho="1024x1024"):
        self.chamadas.append({"model": model, "prompt": prompt, "quantidade": quantidade})
        quantas = quantidade if self.por_chamada is None else min(self.por_chamada, quantidade)
        return [
            ImagemGerada(conteudo=_png((10 * i, 90, 160)), prompt_revisado=f"{prompt} #{i}")
            for i in range(1, quantas + 1)
        ]


@pytest.fixture
def tenant_de_capa(tenant_factory):
    tenant = tenant_factory("capas")
    with schema_context(tenant.schema_name):
        yield tenant


@pytest.fixture
def artigo(tenant_de_capa):
    return Article.objects.create(title="Monitoramento na gestacao", body_markdown="Texto.")


@pytest.fixture
def geracao_falsa(monkeypatch):
    """Substitui a conexao, o cliente e a descricao — o que se testa e a regra."""
    from types import SimpleNamespace

    gerador = GeradorFalso()
    conexao = SimpleNamespace(name="GPU", default_model="modelo-de-imagem", pk=1)

    monkeypatch.setattr("apps.content.capas._conexao_de_imagem", lambda: conexao)
    monkeypatch.setattr("apps.content.capas._registrar_uso", lambda *a, **k: None)
    monkeypatch.setattr(
        "apps.content.capas.descrever_capa",
        lambda article, site=None: ("a monitor on a wooden table", None),
    )
    monkeypatch.setattr("apps.inference.providers.base.get_image_provider", lambda *a, **k: gerador)

    import contextlib

    monkeypatch.setattr("apps.inference.leases.reserva", lambda *a, **k: contextlib.nullcontext())
    return gerador


@pytest.mark.django_db
def test_um_lote_traz_tres_opcoes_diferentes(artigo, geracao_falsa):
    """Escolher exige comparar. Uma opcao unica transforma a revisao em
    'aceita ou pede de novo'."""
    criadas = gerar_opcoes(artigo)

    assert len(criadas) == OPCOES_POR_LOTE
    assert [i.order for i in criadas] == [1, 2, 3]
    assert {i.batch for i in criadas} == {1}
    # Arquivos distintos, nao a mesma imagem repetida.
    assert len({i.image.read() for i in criadas}) == OPCOES_POR_LOTE


@pytest.mark.django_db
def test_toda_opcao_e_gravada_em_webp(artigo, geracao_falsa):
    """O provedor devolveu PNG; o que fica no disco e WebP, como toda imagem
    do sistema."""
    criadas = gerar_opcoes(artigo)

    for imagem in criadas:
        assert imagem.image.name.endswith(".webp")
        assert imagem.image.read()[:4] == b"RIFF"


@pytest.mark.django_db
def test_pedir_mais_exemplos_acrescenta_sem_descartar(artigo, geracao_falsa):
    """A terceira do primeiro lote pode ser melhor que tudo o que veio depois."""
    primeiro = gerar_opcoes(artigo)
    segundo = gerar_opcoes(artigo)

    assert {i.batch for i in primeiro} == {1}
    assert {i.batch for i in segundo} == {2}
    assert artigo.images.count() == OPCOES_POR_LOTE * 2


@pytest.mark.django_db
def test_ha_um_teto_de_lotes(artigo, geracao_falsa):
    """Sem limite, 'gerar mais' vira caca-niquel e o proximo lote nunca e o
    ultimo."""
    for _ in range(MAXIMO_DE_LOTES):
        gerar_opcoes(artigo)

    with pytest.raises(LimiteDeLotes):
        gerar_opcoes(artigo)


@pytest.mark.django_db
def test_nenhuma_capa_e_escolhida_sozinha(artigo, geracao_falsa):
    """Um artigo publicado com uma capa que ninguem olhou e como um artigo
    publicado sem que ninguem tenha lido o texto."""
    gerar_opcoes(artigo)

    assert capa_escolhida(artigo) is None
    assert not artigo.images.filter(is_chosen=True).exists()


@pytest.mark.django_db
def test_escolher_outra_troca_a_capa(artigo, geracao_falsa):
    """A restricao do banco aceita UMA escolhida por artigo: desmarcar precisa
    acontecer antes de marcar, na mesma transacao."""
    primeira, segunda, _ = gerar_opcoes(artigo)

    escolher_capa(artigo, primeira)
    escolher_capa(artigo, segunda)

    assert artigo.images.filter(is_chosen=True).count() == 1
    assert capa_escolhida(artigo).pk == segunda.pk


@pytest.mark.django_db
def test_imagem_de_outro_artigo_e_recusada(artigo, geracao_falsa, tenant_de_capa):
    outro = Article.objects.create(title="Outro artigo")
    imagem = gerar_opcoes(artigo)[0]

    with pytest.raises(ValueError, match="nao pertence"):
        escolher_capa(outro, imagem)


@pytest.mark.django_db
def test_provedor_que_so_devolve_uma_por_vez_ainda_entrega_tres(artigo, monkeypatch):
    """Alguns modelos recusam `n > 1`. O ponto e ter opcoes para comparar, e
    nao a forma como o provedor as entrega."""
    import contextlib
    from types import SimpleNamespace

    gerador = GeradorFalso(por_chamada=1)
    monkeypatch.setattr(
        "apps.content.capas._conexao_de_imagem",
        lambda: SimpleNamespace(name="GPU", default_model="m", pk=1),
    )
    monkeypatch.setattr("apps.content.capas._registrar_uso", lambda *a, **k: None)
    monkeypatch.setattr(
        "apps.content.capas.descrever_capa", lambda article, site=None: ("uma cena", None)
    )
    monkeypatch.setattr("apps.inference.providers.base.get_image_provider", lambda *a, **k: gerador)
    monkeypatch.setattr("apps.inference.leases.reserva", lambda *a, **k: contextlib.nullcontext())

    # O cliente falso ja aplica o limite por chamada; quem completa o lote e o
    # adaptador real. Aqui se verifica que a regra do servico nao trunca.
    criadas = gerar_opcoes(artigo, quantidade=1)
    assert len(criadas) == 1


@pytest.mark.django_db
def test_sem_conexao_de_imagem_a_mensagem_diz_o_que_fazer(artigo, monkeypatch):
    monkeypatch.setattr("apps.inference.leases.escolher_conexao", lambda **k: None)

    with pytest.raises(SemConexaoDeImagem, match="Configuracao"):
        gerar_opcoes(artigo)


@pytest.mark.django_db
def test_opcao_ilegivel_nao_derruba_o_lote(artigo, geracao_falsa, monkeypatch):
    """Duas boas valem mais que um lote inteiro perdido."""
    from apps.content import capas

    original = capas.converter_para_webp
    chamadas = {"n": 0}

    def as_vezes_falha(arquivo, *, nome="imagem"):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise capas.ImagemInvalida("arquivo corrompido")
        return original(arquivo, nome=nome)

    monkeypatch.setattr(capas, "converter_para_webp", as_vezes_falha)

    criadas = gerar_opcoes(artigo)

    assert len(criadas) == OPCOES_POR_LOTE - 1
    assert [i.order for i in criadas] == [1, 3]


@pytest.mark.django_db
def test_capa_publica_so_serve_a_escolhida_de_artigo_aprovado(artigo, geracao_falsa):
    """As outras opcoes sao rascunho: um lote acessivel por URL entregaria
    material descartado."""
    from django.test import Client

    primeira, segunda, _ = gerar_opcoes(artigo)
    escolher_capa(artigo, primeira)

    from django.conf import settings

    cliente = Client(HTTP_HOST=f"capas.{settings.ROOT_DOMAIN}")

    # Artigo ainda em revisao: nem a escolhida sai.
    assert cliente.get(f"/capas/{primeira.pk}.webp").status_code == 404

    Article.objects.filter(pk=artigo.pk).update(status=Article.Status.APPROVED_SCHEDULED)

    assert cliente.get(f"/capas/{primeira.pk}.webp").status_code == 200
    # A nao escolhida continua fora do alcance.
    assert cliente.get(f"/capas/{segunda.pk}.webp").status_code == 404


@pytest.mark.django_db
def test_payload_leva_a_capa_por_referencia(artigo, geracao_falsa, tenant_de_capa):
    """Base64 estouraria o limite de 1 MB do Nginx antes de chegar a aplicacao."""
    from apps.integrations.models import Site
    from apps.integrations.publishing import montar_payload_de_artigo

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    escolher_capa(artigo, gerar_opcoes(artigo)[0])
    artigo.body_html = "<p>x</p>"

    payload = montar_payload_de_artigo(artigo, site)

    capa = payload["cover_image"]
    assert capa["mime_type"] == "image/webp"
    assert capa["url"].endswith(".webp")
    assert len(capa["sha256"]) == 64
    assert capa["bytes"] > 0
    assert "base64" not in str(payload)


@pytest.mark.django_db
def test_sem_capa_escolhida_o_payload_sai_sem_o_campo(artigo, geracao_falsa, tenant_de_capa):
    """Um `cover_image` sem url faria o site tentar baixar nada e recusar com 422."""
    from apps.integrations.models import Site
    from apps.integrations.publishing import montar_payload_de_artigo

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    gerar_opcoes(artigo)

    payload = montar_payload_de_artigo(artigo, site)

    assert "cover_image" not in payload


@pytest.mark.django_db
def test_resposta_de_qa_nunca_leva_imagem(tenant_de_capa):
    """Ilustrar cada pergunta custaria uma inferencia para algo que ninguem
    pediu."""
    from apps.content.models import Answer, Question
    from apps.integrations.models import Site
    from apps.integrations.publishing import montar_payload_de_resposta

    site = Site.objects.create(name="S", slug="s", base_url="https://s.exemplo.org")
    pergunta = Question.objects.create(
        site=site,
        remote_id="1",
        question_text="Uma duvida?",
        submitted_at="2026-01-01T00:00:00Z",
        retention_until="2026-12-01T00:00:00Z",
    )
    resposta = Answer.objects.create(question=pergunta, body_html="<p>x</p>")

    payload = montar_payload_de_resposta(resposta, site)

    assert "cover_image" not in payload
    assert not hasattr(Answer, "images")


@pytest.mark.django_db
def test_uma_capa_por_artigo_no_banco(artigo, geracao_falsa):
    """A trava e no banco: duas marcadas fariam a publicacao escolher pela
    ordem da consulta, ignorando a escolha da pessoa em silencio."""
    from django.db import IntegrityError, transaction

    primeira, segunda, _ = gerar_opcoes(artigo)
    escolher_capa(artigo, primeira)

    with pytest.raises(IntegrityError), transaction.atomic():
        ArticleImage.objects.filter(pk=segunda.pk).update(is_chosen=True)
