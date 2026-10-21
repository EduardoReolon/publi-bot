# ruff: noqa: F811 - as fixtures importadas (modulo, ambiente) entram como parametro
"""Identidade por tipo: a conta diz os estilos permitidos e as orientacoes
(gerais, so das imagens, so dos videos); a IA escolhe o estilo de cada post e o
movimento de cada lamina, e os prompts de imagem e de animacao ficam prontos.
Por lamina: refazer so uma, gerar opcoes e escolher, enviar e tirar clipe,
animar pelo worker ou pela Sora."""

from __future__ import annotations

import io
import json
import shutil

import pytest
from django.urls import reverse
from PIL import Image

from apps.social import criacao, fontes, identidade, redacao, video, video_ia
from apps.social.forms import IdentidadeForm
from apps.social.models import Destino, ElementoVisual, Post
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401

U = "core.urls_tenants"
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
LAMINAS = [
    {
        "titulo": "Acorda cansado?",
        "texto": "Mesmo dormindo 8 horas",
        "visual": "o macaco Gugu bocejando na cama",
        "movimento": "Gugu boceja e esfrega os olhos",
        "fala": "Voce acorda cansado?",
    },
    {"titulo": "Peca o exame", "texto": "e simples", "visual": "Gugu segurando um tubo de ensaio"},
]


def _png(cor="red", tamanho=(896, 1152)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(buffer, format="PNG")
    return buffer.getvalue()


def _post(destino, laminas=LAMINAS, **extras):
    return Post.objects.create(
        destino=destino,
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.RASCUNHO,
        extras={"laminas": [dict(x) for x in laminas], **extras},
    )


@pytest.mark.django_db
def test_estilos_permitidos_e_o_estilo_que_a_ia_escolhe(ambiente, modulo):
    destino = _destino(
        "instagram",
        estilos_permitidos=["quadrinhos", "apresentacao"],
        estilo_das_imagens="quadrinhos",
        descricao_do_estilo="macacos de desenho, cores quentes",
        orientacao_imagens="fundo liso",
        orientacao_videos="movimentos lentos",
    )
    assert identidade.estilos_da_conta(destino) == ["quadrinhos", "apresentacao"]
    texto = identidade.instrucao_de_estilo(destino)
    assert "'quadrinhos'" in texto and "'apresentacao'" in texto and "'foto'" not in texto
    assert "macacos de desenho" in texto and "fundo liso" in texto
    assert "movimentos lentos" not in texto  # a de video vai para a parte do video

    post = _post(destino)
    _, extras = redacao.montar(
        post, {"gancho": "Oi", "texto": "Ola", "estilo": "foto", "laminas": LAMINAS}
    )
    assert "estilo" not in extras  # foto nao e permitida nesta conta
    _, extras = redacao.montar(
        post,
        {
            "gancho": "Oi",
            "texto": "Ola",
            "estilo": "Apresentacao",
            "por_que_estilo": "e uma lista",
            "laminas": LAMINAS,
        },
    )
    assert extras["estilo"] == "apresentacao" and extras["por_que_estilo"] == "e uma lista"
    assert extras["laminas"][0]["movimento"] == "Gugu boceja e esfrega os olhos"
    post.extras = extras
    assert identidade.estilo_do_post(post) == "apresentacao"
    assert "slide" in identidade.descricao_do_estilo(destino, "apresentacao")
    assert "movimentos lentos" in identidade.descricao_do_estilo(destino, "", para="video")

    destino.video = Destino.Video.SEMPRE
    instrucao = video_ia.instrucao(post)
    assert "'cenas'" in instrucao and "movimentos lentos" in instrucao
    assert "formato_sugerido" not in instrucao  # sempre video: nada a sugerir


def test_outra_ia_devolve_estilo_e_movimento():
    resposta = (
        "GANCHO: Oi\nLEGENDA: Ola\nLAMINAS:\n"
        "- Acorda cansado? | Mesmo dormindo | Gugu na cama | Gugu boceja | Voce acorda cansado?\n"
        "- Peca o exame | e simples | tubo de ensaio | Fale com seu medico\n"
        "ESTILO: quadrinhos | o Gugu conta a historia\nFIM"
    )
    dados, _ = criacao.ler(resposta)
    assert dados["laminas"][0]["movimento"] == "Gugu boceja"
    assert dados["laminas"][0]["fala"] == "Voce acorda cansado?"
    # a resposta antiga, sem a coluna do movimento, continua lendo a fala
    assert dados["laminas"][1] == {
        "titulo": "Peca o exame",
        "texto": "e simples",
        "visual": "tubo de ensaio",
        "movimento": "",
        "fala": "Fale com seu medico",
    }
    assert dados["estilo"] == "quadrinhos" and dados["por_que_estilo"] == "o Gugu conta a historia"


@pytest.mark.django_db
def test_prompts_por_lamina_e_opcoes(ambiente, modulo, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    chamadas = []

    def executar(chave, variaveis, json_schema=None):
        chamadas.append(variaveis)
        return json.dumps({"prompt": f"image of {variaveis['cena']}", "movimento": "slow blink"})

    monkeypatch.setattr(fontes, "executar", executar)
    monkeypatch.setattr(
        fontes,
        "gerar_imagens",
        lambda prompt, quantidade: [_png(c) for c in ["red", "green", "blue"][:quantidade]],
    )
    monkeypatch.setattr(fontes, "gerar_imagem", lambda prompt, tamanho="": _png("blue"))
    destino = _destino("instagram", orientacao_videos="camera parada", chamada_final="Fim")
    ElementoVisual.objects.create(
        destino=destino, nome="Gugu", tipo="personagem", descricao="macaco marrom de bone azul"
    )
    post = _post(destino)

    primeiro = identidade.prompts_da_lamina(post, 1)
    assert "macaco marrom de bone azul" in chamadas[0]["cena"]  # a ficha inteira vai junto
    assert chamadas[0]["movimento"] == "Gugu boceja e esfrega os olhos"
    assert chamadas[0]["estilo_video"] == "camera parada"
    assert identidade.prompts_da_lamina(post, 1) == primeiro and len(chamadas) == 1  # guardado
    post.extras["laminas"][0]["visual"] = "Gugu na janela"
    identidade.prompts_da_lamina(post, 1)
    assert len(chamadas) == 2  # a cena mudou: refaz

    # 3 opcoes para a capa, e a escolhida vira a imagem dela
    assert identidade.gerar_imagens(post, apenas=1, opcoes=3) == 1
    post.refresh_from_db()
    assert len(identidade.opcoes_da_lamina(post, 1)) == 3
    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Usar esta" in pagina and "Prompts desta lamina" in pagina
    imagem = client.get(reverse("social:midia_da_lamina", args=[post.pk, 1, "opcao-2"], urlconf=U))
    assert imagem.status_code == 200
    client.post(acao, {"acao": "escolher_opcao", "n": "1", "i": "2"})
    post.refresh_from_db()
    assert post.extras["imagem_propria"] and not identidade.opcoes_da_lamina(post, 1)

    # refazer so a lamina 2, mesmo que ela ja tenha imagem
    identidade.gerar_imagens(post, apenas=2)
    identidade.gerar_imagens(post, apenas=2)
    post.refresh_from_db()
    assert list(post.extras["fundos"]) == ["2"]


@pytest.mark.django_db
def test_form_da_identidade_salva_os_estilos(ambiente):
    destino = _destino("instagram")
    form = IdentidadeForm(
        {
            "estilos_permitidos": ["quadrinhos", "foto"],
            "orientacao_videos": "lento",
            "video": "",
            "chamada_final": "",
        },
        instance=destino,
    )
    assert form.is_valid(), form.errors
    form.save()
    destino.refresh_from_db()
    assert destino.estilos_permitidos == ["quadrinhos", "foto"]
    assert destino.video == "sugerir" and destino.orientacao_videos == "lento"


def test_legendas_em_trechos_curtos_no_tempo_da_fala():
    trechos = video.trechos_da_legenda(
        "Peca o exame de sangue. E simples, rapido e esclarece quase tudo que voce sente."
    )
    assert trechos[0] == "Peca o exame de sangue."
    assert all(len(t.split()) <= video.PALAVRAS_POR_LEGENDA for t in trechos)
    tempos = video.tempos_da_legenda(trechos, 6.0)
    assert tempos[0][0] == 0 and tempos[-1][1] == pytest.approx(6.0, abs=0.02)
    assert all(a < b for a, b in tempos)


@pytest.mark.django_db
def test_prompts_da_folha_de_personagem_na_tela(ambiente):
    _, _, client = ambiente
    destino = _destino("instagram")
    ElementoVisual.objects.create(
        destino=destino, nome="Seu Carlos", tipo="personagem", descricao="dono de empresa, oculos."
    )
    folhas = identidade.prompts_de_folha("Seu Carlos", "dono de empresa, oculos.")
    assert [f["chave"] for f in folhas] == ["2d", "3d", "realista"]
    assert (
        "FOLHA DE PERSONAGEM de Seu Carlos: dono de empresa, oculos. Estilo:" in folhas[0]["prompt"]
    )
    assert "de frente, de 3/4, de perfil e de costas" in folhas[0]["prompt"]
    assert "[nome]" in identidade.prompts_de_folha()[0]["prompt"]
    assert (
        "MESMO lugar" in identidade.prompts_de_folha("Escritorio", "sala", "cenario")[0]["prompt"]
    )
    pagina = client.get(reverse("social:identidade", args=[destino.pk], urlconf=U)).content.decode()
    assert "Criar o personagem numa IA de imagem" in pagina
    assert "FOLHA DE PERSONAGEM de Seu Carlos" in pagina and "Animacao 2D" in pagina


@pytest.mark.django_db
def test_onde_atende_sem_regiao_e_o_pais(ambiente):
    from apps.radar.models import ConfiguracaoDoRadar

    radar = ConfiguracaoDoRadar.carregar()
    radar.regioes = []
    radar.save()
    assert fontes.negocio()["regioes"] == ["Brasil"]
