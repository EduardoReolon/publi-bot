"""Material proprio: caso real, novidade e banco de fotos.

Fotos de verdade geradas aqui (Pillow): nitidez, brilho, quase iguais,
atendimento, recorte por rede e a localizacao que nunca sai. O resto e o
caminho do post: da Entrada a redacao (so o que a pessoa contou), as
imagens, a publicacao (reels; video no LinkedIn fica para copiar) e a
mistura artigos x fotos de cada conta."""

from __future__ import annotations

import io
import json
from datetime import timedelta

import httpx
import numpy as np
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.social import escolha, fontes, fotos, proprio, publicacao, redacao, tasks
from apps.social.models import ConfiguracaoSocial, Destino, Entrada, Midia, Post
from apps.social.redes.base import ImagemPublica, SemSuporte
from apps.social.redes.instagram import PublicadorInstagram
from apps.social.redes.linkedin import PublicadorLinkedIn
from apps.social.tests.test_apis import Rede, _conectado, _post

U = "core.urls_tenants"


def _jpeg(
    *, largura=800, altura=1000, ruido=True, cor=128, quando="2026:03:10 10:00:00", semente=1
) -> bytes:
    rng = np.random.default_rng(semente)
    if ruido:
        pixels = rng.integers(0, 255, (altura, largura, 3), dtype=np.uint8)
    else:
        pixels = np.full((altura, largura, 3), cor, dtype=np.uint8)
    imagem = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[306] = quando
    exif.get_ifd(0x8769)[36867] = quando
    exif[0x8825] = {1: "S", 2: (25.0, 26.0, 27.0)}  # GPS: nunca pode sair daqui
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", exif=exif)
    return saida.getvalue()


def _arquivo(nome="foto.jpg", **kw) -> SimpleUploadedFile:
    return SimpleUploadedFile(nome, _jpeg(**kw), content_type="image/jpeg")


@pytest.fixture
def midia_no_disco(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


# -- Fotos ---------------------------------------------------------------------------------
@pytest.mark.django_db
def test_foto_sem_metadados_com_horario_e_medidas(ambiente, midia_no_disco):
    midia = fotos.receber(_arquivo(), banco=True, nota="degrade")
    assert midia.tirada_em is not None and timezone.localtime(midia.tirada_em).hour == 10
    assert midia.nitidez > 1000 and 100 < midia.brilho < 160
    assert len(midia.assinatura) == 16
    with midia.arquivo.open("rb") as f:
        gravada = Image.open(f)
        gravada.load()
    assert not gravada.getexif()  # sem data, sem GPS
    assert gravada.size == (800, 1000)


@pytest.mark.django_db
def test_tremida_escura_e_repetida_saem_e_atendimentos_viram_grupo(ambiente, midia_no_disco):
    feito = proprio.receber_no_banco(
        [
            _arquivo(quando="2026:03:10 10:00:00", semente=1),
            _arquivo(quando="2026:03:10 10:01:00", semente=1),  # a mesma cena
            _arquivo(quando="2026:03:10 10:05:00", semente=2),
            _arquivo(quando="2026:03:10 15:00:00", semente=3),
            _arquivo(ruido=False, cor=128, quando="2026:03:10 15:02:00"),  # lisa = tremida
            _arquivo(ruido=False, cor=10, quando="2026:03:10 15:03:00"),  # escura
        ],
        autorizada=True,
    )
    assert feito["recebidas"] == 6 and feito["ruins"] == 2 and feito["repetidas"] == 1
    motivos = list(Midia.objects.exclude(motivo="").values_list("motivo", flat=True))
    assert any("tremida" in m for m in motivos) and any("quase igual" in m for m in motivos)

    boas = Midia.objects.exclude(situacao=Midia.Situacao.DESCARTADA)
    assert boas.count() == 3
    # 10:00 e 10:05 (mesmo atendimento) x 15:00.
    assert len({m.grupo for m in boas}) == 2


@pytest.mark.django_db
def test_recorte_por_rede(ambiente, midia_no_disco):
    larga = fotos.receber(_arquivo(largura=2000, altura=800), banco=False)

    def proporcao(dados):
        imagem = Image.open(io.BytesIO(dados))
        return imagem.width / imagem.height

    assert proporcao(fotos.versao_para(larga, "instagram")) == pytest.approx(1.91, abs=0.02)
    assert proporcao(fotos.versao_para(larga, "instagram", carrossel=True)) == pytest.approx(
        0.8, abs=0.02
    )
    assert proporcao(fotos.versao_para(larga, "gmn")) == pytest.approx(1.78, abs=0.02)


# -- Caso real ---------------------------------------------------------------------------------
def _destinos():
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    linkedin = Destino.objects.create(rede="linkedin", nome="Linkedin")
    return insta, linkedin


@pytest.mark.django_db
def test_caso_real_pede_autorizacao_e_vira_post_em_cada_conta(ambiente, monkeypatch):
    escritos = []
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: escritos.append(pk))
    insta, linkedin = _destinos()
    with pytest.raises(proprio.EntradaInvalida, match="autorizou"):
        proprio.criar(tipo="caso", texto="Um caso", arquivos=[], destinos=[insta.pk])
    with pytest.raises(proprio.EntradaInvalida, match="conta"):
        proprio.criar(tipo="novidade", texto="x", arquivos=[], destinos=[])

    entrada = proprio.criar(
        tipo="caso",
        texto="Um cliente estava com o estoque parado. Em 3 semanas voltou a vender.",
        arquivos=[],
        artigo="",
        link="https://site.exemplo.org/contato",
        autorizado=True,
        destinos=[insta.pk, linkedin.pk],
    )
    posts = list(entrada.posts.all())
    assert {p.destino for p in posts} == {insta, linkedin}
    assert all(p.motivo == Post.Motivo.CASO and p.artigo_id is None for p in posts)
    assert all(p.artigo_url == "https://site.exemplo.org/contato" for p in posts)


@pytest.mark.django_db
def test_redacao_do_caso_so_com_o_relato_e_avisa_identificacao(ambiente, monkeypatch):
    insta, _ = _destinos()
    entrada = Entrada.objects.create(
        tipo="caso",
        texto="Um cliente estava com o estoque parado. Em 3 semanas voltou a vender.",
        link="https://site.exemplo.org/contato",
        autorizado=True,
    )
    post = Post.objects.create(
        destino=insta, entrada=entrada, motivo=Post.Motivo.CASO, artigo_url=entrada.link
    )
    pedidos = []

    def executar(chave, variaveis, **_):
        pedidos.append(variaveis)
        return json.dumps(
            {
                "gancho": "Dona Maria, 54 anos, voltou a vender em 3 semanas",
                "texto": "Em 40% menos tempo. Link na bio.",
                "laminas": [{"titulo": "Estoque parado", "texto": "e agora?"}],
            }
        )

    monkeypatch.setattr(fontes, "executar", executar)
    redacao.escrever(post)
    post.refresh_from_db()

    assert "NAO E DE UM ARTIGO" in pedidos[0]["ajuste"] and "CASO REAL" in pedidos[0]["ajuste"]
    assert "estoque parado" in pedidos[0]["resumo"]
    assert any("40" in a for a in post.avisos)  # numero que a pessoa nao contou
    assert any("idade" in a for a in post.avisos) and any("nome" in a for a in post.avisos)
    # Sem foto, o Instagram ganha laminas com o texto.
    assert post.extras["laminas"] and post.imagens


@pytest.mark.django_db
def test_novidade_com_fotos_usa_as_fotos_e_serve_jpeg(ambiente, monkeypatch, midia_no_disco):
    _, _, client = ambiente
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    insta, linkedin = _destinos()
    entrada = proprio.criar(
        tipo="novidade",
        texto="Chegou a cadeira nova.",
        arquivos=[_arquivo(semente=1), _arquivo(semente=2), _arquivo(semente=3)],
        artigo="",
        autorizado=True,
        destinos=[insta.pk, linkedin.pk],
    )
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda *a, **k: '{"gancho": "Cadeira nova", "texto": "Venha ver.", "laminas": []}',
    )
    for post in entrada.posts.all():
        redacao.escrever(post)
        post.refresh_from_db()
        assert not any("lamina" in a for a in post.avisos)
        assert all(i["tipo"] == "foto" for i in post.imagens)
        esperado = 3 if post.destino.rede == "instagram" else 1
        assert len(post.imagens) == esperado
    post = entrada.posts.get(destino=insta)
    resposta = client.get(reverse("social:midia_publica", args=[post.chave_publica, 2], urlconf=U))
    assert resposta.status_code == 200 and resposta["Content-Type"] == "image/jpeg"


@pytest.mark.django_db
def test_audio_transcrito_cria_os_posts(ambiente, monkeypatch, midia_no_disco):
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    monkeypatch.setattr(tasks.transcrever_entrada, "delay", lambda *a: None)
    insta, _ = _destinos()
    audio = SimpleUploadedFile("caso.ogg", b"OggS...", content_type="audio/ogg")
    entrada = proprio.criar(
        tipo="caso", texto="", arquivos=[], audio=audio, autorizado=True, destinos=[insta.pk]
    )
    assert entrada.situacao_do_audio == Entrada.Transcricao.ESPERANDO
    assert not entrada.posts.exists()

    def ocupado(*a, **k):
        raise fontes.TranscricaoAdiada("placa ocupada")

    monkeypatch.setattr(fontes, "transcrever", ocupado)
    with pytest.raises(fontes.TranscricaoAdiada):
        proprio.transcrever(entrada)

    monkeypatch.setattr(fontes, "transcrever", lambda *a, **k: "Atendi um cliente hoje.")
    proprio.transcrever(entrada)
    entrada.refresh_from_db()
    assert entrada.situacao_do_audio == Entrada.Transcricao.PRONTA
    assert entrada.posts.count() == 1 and "Atendi um cliente" in entrada.relato


@pytest.mark.django_db
def test_tela_do_novo_post(ambiente, monkeypatch):
    _, _, client = ambiente
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    insta, _ = _destinos()
    url = reverse("social:novo_post", urlconf=U)
    assert "Quem aparece ou e citado autorizou" in client.get(url).content.decode()
    client.post(
        url,
        {
            "tipo": "novidade",
            "texto": "Abrimos aos sabados.",
            "artigo": "",
            "destinos": [insta.pk],
        },
    )
    assert Post.objects.filter(motivo=Post.Motivo.CASO).count() == 1


# -- Banco de fotos e mistura --------------------------------------------------------------------
def _banco(n_grupos: int):
    for g in range(n_grupos):
        proprio.receber_no_banco(
            [_arquivo(quando=f"2026:03:{10 + g:02d} 10:00:00", semente=10 + g)], autorizada=True
        )


@pytest.mark.django_db
def test_rodada_tira_do_banco_na_proporcao_e_cada_conta_tem_o_seu(
    ambiente, monkeypatch, midia_no_disco
):
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    config = ConfiguracaoSocial.carregar()
    config.ligado = True
    config.save()
    _banco(3)
    insta = Destino.objects.create(rede="instagram", nome="Insta", fotos_por_cento=100)
    gmn = Destino.objects.create(rede="gmn", nome="Google", fotos_por_cento=100)

    assert proprio.estoque(insta) == {"grupos": 3, "dias": 7, "baixo": False}
    escolha.rodada()
    assert insta.posts.filter(motivo=Post.Motivo.FOTOS).count() == 1
    assert gmn.posts.filter(motivo=Post.Motivo.FOTOS).count() == 1
    # O mesmo atendimento pode ir para as duas contas; a mesma conta nao repete.
    assert len(proprio.grupos_disponiveis(insta)) == 2
    assert len(proprio.grupos_disponiveis(gmn)) == 2


@pytest.mark.django_db
def test_tipo_da_vez_segue_a_proporcao(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta", fotos_por_cento=50)
    assert proprio.tipo_da_vez(destino) == "fotos"  # nada ainda
    Post.objects.create(destino=destino, motivo=Post.Motivo.FOTOS)
    assert proprio.tipo_da_vez(destino) == "artigos"
    Post.objects.create(destino=destino, motivo=Post.Motivo.NOVO)
    assert proprio.tipo_da_vez(destino) == "artigos"  # 50%: ja esta na proporcao
    destino.fotos_por_cento = 0
    assert proprio.tipo_da_vez(destino) == "artigos"


@pytest.mark.django_db
def test_mistura_anda_para_o_que_funciona(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta", fotos_por_cento=50)
    agora = timezone.now()
    for i in range(6):
        Post.objects.create(
            destino=destino, motivo=Post.Motivo.FOTOS, sucesso=i < 5, publicado_em=agora
        )
        Post.objects.create(
            destino=destino, motivo=Post.Motivo.NOVO, sucesso=i < 2, publicado_em=agora
        )
    assert proprio.placar_por_tipo(destino) == {
        "fotos": {"n": 6, "taxa": 83},
        "artigos": {"n": 6, "taxa": 33},
    }
    assert proprio.ajustar_mistura(destino) == 60
    assert proprio.ajustar_mistura(destino) is None  # so de 30 em 30 dias
    destino.mistura_ajustada_em = agora - timedelta(days=31)
    destino.fotos_por_cento = 90
    destino.save()
    assert proprio.ajustar_mistura(destino) is None  # teto de 90


@pytest.mark.django_db
def test_tela_do_banco_e_estoque_baixo_avisa(ambiente, midia_no_disco):
    from apps.social import painel

    _, _, client = ambiente
    Destino.objects.create(rede="instagram", nome="Insta", fotos_por_cento=100, teto_semanal=7)
    url = reverse("social:banco_de_fotos", urlconf=U)
    client.post(url, {"acao": "enviar", "fotos": [_arquivo(semente=5)]})
    assert not Midia.objects.exists()  # sem a autorizacao
    client.post(url, {"acao": "enviar", "fotos": [_arquivo(semente=5)], "autorizada": "1"})
    assert Midia.objects.count() == 1
    pagina = client.get(url).content.decode()
    assert "Banco de fotos" in pagina and "1 atendimento(s) ainda nao postado(s)" in pagina
    assert any("banco de fotos da para ~1 dia" in a for a in painel.alertas())


# -- Publicacao com video -----------------------------------------------------------------------
@pytest.mark.django_db
def test_instagram_publica_video_como_reels(ambiente):
    criados = []

    def criar(pedido):
        criados.append(dict(pedido.url.params))
        return httpx.Response(200, json={"id": "reels"})

    rede = Rede(
        {
            "POST graph.facebook.com/v21.0/17841/media_publish": httpx.Response(
                200, json={"id": "M1"}
            ),
            "POST graph.facebook.com/v21.0/17841/media": criar,
            "GET graph.facebook.com/v21.0/reels": httpx.Response(
                200, json={"status_code": "FINISHED"}
            ),
            "GET graph.facebook.com": httpx.Response(200, json={"permalink": "https://ig/p/R"}),
        }
    )
    destino = _conectado("instagram", "17841")
    publicador = PublicadorInstagram(destino, http=rede.cliente(), esperar=lambda s: None)
    publicado = publicador.publicar(
        _post(destino), "Legenda", [ImagemPublica(url="https://p/v.mp4", caminho="", video=True)]
    )
    assert publicado.id_remoto == "M1"
    assert criados[0]["media_type"] == "REELS" and criados[0]["video_url"] == "https://p/v.mp4"


@pytest.mark.django_db
def test_video_no_linkedin_fica_para_copiar(ambiente):
    destino = _conectado("linkedin", "urn:li:person:1")
    post = _post(destino, texto="Legenda", imagens=[{"url": "u", "caminho": "c", "tipo": "video"}])
    with pytest.raises(SemSuporte):
        PublicadorLinkedIn(destino).publicar(
            post, "Legenda", [ImagemPublica(url="u", caminho="c", video=True)]
        )
    with pytest.raises(SemSuporte):
        publicacao.publicar(post)
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.APROVADO and post.tentativas == 0
    assert "copiar" in post.extras["so_copiar"]
    assert publicacao.publicar_vencidos() == 0  # nao tenta de novo


# -- Descrever foto (modelo de visao) ------------------------------------------------------------
@pytest.mark.django_db
def test_descricao_da_foto_so_com_a_opcao_ligada(ambiente, monkeypatch, midia_no_disco):
    _banco(1)
    recebidas = []

    def executar(chave, variaveis, **k):
        recebidas.append((chave, k.get("imagens")))
        return '{"descricao": "corte degrade com risca lateral"}'

    monkeypatch.setattr(fontes, "executar", executar)
    assert proprio.descrever_pendentes() == 0  # desligado
    config = ConfiguracaoSocial.carregar()
    config.descrever_fotos = True
    config.save()
    assert proprio.descrever_pendentes() == 1
    chave, imagens = recebidas[0]
    assert chave == "social_foto" and imagens[0][0] == "image/jpeg"
    midia = Midia.objects.get()
    assert midia.descricao == "corte degrade com risca lateral" and midia.descricao_tentada


def test_o_provedor_manda_a_imagem_junto(monkeypatch):
    from apps.inference.providers.openai_compatible import OpenAICompatibleClient

    enviados = []

    class Cliente:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, **k):
            enviados.append(json)
            corpo = {"choices": [{"message": {"content": "ok"}}], "model": "m"}
            return httpx.Response(200, json=corpo, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: Cliente())
    OpenAICompatibleClient(base_url="http://w").chat(
        model="m", system="s", user="descreva", imagens=[("image/jpeg", b"\xff\xd8")]
    )
    conteudo = enviados[0]["messages"][1]["content"]
    assert conteudo[0] == {"type": "text", "text": "descreva"}
    assert conteudo[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
