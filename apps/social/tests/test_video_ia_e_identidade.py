"""Video por IA enviado no post, a identidade visual escrita pela outra IA e a
foto por IA das redes sem laminas."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.social import identidade, identidade_ia, video_ia
from apps.social.models import ElementoVisual, Post
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401
from apps.social.tests.test_video_e_api import _post

U = "core.urls_tenants"


@pytest.mark.django_db
def test_video_feito_fora_vira_o_video_do_post_e_sai(ambiente, modulo, settings, tmp_path):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    post = _post(_destino("instagram"), extras={"laminas": [{"titulo": "Oi", "texto": "Tudo"}]})
    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)

    client.post(acao, {"acao": "enviar_video_ia", "video": SimpleUploadedFile("x.txt", b"x")})
    post.refresh_from_db()
    assert "video" not in post.extras  # formato errado nao entra

    arquivo = SimpleUploadedFile("reels.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")
    client.post(acao, {"acao": "enviar_video_ia", "video": arquivo})
    post.refresh_from_db()
    assert post.extras["formato"] == "video" and post.extras["video_de"] == "ia"
    assert post.extras["video"].endswith(".mp4")

    client.post(acao, {"acao": "tirar_video_ia"})
    post.refresh_from_db()
    assert "video" not in post.extras and post.extras.get("formato") != "video"


RESPOSTA_DA_IDENTIDADE = """Claro! Segue:

**ESTILO PREFERIDO:** ilustracao
ESTILOS PERMITIDOS: ilustracao, quadrinhos, apresentacao, cinema
ORIENTACOES GERAIS: ilustracao plana, traco grosso, tons de azul e areia, luz suave
SO IMAGENS: personagem a esquerda, metade de baixo lisa para o texto
SO VIDEOS: movimentos lentos, camera parada, som ambiente leve
FOTO REALISTA QUANDO: nenhum
TEXTO NO VIDEO: legendas, curtas
CHAMADA DO VIDEO: Agende pelo link da bio
ULTIMA LAMINA: Salve e mande para quem precisa
CORES: fundo #0F1E3D, texto #FFFFFF, destaque #F2C14E
PERSONAGENS:
- Gugu | personagem | gorila adulto de terno azul-marinho, gravata amarela, oculos redondos
- Consultorio | cenario | sala clara com planta e janela grande
COMENTARIOS: mantenha o Gugu em todos os posts.
"""


@pytest.mark.django_db
def test_identidade_pela_outra_ia_previa_e_aplicar(ambiente, modulo):  # noqa: F811
    _, _, client = ambiente
    destino = _destino("instagram")
    url = reverse("social:identidade", args=[destino.pk], urlconf=U)

    pagina = client.post(url, {"acao": "pedido_ia", "ideia": "mascote gorila, leve"}).content
    pagina = pagina.decode()
    assert "mascote gorila, leve" in pagina and "ESTILOS PERMITIDOS:" in pagina

    dados = identidade_ia.ler(RESPOSTA_DA_IDENTIDADE)
    assert dados["estilos_permitidos"] == ["ilustracao", "quadrinhos", "apresentacao"]
    assert "cenarios_de_foto" not in dados  # "nenhum" nao apaga o campo
    assert dados["texto_no_video"] == "legendas"

    previa = client.post(url, {"acao": "ler_ia", "resposta": RESPOSTA_DA_IDENTIDADE}).content
    assert "Agende pelo link da bio" in previa.decode()
    destino.refresh_from_db()
    assert destino.chamada_do_video == ""  # a previa nao grava

    client.post(url, {"acao": "aplicar_ia", "resposta": RESPOSTA_DA_IDENTIDADE})
    destino.refresh_from_db()
    assert destino.estilo_das_imagens == "ilustracao"
    assert destino.cores["destaque"] == "#F2C14E"
    assert destino.chamada_final == "Salve e mande para quem precisa"
    gugu = ElementoVisual.objects.get(destino=destino, nome="Gugu")
    assert gugu.tipo == "personagem" and "gravata amarela" in gugu.descricao
    assert ElementoVisual.objects.get(nome="Consultorio").tipo == "cenario"


@pytest.mark.django_db
def test_foto_por_ia_no_linkedin(ambiente, modulo, settings, tmp_path, monkeypatch):  # noqa: F811
    """A foto das redes sem laminas e a "lamina 0": prompt com a identidade
    (e as regras do negocio), opcoes geradas aqui e a escolha vira a foto."""
    import io

    from PIL import Image

    from apps.social import fontes
    from apps.social.models import ConfiguracaoSocial

    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    config = ConfiguracaoSocial.carregar()
    config.regras = "nunca mostrar sangue"
    config.save()
    destino = _destino("linkedin", descricao_do_estilo="ilustracao plana azul")
    post = _post(destino, extras={"gancho": "Acordar cansado tem causa"})

    texto = identidade.pedido_de_foto(post)
    assert "quadrado 1:1" in texto and "ilustracao plana azul" in texto
    assert "nunca mostrar sangue" in texto and "Acordar cansado tem causa" in texto

    pedidos = []

    def executar(chave, variaveis, **_k):
        pedidos.append(variaveis)
        return '{"prompt": "a calm bedroom at dawn", "movimento": ""}'

    def png(cor):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), cor).save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(fontes, "executar", executar)
    monkeypatch.setattr(
        fontes, "gerar_imagens", lambda prompt, **k: [png("red"), png("green"), png("blue")]
    )
    assert identidade.gerar_imagens(post, apenas=identidade.FOTO, opcoes=3) == 1
    assert "nunca mostrar sangue" in pedidos[0]["estilo"]
    post.refresh_from_db()
    assert post.opcoes_da_foto == ["0", "1", "2"]

    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Copiar o pedido da foto" in pagina

    client.post(
        reverse("social:acao_no_post", args=[post.pk], urlconf=U),
        {"acao": "escolher_opcao", "n": "0", "i": "1"},
    )
    post.refresh_from_db()
    assert post.extras["imagem_propria"].endswith("propria.jpg")
    assert post.opcoes_da_foto == []
    assert Post.objects.get(pk=post.pk).imagens[0]["caminho"] == post.extras["imagem_propria"]


def test_roteiro_lido_com_segundos_soltos_e_o_formato_antigo():
    roteiro = video_ia.ler_roteiro(
        {
            "cenas": [
                {"imagem": "a", "segundos": "4 s", "fala": "Oi  de novo"},
                {"imagem": ""},
                {"mostra": "b"},
            ],
        }
    )
    assert [c["segundos"] for c in roteiro["cenas"]] == [4, 0]
    assert roteiro["cenas"][0]["fala"] == "Oi de novo" and roteiro["cenas"][1]["imagem"] == "b"
    antigo = video_ia.ler_roteiro({"gancho": "o despertador", "cenas": [{"mostra": "x"}]})
    assert [c["imagem"] for c in antigo["cenas"]] == ["o despertador", "x"]
    assert video_ia.ler_roteiro({"cenas": []}) == {}
