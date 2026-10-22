# ruff: noqa: F811 - as fixtures importadas (modulo) entram como parametro
"""Video (Reels) por cenas e a outra IA por API.

* a outra IA sugere o formato e, no video, devolve as cenas;
* cada cena: imagem inicial, animacao com seed, e a montagem com voz e legenda;
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
from apps.social.models import Destino, Isca, Post, RespostaDaOutraIA
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


RESPOSTA_COM_CENAS = RESPOSTA_COM_VIDEO.replace(
    "COMENTARIOS:",
    "CENAS:\n"
    "- 3 s | Voce dorme oito horas e acorda assim? | close no rosto cansado da mulher, "
    "despertador ao lado | ela abre os olhos devagar; camera aproxima | Cansada de novo\n"
    "- 4 | O cansaco pode ter causa. | medica mostrando o exame | ela vira a folha |\n"
    "COMENTARIOS:",
)


@pytest.mark.django_db
def test_ia_sugere_video_com_cenas_e_nada_sai_sozinho(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    """O roteiro vem picado em cenas (o que o worker anima) e nada sai sozinho:
    nem a conta antiga marcada "automatico"."""
    settings.MEDIA_ROOT = tmp_path
    from apps.social import tasks, video_ia

    pedidos = []
    monkeypatch.setattr(tasks.gerar_cenas, "delay", lambda pk, **_kw: pedidos.append(pk))
    destino = _destino("instagram", video="automatico")
    destino.texto_no_video = "frases"
    destino.chamada_do_video = "Agende pelo link da bio"
    destino.save()
    post = _post(destino)
    criacao.aplicar(post, RESPOSTA_COM_CENAS)
    post.refresh_from_db()
    assert pedidos == []
    assert post.extras["formato_sugerido"] == "video"
    cenas = post.extras["video_ia"]["cenas"]
    assert [c["segundos"] for c in cenas] == [3, 4]
    assert cenas[0]["fala"] == "Voce dorme oito horas e acorda assim?"
    assert cenas[0]["movimento"].startswith("ela abre os olhos") and cenas[0]["texto"]
    assert cenas[1]["texto"] == ""

    pedido_do_post = criacao.pedido(post)
    assert "CENAS:" in pedido_do_post and "Responda JA com o post pronto" in pedido_do_post
    assert "a CADA" in pedido_do_post and "Agende pelo link da bio" in pedido_do_post
    assert "de 2 a 4 s" in pedido_do_post and "ninguem fala em cena" in pedido_do_post
    assert "3 palavras por segundo" in pedido_do_post and "pode ocupar 2 cenas" in pedido_do_post
    assert "MESMO sentido em que o artigo" in pedido_do_post
    assert "PERSONAGENS FIXOS" in pedido_do_post

    texto = video_ia.pedido(post)
    assert "9:16" in texto and "FRASES CURTAS" in texto
    assert "Cena 1 (3 s)" in texto and "Imagem inicial: close no rosto" in texto
    sem = _post(Destino.objects.create(rede="instagram", nome="Outra"))
    assert "Monte voce o roteiro" in video_ia.pedido(sem)


def _mp4(segundos: float = 1.0) -> bytes:
    return subprocess.run(  # noqa: S603 - ffmpeg do sistema, so no teste
        [
            shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi",
            "-i", f"testsrc=size=360x640:rate=30:duration={segundos}",
            "-pix_fmt", "yuv420p", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip


@pytest.mark.django_db
@pytest.mark.skipif(not FFMPEG, reason="sem ffmpeg nesta maquina")
def test_cenas_geradas_refeitas_e_montadas(ambiente, modulo, settings, tmp_path, monkeypatch):
    """Cada cena: a imagem inicial, a animacao (com seed; refazer troca a seed),
    e a montagem no tempo da fala, com a voz e a legenda. Mudar a fala so pede
    montar de novo; o clipe curto e esticado/segurado ate o fim da fala."""
    import io
    import json

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.social import cenas

    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    falas, animados = [], []
    monkeypatch.setattr(fontes, "falar", lambda texto, voz="": falas.append(texto) or _mp3(1.0))
    monkeypatch.setattr(fontes, "tem_voz", lambda: True)
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda chave, variaveis, **k: json.dumps(
            {"prompt": f"first frame: {variaveis['cena']}", "movimento": "slow blink"}
        ),
    )

    def pedir(imagem, prompt, *, segundos, tamanho, seed):
        animados.append((segundos, tamanho, seed, prompt))
        return {"id": f"video_{len(animados)}", "seed": seed}

    monkeypatch.setattr(fontes, "pedir_animacao", pedir)
    monkeypatch.setattr(fontes, "situacao_da_animacao", lambda i: {"status": "completed"})
    monkeypatch.setattr(fontes, "baixar_animacao", lambda i: _mp4(0.8))  # menos que o pedido

    def animar(n, **k):
        cenas.pedir_animacao(post, n, **k)
        post.refresh_from_db()
        assert cenas.conferir_animacao(post, n) == "pronta"
        post.refresh_from_db()

    destino = _destino("instagram", voz=f"v-{uuid.uuid4().hex[:8]}")
    destino.texto_no_video = "legendas"
    destino.save()
    roteiro = {
        "cenas": [
            {
                "segundos": 3,
                "fala": "Oi, tudo bem?",
                "imagem": "mulher cansada",
                "movimento": "pisca",
            },
            {"segundos": 2, "fala": "", "imagem": "a mesa", "movimento": ""},
        ]
    }
    post = _post(destino, extras={"video_ia": roteiro})
    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)

    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), "red").save(buffer, format="PNG")
    client.post(
        acao,
        {
            "acao": "cena_enviar_imagem",
            "n": "1",
            "imagem": SimpleUploadedFile("q.png", buffer.getvalue(), "image/png"),
        },
    )
    post.refresh_from_db()
    assert post.cenas_do_video[0]["tem_quadro"] and post.cenas_do_video[0]["quadro_de"] == "enviado"

    animar(1)
    primeira_seed = post.extras["video_ia"]["cenas"][0]["seed"]
    # 3 s de cena: 2 trechos (2,2 s esticados ate 25% e o fim parado), nao 3
    assert animados[0][:2] == (2, cenas.TAMANHO_DA_ANIMACAO) and animados[0][3] == "slow blink"
    animar(1)  # de novo, sem pedir variacao: a mesma seed
    animar(1, nova_seed=True)
    assert animados[1][2] == primeira_seed and animados[2][2] != primeira_seed

    # a cena 2 so tem a imagem: entra parada, aproximando devagar
    monkeypatch.setattr(fontes, "gerar_quadro", lambda *a, **k: (buffer.getvalue(), ""))
    cenas.gerar_quadro(post, 2)
    post.refresh_from_db()
    caminho = cenas.montar(post)
    post.refresh_from_db()
    assert post.extras["formato"] == "video" and post.extras["video_de"] == "cenas"
    assert post.imagens[0]["tipo"] == "video"
    copia = tmp_path / "v.mp4"
    with default_storage.open(caminho, "rb") as arquivo:
        copia.write_bytes(arquivo.read())
    # cena 1: a fala (1 s) + respiro, ou os 3 s do roteiro; cena 2: 2 s; menos a fusao
    assert 4.2 < video._duracao(shutil.which("ffprobe"), copia) < 5.2
    assert falas == ["Oi, tudo bem?"]

    client.post(acao, {"acao": "cena_salvar", "n": "1", "fala": "Mudou a fala.", "imagem": "outra"})
    post.refresh_from_db()
    tela = post.cenas_do_video[0]
    assert tela["fala"] == "Mudou a fala." and tela["tem_clipe"]  # a animacao fica
    cenas.montar(post)
    assert falas == ["Oi, tudo bem?", "Mudou a fala."]  # so a fala nova e paga

    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Refazer a animacao" in pagina and "Montar o video" in pagina
    clipe = client.get(reverse("social:midia_da_cena", args=[post.pk, 1, "clipe"], urlconf=U))
    assert clipe.status_code == 200


@pytest.mark.django_db
def test_lugar_real_na_janela_e_worker_sem_a_rota(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    """O lugar real vai na janela (base + mascara); o personagem citado vai como
    referencia. Worker sem /images/edits: a cena sai so pelo texto, com aviso."""
    import io
    import json

    from django.core.files.base import ContentFile
    from PIL import Image

    from apps.inference.providers.base import RotaInexistente
    from apps.social import cenas
    from apps.social.models import ElementoVisual

    settings.MEDIA_ROOT = tmp_path

    def png(cor, tamanho=(200, 120)):
        buffer = io.BytesIO()
        Image.new("RGB", tamanho, cor).save(buffer, format="PNG")
        return buffer.getvalue()

    destino = _destino("instagram")
    museu = ElementoVisual(destino=destino, nome="Museu do Olho", tipo="lugar", descricao="museu")
    museu.imagem.save("museu.png", ContentFile(png("blue")), save=True)
    carlos = ElementoVisual(destino=destino, nome="Carlos", tipo="personagem", descricao="55 anos")
    carlos.imagem.save("carlos.png", ContentFile(png("green")), save=True)
    roteiro = {
        "cenas": [
            {"imagem": "Carlos cansado na mesa; janela a direita com o Museu do Olho"},
            {"imagem": "Museu do Olho de fundo ao entardecer"},
        ]
    }
    post = _post(destino, extras={"video_ia": roteiro})
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda chave, variaveis, **k: json.dumps({"prompt": "frame", "movimento": "blink"}),
    )
    pedidos = []

    def editar(prompt, *, tamanho, base=None, mascara=None, referencias=()):
        pedidos.append((base, mascara, referencias))
        if len(pedidos) > 1:  # o worker 2.12 tem a rota, mas sem o modelo de referencia
            return png("white", cenas.QUADRO), ("reference_image ignorado: modelo desligado",)
        raise RotaInexistente("sem rota")

    monkeypatch.setattr("apps.content.capas.editar_imagem", editar)
    monkeypatch.setattr(fontes, "gerar_imagem", lambda prompt, tamanho="": png("white", (72, 128)))
    cenas.gerar_quadro(post, 1)
    base, mascara, referencias = pedidos[0]
    assert len(referencias) == 1  # o Carlos (o lugar nao e referencia)
    largura, _altura = cenas.QUADRO
    janela = Image.open(io.BytesIO(mascara))
    assert janela.getpixel((largura - 60, 120))[3] == 255  # a foto fica (a direita, no alto)
    assert janela.getpixel((60, 1200))[3] == 0  # o resto e pintado
    assert Image.open(io.BytesIO(base)).getpixel((largura - 60, 120))[:3] == (0, 0, 255)
    post.refresh_from_db()
    assert "ainda nao aceita referencia" in post.extras["video_ia"]["cenas"][0]["aviso"]

    cenas.gerar_quadro(post, 1)  # de novo: agora o worker tem a rota e avisa
    post.refresh_from_db()
    assert post.extras["video_ia"]["cenas"][0]["aviso"].startswith("reference_image ignorado")
    assert Image.open(io.BytesIO(pedidos[1][1])).mode == "RGBA"  # a mascara tem alfa
    assert cenas.usa_referencia(post, roteiro["cenas"][0])
    assert not cenas.usa_referencia(post, roteiro["cenas"][1])

    cenas.gerar_quadro(post, 2)  # lugar de fundo, sem personagem: a propria foto
    post.refresh_from_db()
    assert post.extras["video_ia"]["cenas"][1]["quadro_de"] == "lugar" and len(pedidos) == 2
    assert "Museu do Olho" in cenas.pedido_das_imagens(post)


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


def test_segundos_pedidos_sao_o_minimo_em_trechos_inteiros():
    """O worker faz round(seconds / 1,2) trechos (contrato 2.12): pede-se o
    menor clipe que esticado ate 25% (e meio segundo parado) cobre a cena."""
    from apps.social import cenas

    # 3 s de cena: 2 trechos (2,4 s, esticados ate 3 s), nao 3
    assert cenas.segundos_pedidos({"segundos": 3}) == 2
    assert cenas.segundos_pedidos({"segundos": 2}) == 1  # 1 trecho (1,2 s -> 1,5 s + 0,5)
    assert cenas.segundos_pedidos({"segundos": 4}) == 4  # 3 trechos
    fala = {"fala": " ".join(["palavra"] * 13)}  # ~5,3 s de fala: 4 trechos
    assert cenas.segundos_pedidos(fala) == 5
    for segundos in (1, 2, 4, 5, 6):  # o que se pede volta nos trechos certos no worker
        trechos = max(1, round(segundos / cenas.TRECHO))
        assert round(trechos * cenas.TRECHO) == segundos


@pytest.mark.django_db
def test_worker_desligado_ou_reiniciado_no_meio_so_atrasa_a_cena(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    """Animacao pedida e o worker cai: a cena espera (as prontas ficam). Ele
    volta sem lembrar do trabalho: a cena e pedida de novo, com a mesma seed."""
    import json

    from django.core.files.base import ContentFile

    from apps.inference.providers.base import ProviderTransientError
    from apps.social import cenas

    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda chave, variaveis, **k: json.dumps({"prompt": "frame", "movimento": "blink"}),
    )
    pedidos = []
    monkeypatch.setattr(
        fontes,
        "pedir_animacao",
        lambda *a, seed, **k: pedidos.append(seed) or {"id": "video_x", "seed": seed},
    )
    post = _post(
        _destino("instagram"),
        extras={"video_ia": {"cenas": [{"imagem": "a", "clipe": ""}, {"imagem": "b"}]}},
    )
    lista = cenas.cenas(post)
    for n, cena in enumerate(lista, start=1):
        cena["quadro"] = default_storage.save(f"social/{post.pk}/q{n}.png", ContentFile(b"PNG"))
    lista[0]["clipe"] = default_storage.save(f"social/{post.pk}/c1.mp4", ContentFile(b"MP4"))
    cenas._gravar(post, lista)

    cenas.pedir_animacao(post, 2)
    post.refresh_from_db()

    def fora(identificador):
        raise ProviderTransientError("conexao recusada")

    monkeypatch.setattr(fontes, "situacao_da_animacao", fora)
    assert cenas.conferir_animacao(post, 2) == "esperando"
    monkeypatch.setattr(fontes, "situacao_da_animacao", lambda i: {"status": "in_progress"})
    assert cenas.conferir_animacao(post, 2) == "esperando"
    monkeypatch.setattr(fontes, "situacao_da_animacao", lambda i: {"status": "sumiu"})
    assert cenas.conferir_animacao(post, 2) == "perdida"
    post.refresh_from_db()
    assert cenas.faltam(post) == [2]  # a cena 1 continua pronta
    cenas.pedir_animacao(post, 2)
    assert pedidos[0] == pedidos[1]  # a mesma variacao

    post.refresh_from_db()
    monkeypatch.setattr(
        fontes,
        "situacao_da_animacao",
        lambda i: {"status": "failed", "error": {"message": "sem_vram"}},
    )
    with pytest.raises(video.VideoIndisponivel, match="sem_vram"):
        cenas.conferir_animacao(post, 2)
