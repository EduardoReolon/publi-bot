# ruff: noqa: F811 - as fixtures importadas (modulo) entram como parametro
"""Video (Reels) por cenas e a outra IA por API.

* a outra IA sugere o formato e, no video, devolve as cenas;
* cada cena: imagem inicial, animacao com seed, e a montagem com voz e legenda;
* a outra IA por API recebe o MESMO pedido de copiar e colar e a resposta passa
  pelos mesmos leitores."""

from __future__ import annotations

import io
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
    vizinhas = []

    def falar(texto, voz="", *, antes="", depois=""):
        falas.append(texto)
        vizinhas.append((antes, depois))
        return _mp3(1.0)

    monkeypatch.setattr(fontes, "falar", falar)
    monkeypatch.setattr(fontes, "tem_voz", lambda: True)
    monkeypatch.setattr(
        fontes,
        "executar",
        lambda chave, variaveis, **k: json.dumps(
            {"prompt": f"first frame: {variaveis['cena']}", "movimento": "slow blink"}
        ),
    )

    def pedir(imagem, prompt, *, segundos, tamanho, seed, maior=False):
        animados.append((segundos, tamanho, seed, prompt, maior))
        modelo = "framepack" if maior else "framepack-fp8"
        return {"id": f"video_{len(animados)}", "seed": seed, "model": modelo}

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
    assert post.extras["video_ia"]["cenas"][0]["modelo"] == "framepack-fp8"  # o menor
    variacao = animados[2][2]
    animar(1, maior=True)  # qualidade maxima: o modelo maior com a MESMA seed
    assert animados[3][2] == variacao and animados[3][4]
    assert post.cenas_do_video[0]["modelo"] == "framepack" and post.cenas_do_video[0]["no_maior"]

    # O historico: cada animacao nova guardou a anterior, que pode voltar ou sumir.
    versoes = post.cenas_do_video[0]["versoes"]
    assert [v["seed"] for v in versoes] == [variacao, primeira_seed, primeira_seed]
    maxima = cenas.cenas(post)[0]["clipe"]
    client.post(acao, {"acao": "cena_usar_versao", "n": "1", "i": "0"})
    post.refresh_from_db()
    cena = cenas.cenas(post)[0]
    assert cena["seed"] == variacao and cena["modelo"] == "framepack-fp8" and not cena["no_maior"]
    assert cena["historico"][0]["clipe"] == maxima and cena["historico"][0]["no_maior"]
    assert cena["movimento"] == "pisca" and cena["imagem"] == "mulher cansada"
    versao = client.get(reverse("social:midia_da_cena", args=[post.pk, 1, "versao-0"], urlconf=U))
    assert versao.status_code == 200
    antigo = cena["historico"][2]["clipe"]
    client.post(acao, {"acao": "cena_apagar_versao", "n": "1", "i": "2"})
    post.refresh_from_db()
    cena = cenas.cenas(post)[0]
    assert not default_storage.exists(antigo) and len(cena["historico"]) == 2
    assert default_storage.exists(cena["quadro"])  # a imagem e a mesma das outras versoes
    client.post(acao, {"acao": "cena_usar_versao", "n": "1", "i": "0"})  # volta a maxima
    post.refresh_from_db()
    assert cenas.cenas(post)[0]["clipe"] == maxima

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
    assert "Versoes anteriores" in pagina and "cena_apagar_versao" in pagina
    clipe = client.get(reverse("social:midia_da_cena", args=[post.pk, 1, "clipe"], urlconf=U))
    assert clipe.status_code == 200

    # Refazer a imagem e SO a imagem: a cena fica parada ate pedirem a animacao.
    fila = []
    monkeypatch.setattr(fontes, "na_fila", lambda tarefa, *a, **k: fila.append(k) or "t")
    monkeypatch.setattr(fontes, "ha_modelo_de_imagem", lambda: True)
    monkeypatch.setattr(fontes, "tem_animacao", lambda: True)
    client.post(acao, {"acao": "cena_refazer_imagem", "n": "1"})
    post.refresh_from_db()
    assert fila[-1]["so_quadros"] and fila[-1]["refazer_quadro"]
    assert fila[-1]["rotulo"] == "Imagem da cena 1"
    assert cenas.cenas(post)[0]["parada"]
    client.post(acao, {"acao": "cena_gerar", "n": "1"})
    post.refresh_from_db()
    assert not fila[-1]["so_quadros"] and "parada" not in cenas.cenas(post)[0]
    assert fila[-1]["rotulo"] == "Animacao da cena 1"
    client.post(acao, {"acao": "cena_sem_referencia", "n": "1"})
    assert fila[-1]["sem_referencia"] and fila[-1]["rotulo"].endswith("(sem referencia)")

    # A entonacao gravada no navegador e so um guia: a voz do video a repete
    # (conversao). Sem servico que converta, vale a voz gerada, com aviso; a
    # gravacao em si nunca entra.
    gravada = SimpleUploadedFile("fala.webm", _mp3(1.2), "audio/webm")
    client.post(acao, {"acao": "cena_enviar_fala", "n": "1", "fala_gravada": gravada})
    post.refresh_from_db()
    assert post.cenas_do_video[0]["tem_fala_gravada"]
    pedidas = len(falas)
    cenas.montar(post)  # nenhuma conexao converte: voz gerada e aviso
    post.refresh_from_db()
    assert len(falas) == pedidas  # a voz gerada (ja guardada), nao a gravacao
    assert "entonacao gravada nao usada" in post.cenas_do_video[0]["aviso_fala"]
    convertidas = []
    monkeypatch.setattr(
        fontes, "converter_fala", lambda audio, voz="": convertidas.append(voz) or _mp3(1.0)
    )
    montado = cenas.montar(post)
    post.refresh_from_db()
    assert convertidas == [destino.voz]  # a voz do video repetiu a entonacao
    # A cena 1 (3 s planejados) dura a fala convertida (1 s): o video encurta.
    copia = tmp_path / "v2.mp4"
    with default_storage.open(montado, "rb") as arquivo:
        copia.write_bytes(arquivo.read())
    assert video._duracao(shutil.which("ffprobe"), copia) < 4.0
    assert "aviso_fala" not in post.cenas_do_video[0]
    client.post(acao, {"acao": "cena_salvar", "n": "1", "fala": "Outra fala.", "imagem": "x"})
    post.refresh_from_db()
    assert post.cenas_do_video[0]["fala_mudou"]
    client.post(acao, {"acao": "cena_tirar_fala", "n": "1"})
    post.refresh_from_db()
    assert not post.cenas_do_video[0]["tem_fala_gravada"]

    # Todas as imagens de uma vez: a 1a na cena 1, a 2a na cena 2; a 3a sobra.
    def arquivo(cor):
        dados = io.BytesIO()
        Image.new("RGB", (720, 1280), cor).save(dados, format="PNG")
        return SimpleUploadedFile(f"{cor}.png", dados.getvalue(), "image/png")

    client.post(
        acao,
        {
            "acao": "cenas_enviar_imagens",
            "imagens": [arquivo("green"), arquivo("blue"), arquivo("white")],
        },
    )
    post.refresh_from_db()
    tela = post.cenas_do_video
    assert [c["quadro_de"] for c in tela] == ["enviado", "enviado"]
    with default_storage.open(tela[1]["quadro"], "rb") as guardada:
        assert Image.open(guardada).getpixel((10, 10)) == (0, 0, 255)


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
    settings.CENAS_REFERENCIA_LOCAL = True  # desligada de fabrica

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

    def editar(prompt, *, tamanho, base=None, mascara=None, referencias=(), **extras):
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
    assert janela.getpixel((largura - 150, 200))[3] == 255  # a foto fica (a direita, no alto)
    assert janela.getpixel((60, 1200))[3] == 0  # o resto e pintado
    desenho = Image.open(io.BytesIO(base))
    assert desenho.getpixel((largura - 150, 200))[:3] == (0, 0, 255)
    # o batente da janela em volta da foto (fica, e diz ao gerador que e janela)
    batente = cenas.MOLDURAS["janela"][1]
    assert desenho.getpixel((largura - 150, 95))[:3] == batente
    assert janela.getpixel((largura - 150, 95))[3] == 255
    post.refresh_from_db()
    assert "ainda nao aceita referencia" in post.extras["video_ia"]["cenas"][0]["aviso"]

    cenas.gerar_quadro(post, 1)  # de novo: agora o worker tem a rota e avisa
    post.refresh_from_db()
    assert post.extras["video_ia"]["cenas"][0]["aviso"].startswith("reference_image ignorado")
    assert post.cenas_do_video[0]["tem_referencia"]  # a referencia enviada, para conferir
    cenas.gerar_quadro(post, 1, sem_referencia=True)  # para comparar: so pelo texto
    post.refresh_from_db()
    assert not pedidos[-1][2] and not post.cenas_do_video[0]["tem_referencia"]
    pedidos.pop()
    assert Image.open(io.BytesIO(pedidos[1][1])).mode == "RGBA"  # a mascara tem alfa
    assert cenas.usa_referencia(post, roteiro["cenas"][0])
    assert not cenas.usa_referencia(post, roteiro["cenas"][1])

    cenas.gerar_quadro(post, 2)  # lugar de fundo, sem personagem: a propria foto
    post.refresh_from_db()
    assert post.extras["video_ia"]["cenas"][1]["quadro_de"] == "lugar" and len(pedidos) == 2
    pedido = cenas.pedido_das_imagens(post)
    assert "Imagem 1: Carlos" in pedido and "Imagem 2: Museu do Olho" in pedido
    assert "Use: Carlos (imagem 1), Museu do Olho (imagem 2)." in pedido
    assert post.cenas_do_video[1]["usa"] == [(2, "Museu do Olho")]

    settings.CENAS_REFERENCIA_LOCAL = False  # o padrao: a imagem daqui so pela descricao
    cenas.gerar_quadro(post, 1)
    assert pedidos[-1][2] == []


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


@pytest.mark.django_db
def test_clipe_pronto_chega_pela_varredura_e_parar_nao_pede_de_novo(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    """A conferencia da tarefa pode ficar presa numa implantacao: a varredura de
    5 em 5 minutos busca o clipe pronto. Parada, a cena nao e conferida nem pedida
    de novo, nem com o worker reiniciado."""
    import json

    from django.core.files.base import ContentFile

    from apps.social import cenas, tasks

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
        lambda *a, seed, **k: pedidos.append(seed) or {"id": f"video_{len(pedidos)}", "seed": seed},
    )
    post = _post(
        _destino("instagram"), extras={"video_ia": {"cenas": [{"imagem": "a"}, {"imagem": "b"}]}}
    )
    lista = cenas.cenas(post)
    for n, cena in enumerate(lista, start=1):
        cena["quadro"] = default_storage.save(f"social/{post.pk}/q{n}.png", ContentFile(b"PNG"))
    cenas._gravar(post, lista)
    cenas.pedir_animacao(post, 1)
    cenas.pedir_animacao(post, 2)
    post.refresh_from_db()

    cenas.parar_animacao(post, 2)
    monkeypatch.setattr(fontes, "situacao_da_animacao", lambda i: {"status": "completed"})
    monkeypatch.setattr(fontes, "baixar_animacao", lambda i: b"MP4")
    assert cenas.retomar_animacoes() == 1  # a cena 1 chegou; a 2 parada nem e conferida
    post.refresh_from_db()
    tela = cenas.para_a_tela(post)
    assert tela[0]["tem_clipe"] and not tela[1]["animando"] and len(pedidos) == 2

    # A tarefa que ainda estava rodando pula a cena parada (nao pede de novo).
    tasks.gerar_cenas.apply(args=[str(post.pk)], kwargs={"n": 2}).get()
    assert len(pedidos) == 2
    cenas.liberar(post, 2)  # pedido explicito ("Animar a cena")
    post.refresh_from_db()
    assert "parada" not in cenas.cenas(post)[1]


def test_da_folha_de_personagem_vai_so_a_primeira_figura():
    """A folha (fundo branco, a mesma figura 4 vezes e os rostos embaixo) virava
    cena branca com o personagem repetido: vai so a 1a figura, num quadrado."""
    from PIL import Image, ImageDraw

    from apps.social import cenas

    folha = Image.new("RGB", (1600, 900), "white")
    desenho = ImageDraw.Draw(folha)
    for i in range(4):  # as 4 vistas, lado a lado
        desenho.rectangle((60 + i * 400, 40, 260 + i * 400, 560), fill=(30, 60, 160))
    for i in range(3):  # os rostos, embaixo
        desenho.ellipse((300 + i * 350, 650, 450 + i * 350, 820), fill=(200, 150, 120))
    figura = cenas.primeira_figura(folha)
    assert figura.size[0] == figura.size[1]
    assert 520 <= figura.size[0] <= 640  # so a 1a vista (520 de altura + margem)
    cores = {c for _n, c in figura.getcolors(1_000_000)}
    assert (200, 150, 120) not in cores  # sem os rostos de baixo

    foto = Image.effect_noise((300, 200), 80).convert("RGB")  # foto comum: inteira
    assert cenas.primeira_figura(foto).size == (300, 300)


@pytest.mark.django_db
def test_so_o_personagem_principal_vira_referencia(ambiente, modulo, settings, tmp_path):
    """Duas referencias se misturavam numa aparencia so (a roupa de um com o
    rosto do outro): vai so a do 1o personagem citado NO TEXTO."""
    from django.core.files.base import ContentFile

    from apps.social import cenas
    from apps.social.models import ElementoVisual

    settings.MEDIA_ROOT = tmp_path
    destino = _destino("instagram")
    for nome, tipo in (("Bia", "personagem"), ("Carlos", "personagem"), ("Caneca", "objeto")):
        e = ElementoVisual(destino=destino, nome=nome, tipo=tipo, descricao=nome)
        e.imagem.save(f"{nome}.png", ContentFile(b"PNG"), save=True)
    principal = cenas.principal_da_cena
    assert principal(_post(destino), {"imagem": "Carlos conversa com a Bia"}).nome == "Carlos"
    assert principal(_post(destino), {"imagem": "a Caneca na mesa e a Bia"}).nome == "Bia"
    assert principal(_post(destino), {"imagem": "a Caneca na mesa"}).nome == "Caneca"
    assert principal(_post(destino), {"imagem": "o escritorio vazio"}) is None


@pytest.mark.django_db
def test_modelo_da_animacao_pelo_env(ambiente, settings, monkeypatch):
    from apps.inference.providers import animacao

    pedidos = []

    class Falso:
        def criar_trabalho(self, **k):
            pedidos.append(k["modelo"])
            return {"id": "v1", "seed": 7}

    conexao = type("C", (), {"default_model": "framepack"})()
    monkeypatch.setattr(fontes, "_animacao", lambda: conexao)
    monkeypatch.setattr(animacao, "get_animation_provider", lambda c: Falso())
    monkeypatch.setattr("apps.inference.leases.reserva", lambda *a, **k: _nada())
    settings.ANIMACAO_MODELO_MENOR, settings.ANIMACAO_MODELO_MAIOR = "rapido", "caprichado"
    assert fontes.pedir_animacao(b"PNG", "p", segundos=2, tamanho="448x800")["model"] == "rapido"
    fontes.pedir_animacao(b"PNG", "p", segundos=2, tamanho="448x800", maior=True)
    assert pedidos == ["rapido", "caprichado"]


def _nada():
    from contextlib import nullcontext

    return nullcontext()


@pytest.mark.django_db
def test_lugar_num_quadro_e_foto_sem_borda_lisa(ambiente, modulo, settings, tmp_path):
    from django.core.files.base import ContentFile
    from PIL import Image, ImageDraw

    from apps.social import cenas
    from apps.social.models import ElementoVisual

    settings.MEDIA_ROOT = tmp_path
    foto = Image.new("RGB", (400, 300), "white")  # a foto baixada, com faixa branca
    ImageDraw.Draw(foto).rectangle((0, 40, 399, 259), fill=(10, 20, 200))
    sem = cenas.sem_bordas(foto)
    assert sem.size == (400, 220) and sem.getpixel((0, 0)) == (10, 20, 200)
    assert cenas.sem_bordas(Image.new("RGB", (50, 50), "white")).size == (50, 50)

    destino = _destino("instagram")
    museu = ElementoVisual(destino=destino, nome="Museu", tipo="lugar", descricao="museu")
    buffer = io.BytesIO()
    foto.save(buffer, format="PNG")
    museu.imagem.save("m.png", ContentFile(buffer.getvalue()), save=True)
    post = _post(destino)
    assert cenas._lugar_da_cena(post, {"imagem": "o Museu num quadro na parede"})[1] == "quadro"
    assert cenas._lugar_da_cena(post, {"imagem": "o Museu na janela"})[1] == "janela"
    assert cenas._lugar_da_cena(post, {"imagem": "o Museu ao entardecer"})[1] == "fundo"


@pytest.mark.django_db
def test_referencia_local_vence_a_folha(ambiente, modulo, settings, tmp_path):
    from django.core.files.base import ContentFile
    from PIL import Image

    from apps.social import cenas
    from apps.social.models import ElementoVisual

    settings.MEDIA_ROOT = tmp_path

    def png(cor, tamanho):
        buffer = io.BytesIO()
        Image.new("RGB", tamanho, cor).save(buffer, format="PNG")
        return buffer.getvalue()

    destino = _destino("instagram")
    carlos = ElementoVisual(destino=destino, nome="Carlos", tipo="personagem", descricao="55")
    carlos.imagem.save("folha.png", ContentFile(png("white", (160, 90))), save=True)
    post = _post(destino)
    cena = {"imagem": "Carlos na mesa"}
    assert len(cenas._referencias(post, cena)) == 1  # sem a local: a folha (recortada)
    carlos.referencia_local.save("local.png", ContentFile(png("gray", (80, 120))), save=True)
    ref = Image.open(io.BytesIO(cenas._referencias(post, cena)[0]))
    assert ref.size == (120, 120) and ref.getpixel((0, 0)) == (128, 128, 128)
    assert "UMA vez so" in carlos.prompt_da_referencia_local and "Carlos" in (
        carlos.prompt_da_referencia_local
    )


def test_imagem_com_faixa_vazia_vira_quadro_cheio():
    """A IA de fora devolveu a cena em cima e uma faixa branca embaixo: a faixa
    sai e a cena entra inteira sobre o fundo desfocado, sem faixa branca."""
    from PIL import Image, ImageDraw

    from apps.social import cenas

    imagem = Image.new("RGB", (572, 1024), (245, 246, 248))
    desenho = ImageDraw.Draw(imagem)
    for x in range(0, 572, 20):  # a cena: listras, nada lisa
        desenho.rectangle((x, 0, x + 9, 679), fill=(30, 60, 160))
        desenho.rectangle((x + 10, 0, x + 19, 679), fill=(200, 120, 40))
    assert cenas.sem_bordas(imagem).size == (572, 680)
    quadro = cenas.encaixar(imagem)
    assert quadro.size == cenas.QUADRO
    assert quadro.getpixel((360, 1270)) != (245, 246, 248)  # o pe nao e mais a faixa
    quase_9_16 = Image.new("RGB", (1024, 1536), (30, 60, 160))  # o 2:3 das IAs: so corta
    assert cenas.encaixar(quase_9_16).size == cenas.QUADRO


def test_previsao_da_animacao_pelo_modelo_e_pelos_trechos(settings):
    from django.utils import timezone

    from apps.social import cenas

    settings.ANIMACAO_MINUTOS_DE_CARGA = 3
    settings.ANIMACAO_MINUTOS_POR_TRECHO_MENOR = 7.5
    settings.ANIMACAO_MINUTOS_POR_TRECHO_MAIOR = 15
    agora = timezone.now().isoformat()
    assert cenas.previsao({"desde": agora, "trechos": 3}).startswith("~26 min, por volta de")
    assert cenas.previsao({"desde": agora, "trechos": 2, "maior": True}).startswith("~33 min")
    assert cenas.previsao({}) == ""


@pytest.mark.django_db
def test_lote_nao_espera_a_cena_refeita_por_outro_botao(
    ambiente, modulo, settings, tmp_path, monkeypatch
):
    """Todas as cenas animadas e a 2 sendo refeita (outra variacao): o lote
    "Animar o que falta" termina e manda montar, sem ficar conferindo a 2."""
    from django.core.files.base import ContentFile
    from django.utils import timezone

    from apps.social import cenas, tasks

    settings.MEDIA_ROOT = tmp_path
    conferidas, fila = [], []
    monkeypatch.setattr(
        fontes, "situacao_da_animacao", lambda i: conferidas.append(i) or {"status": "in_progress"}
    )
    monkeypatch.setattr(fontes, "na_fila", lambda tarefa, *a, **k: fila.append(k["rotulo"]))
    post = _post(
        _destino("instagram"), extras={"video_ia": {"cenas": [{"imagem": "a"}, {"imagem": "b"}]}}
    )
    lista = cenas.cenas(post)
    for n, cena in enumerate(lista, start=1):
        cena["quadro"] = default_storage.save(f"social/{post.pk}/q{n}.png", ContentFile(b"PNG"))
        cena["clipe"] = default_storage.save(f"social/{post.pk}/c{n}.mp4", ContentFile(b"MP4"))
    lista[1]["animando"] = {"id": "v2", "seed": 1, "desde": timezone.now().isoformat()}
    cenas._gravar(post, lista)

    tasks.gerar_cenas.apply(args=[str(post.pk)]).get()
    assert conferidas == [] and fila == ["Montar o video"]


@pytest.mark.django_db
def test_regras_pedem_narracao_continua(ambiente):
    from apps.social import video_ia

    regras = video_ia.regras_das_cenas(_destino("instagram"))
    assert "UMA narracao continua" in regras and "nada de frase solta" in regras


@pytest.mark.django_db
def test_voz_do_video_no_post_e_vazia_volta_a_da_conta(ambiente, modulo):
    from django.urls import reverse

    from apps.social import video

    _, _, client = ambiente
    post = _post(_destino("instagram", voz="pm_alex"), extras={"video_ia": {"cenas": []}})
    assert video.voz_do_post(post) == "pm_alex"
    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    client.post(acao, {"acao": "cenas_voz", "voz": " qwen:carla "})
    post.refresh_from_db()
    assert post.extras["voz"] == "qwen:carla" and video.voz_do_post(post) == "qwen:carla"
    client.post(acao, {"acao": "cenas_voz", "voz": ""})
    post.refresh_from_db()
    assert "voz" not in post.extras and video.voz_do_post(post) == "pm_alex"


@pytest.mark.django_db
def test_nao_fazer_tira_da_fila_o_que_nao_comecou(ambiente, modulo, settings, tmp_path):
    """Na tela do post, a cena com pedido que ainda nao comecou tem "Nao fazer":
    cancela o pedido dela e o lote passa por ela. A que ja anima no worker fica.
    Na Operacao, "Nao fazer" cancela a tarefa; cancelada, ela nao roda mais."""
    from django.core.files.base import ContentFile

    from apps.ops.acompanhamento import Cancelado, acompanhar
    from apps.ops.models import GenerationJob
    from apps.social import cenas

    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    post = _post(
        _destino("instagram"),
        extras={"video_ia": {"cenas": [{"imagem": "a"}, {"imagem": "b"}, {"imagem": "c"}]}},
    )
    lista = cenas.cenas(post)
    for n, cena in enumerate(lista, start=1):
        cena["quadro"] = default_storage.save(f"social/{post.pk}/q{n}.png", ContentFile(b"PNG"))
    lista[0]["animando"] = {"id": "video_1", "seed": 1}
    cenas._gravar(post, lista)

    def tarefa(**kwargs):
        return GenerationJob.objects.create(
            kind=GenerationJob.Kind.TAREFA,
            target_object_id=post.pk,
            status=GenerationJob.Status.WAITING_CAPACITY,
            step_payloads={
                "rotulo": "x",
                "tarefa": "apps.social.tasks.gerar_cenas",
                "kwargs": kwargs,
            },
        )

    da_cena_2, lote = tarefa(n=2), tarefa()
    tela = post.cenas_do_video
    assert [c["na_fila"] for c in tela] == [False, True, True]  # a 1 ja esta no worker
    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "cena_nao_fazer" in pagina

    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    client.post(acao, {"acao": "cena_nao_fazer", "n": "2"})
    da_cena_2.refresh_from_db()
    post.refresh_from_db()
    assert da_cena_2.status == GenerationJob.Status.CANCELED
    assert [c["na_fila"] for c in post.cenas_do_video] == [False, False, True]
    assert cenas.cenas(post)[1]["parada"]

    operacao = client.get(reverse("operacao:trabalhos", urlconf=U)).content.decode()
    assert "nao-fazer" in operacao
    client.post(reverse("operacao:nao_fazer", args=[lote.pk], urlconf=U))
    lote.refresh_from_db()
    assert lote.status == GenerationJob.Status.CANCELED
    with pytest.raises(Cancelado), acompanhar(str(lote.pk)):
        pass
    lote.refresh_from_db()
    assert lote.status == GenerationJob.Status.CANCELED


@pytest.mark.django_db
def test_ajustar_o_final_com_outra_ia_muda_so_o_que_veio(ambiente, modulo):
    """O pedido leva o texto do post e o roteiro; a resposta muda so as cenas e
    os campos que vieram (a imagem e a animacao ficam)."""
    from apps.social import cenas

    _, _, client = ambiente
    post = _post(
        _destino("instagram"),
        texto="Qual destes voce ja viveu? A) um B) dois",
        extras={
            "video_ia": {
                "cenas": [
                    {"fala": "Comeco.", "imagem": "a", "clipe": "social/c1.mp4"},
                    {"fala": "Siga para mais.", "imagem": "b", "movimento": "acena"},
                ]
            }
        },
    )
    pedido = post.pedido_do_final_do_video
    assert "Qual destes voce ja viveu?" in pedido and "Siga para mais." in pedido
    assert '"n": 2' in pedido

    acao = reverse("social:acao_no_post", args=[post.pk], urlconf=U)
    resposta = (
        'Aqui:\n```json\n{"cenas": [{"n": 2, "fala": "Responda nos comentarios!"}, {"n": 9}]}\n```'
    )
    client.post(acao, {"acao": "cenas_ajustar_final", "resposta": resposta})
    post.refresh_from_db()
    lista = cenas.cenas(post)
    assert lista[1]["fala"] == "Responda nos comentarios!" and lista[1]["movimento"] == "acena"
    assert lista[0]["fala"] == "Comeco." and lista[0]["clipe"] == "social/c1.mp4"
    assert (
        cenas.ajustar_final(post, '{"cenas": [{"n": 2, "fala": "Responda nos comentarios!"}]}')
        == []
    )
    with pytest.raises(video.VideoIndisponivel):
        cenas.ajustar_final(post, "sem json")


@pytest.mark.django_db
def test_gravar_todas_as_entonacoes_e_salvar_de_uma_vez(ambiente, modulo, settings, tmp_path):
    """A tela das entonacoes mostra cada cena com a frase; um envio so salva as
    novas (trocando a que havia) e tira as marcadas para excluir."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.social import cenas

    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    post = _post(
        _destino("instagram"),
        extras={
            "video_ia": {
                "cenas": [
                    {"fala": "Primeira frase.", "imagem": "a"},
                    {"fala": "Segunda frase.", "imagem": "b"},
                    {"fala": "", "imagem": "c"},
                ]
            }
        },
    )
    cenas.enviar_fala(post, 2, SimpleUploadedFile("antes.webm", _mp3(1.0), "audio/webm"))
    post.refresh_from_db()
    tela = reverse("social:entonacoes", args=[post.pk], urlconf=U)
    pagina = client.get(tela, {"voltar": "/redes/#video"}).content.decode()
    assert "Primeira frase." in pagina and "Segunda frase." in pagina
    assert (
        "data-salvar" in pagina and "(cena sem fala)" in pagina and 'href="/redes/#video"' in pagina
    )
    revisar = client.get(reverse("social:inicio", urlconf=U), {"aba": "revisar"}).content.decode()
    assert "Gravar as entonacoes (todas as cenas)" in revisar

    resposta = client.post(
        tela,
        {
            "fala_1": SimpleUploadedFile("fala-1.webm", _mp3(1.2), "audio/webm"),
            "excluir_2": "1",
        },
    )
    assert resposta.json() == {"salvas": [1], "tiradas": [2], "erros": []}
    post.refresh_from_db()
    tela_das_cenas = post.cenas_do_video
    assert tela_das_cenas[0]["tem_fala_gravada"] and not tela_das_cenas[1]["tem_fala_gravada"]
