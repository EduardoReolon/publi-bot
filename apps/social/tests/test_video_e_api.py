# ruff: noqa: F811 - as fixtures importadas (modulo) entram como parametro
"""Video (Reels) das laminas com voz de IA e a outra IA por API.

* a outra IA devolve a fala de cada lamina e sugere carrossel ou video;
* o video e montado com ffmpeg (as mesmas laminas, a narracao de cada uma);
* a outra IA por API recebe o MESMO pedido de copiar e colar e a resposta passa
  pelos mesmos leitores."""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest
from django.core.files.storage import default_storage
from django.urls import reverse

from apps.social import criacao, fontes, outra_ia_por_api, video
from apps.social.models import Isca, Post, RespostaDaOutraIA
from apps.social.tests.test_clareza import RESPOSTA_DO_POST
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401

U = "core.urls_tenants"
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

RESPOSTA_COM_VIDEO = RESPOSTA_DO_POST.replace(
    "- Acorda cansado? | Mesmo dormindo 8 horas | mulher olhando o despertador, exausta",
    "- Acorda cansado? | Mesmo dormindo 8 horas | mulher olhando o despertador, exausta | "
    "Voce acorda cansado mesmo dormindo oito horas?",
).replace("COMENTARIOS:", "FORMATO: video | a pergunta falada prende mais que lida\nCOMENTARIOS:")


def _post(destino, **campos):
    return Post.objects.create(
        destino=destino,
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.RASCUNHO,
        **campos,
    )


def _mp3(segundos: float = 0.4) -> bytes:
    return subprocess.run(  # noqa: S603 - ffmpeg do sistema, so no teste
        [
            shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={segundos}", "-f", "mp3", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip


def test_outra_ia_devolve_fala_e_formato():
    dados, _ = criacao.ler(RESPOSTA_COM_VIDEO)
    assert dados["laminas"][0]["fala"] == "Voce acorda cansado mesmo dormindo oito horas?"
    assert dados["laminas"][1]["fala"] == ""
    assert dados["formato_sugerido"] == "video"
    assert dados["por_que_formato"].startswith("a pergunta falada")


@pytest.mark.django_db
def test_roteiro_e_aviso_de_narracao_longa(ambiente, modulo):
    post = _post(
        _destino("instagram", chamada_final="Link na bio"),
        extras={
            "laminas": [
                {"titulo": "Acorda cansado?", "texto": "Mesmo dormindo bem", "fala": "Oi!"},
                {"titulo": "Peca o exame", "texto": "e simples", "visual": "tubo de ensaio"},
            ]
        },
    )
    assert video.falas(post) == ["Oi!", "Peca o exame. e simples", "Link na bio"]
    texto = video.roteiro(post)
    assert "1. TELA: Acorda cansado? — Mesmo dormindo bem\n   FALA: Oi!" in texto
    assert "IMAGEM: tubo de ensaio" in texto
    longa = {"formato_sugerido": "video", "laminas": [{"fala": "palavra " * 300}]}
    assert video.avisos(longa) and not video.avisos({"laminas": longa["laminas"]})


@pytest.mark.django_db
@pytest.mark.skipif(not FFMPEG, reason="sem ffmpeg nesta maquina")
def test_video_montado_com_a_voz_vira_reels(ambiente, modulo, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    falas = []
    monkeypatch.setattr(fontes, "falar", lambda texto, voz="": falas.append(texto) or _mp3())
    monkeypatch.setattr(fontes, "tem_voz", lambda: True)
    post = _post(
        # voz unica por rodada: a narracao fica guardada por voz + texto
        _destino("instagram", voz=f"nova-{uuid.uuid4().hex[:8]}"),
        extras={
            "laminas": [
                {"titulo": "Acorda cansado?", "texto": "Mesmo dormindo bem", "fala": "Oi!"},
                {"titulo": "Peca o exame", "texto": "e simples", "fala": ""},
            ]
        },
    )
    from apps.social.tasks import gerar_video

    gerar_video.apply(args=[str(post.pk)])
    post.refresh_from_db()
    assert post.extras["formato"] == "video" and default_storage.exists(post.extras["video"])
    assert post.imagens[0]["tipo"] == "video" and len(post.imagens) == 1
    assert falas[0] == "Oi!" and len(falas) == 3  # a chamada final tambem fala
    copia = tmp_path / "copia.mp4"
    with default_storage.open(post.extras["video"], "rb") as arquivo:
        copia.write_bytes(arquivo.read())
    duracao = video._duracao(shutil.which("ffprobe"), copia)
    assert 1.5 < duracao < 4

    # a mesma fala nao e paga duas vezes (audio guardado por voz + texto)
    video.montar(post)
    assert len(falas) == 3

    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Video (Reels)" in pagina and "Voltar ao carrossel" in pagina
    client.post(acao, {"acao": "falas", "falas": "Ola de novo\nPeca o exame hoje"})
    post.refresh_from_db()
    assert [x["fala"] for x in post.extras["laminas"]] == ["Ola de novo", "Peca o exame hoje"]
    client.post(acao, {"acao": "voltar_ao_carrossel"})
    post.refresh_from_db()
    assert post.extras.get("formato") != "video" and post.imagens[0].get("tipo") != "video"


@pytest.mark.django_db
def test_conta_automatica_gera_o_video_quando_a_ia_sugere(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    from apps.social import tasks

    pedidos = []
    monkeypatch.setattr(tasks.gerar_video, "delay", lambda pk, **_kw: pedidos.append(pk))
    monkeypatch.setattr(fontes, "tem_voz", lambda: True)
    post = _post(_destino("instagram", video="automatico"))
    criacao.aplicar(post, RESPOSTA_COM_VIDEO)
    post.refresh_from_db()
    assert pedidos == [str(post.pk)]
    assert post.extras["formato_sugerido"] == "video"

    manual = _post(_destino("instagram"))  # padrao: so sugere
    criacao.aplicar(manual, RESPOSTA_COM_VIDEO)
    assert pedidos == [str(post.pk)]


@pytest.mark.django_db
def test_outra_ia_por_api_usa_o_mesmo_pedido_e_os_mesmos_leitores(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    from apps.social import tasks

    fila = []
    monkeypatch.setattr(tasks.perguntar_a_outra_ia, "delay", lambda pk, **_kw: fila.append(pk))
    monkeypatch.setattr(fontes, "outra_ia_por_api", lambda: "Claude (API) (claude-x)")
    respostas = {}
    monkeypatch.setattr(fontes, "perguntar_a_outra_ia", lambda pedido: respostas["proxima"])

    post = _post(_destino("instagram"))
    url = reverse("social:post_pela_outra_ia", args=[post.pk], urlconf=U)
    assert "Pedir pela API (Claude (API) (claude-x))" in client.get(url).content.decode()
    client.post(
        reverse("social:pedir_a_outra_ia", urlconf=U),
        {"tipo": "post", "post": post.pk, "voltar": url},
    )
    registro = RespostaDaOutraIA.objects.get()
    assert registro.pedido == criacao.pedido(post) and fila == [str(registro.pk)]
    respostas["proxima"] = RESPOSTA_DO_POST
    outra_ia_por_api.responder(registro)
    post.refresh_from_db()
    assert registro.situacao == "aplicada" and post.texto.startswith("Acorda cansado")

    respostas["proxima"] = "ISCAS:\n- Checklist do sono | SONO | 5 passos | Aqui esta\nFIM"
    outra_ia_por_api.responder(outra_ia_por_api.pedir("iscas"))
    assert Isca.objects.filter(nome="Checklist do sono", palavra="SONO").exists()

    respostas["proxima"] = (
        "PUBLICOS:\n- Maes de Curitiba | Curitiba | 25-45 | Maes; Familia | perto\nFIM"
    )
    outra_ia_por_api.responder(outra_ia_por_api.pedir("publicos", post=post))
    assert post.destino.publicos.filter(nome="Maes de Curitiba").exists()

    def falha(pedido):
        raise RuntimeError("chave recusada")

    monkeypatch.setattr(fontes, "perguntar_a_outra_ia", falha)
    errado = outra_ia_por_api.pedir("iscas")
    outra_ia_por_api.responder(errado)
    assert errado.situacao == "erro" and "chave recusada" in errado.erro


@pytest.mark.django_db
def test_sem_api_o_botao_some_e_o_pedido_e_recusado(ambiente, modulo):
    _, _, client = ambiente
    post = _post(_destino("instagram"))
    url = reverse("social:post_pela_outra_ia", args=[post.pk], urlconf=U)
    assert "Pedir pela API" not in client.get(url).content.decode()
    client.post(reverse("social:pedir_a_outra_ia", urlconf=U), {"tipo": "post", "post": post.pk})
    assert not RespostaDaOutraIA.objects.exists()


@pytest.mark.django_db
def test_conta_com_ia_grande_manda_o_post_para_ela(ambiente, modulo, monkeypatch):
    from apps.social import tasks

    pedidos = []
    monkeypatch.setattr(fontes, "outra_ia_por_api", lambda: "Claude (API) (claude-x)")
    monkeypatch.setattr(tasks.perguntar_a_outra_ia, "delay", lambda pk, **_kw: pedidos.append(pk))
    post = _post(_destino("instagram", escritor="outra_ia"), extras={})
    post.situacao = Post.Situacao.SUGERIDO
    post.save()
    assert tasks.escrever_post.apply(args=[str(post.pk)]).get() == "pedido a outra IA"
    registro = RespostaDaOutraIA.objects.get()
    assert registro.tipo == "post" and pedidos == [str(registro.pk)]
    tasks.escrever_post.apply(args=[str(post.pk)])  # retomada: nao pede duas vezes
    assert RespostaDaOutraIA.objects.count() == 1

    # a IA grande falhou: o post nao fica "Escrevendo" para sempre
    monkeypatch.setattr(fontes, "perguntar_a_outra_ia", lambda pedido: 1 / 0)
    outra_ia_por_api.responder(registro)
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.FALHOU and "IA grande" in post.erro
