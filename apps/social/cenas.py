"""O video por cenas, feito aqui: cada cena do roteiro (`video_ia`) vira uma
animacao curta do worker, com a fala em voz off e a legenda por codigo, e as
cenas sao coladas com uma transicao curta.

Cada cena, no post (`extras["video_ia"]["cenas"][i]`), alem do roteiro:

- `quadro`: a imagem inicial (o primeiro quadro), e `quadro_de`: "gerado",
  "enviado" ou "lugar" (a foto de um lugar real, usada como esta);
  `quadro_do_texto`: a descricao com que foi gerada (a tela avisa quando a
  descricao mudou e a imagem nao);
- `clipe`: a animacao do quadro, `seed` (a mesma imagem + o mesmo movimento +
  a mesma seed repetem o clipe; refazer a animacao troca a seed) e
  `clipe_do_movimento`;
- `aviso`: o que a ultima geracao nao conseguiu.

A fala nao entra no clipe: a voz e a legenda entram na montagem. Por isso
mudar a fala custa so montar de novo (segundos), e nao animar de novo.

A duracao de cada cena e a da fala (mais um respiro), ou os segundos da cena
no roteiro, o que for maior. O worker gera em trechos
(o FramePack, em pedacos de ~1,1 s) e pode devolver menos que o pedido: o clipe
curto e desacelerado ate 25% e, se ainda faltar, segura o ultimo quadro; o
longo e cortado.

A foto de um lugar real (Identidade visual > "Lugar real (foto)"):
- "de fundo": a cena parte da propria foto (sem personagem, e ela; com
  personagem, a metade de cima da foto fica e a de baixo e pintada com ele);
- "na janela": o PubliBot poe a foto num retangulo (a janela) e o gerador pinta
  a cena em volta, sem tocar nela (a mascara). Os dois pedem a rota de edicao
  do worker (docs/WORKER_IMAGEM_E_CENAS.md); sem ela, a cena sai so pela
  descricao, com aviso.
"""

from __future__ import annotations

import io
import logging
import math
import random
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.social import fontes, identidade, laminas, video, video_ia

logger = logging.getLogger("publibot.social")

QUADRO = (720, 1280)  # o 9:16 que o worker e a Sora animam
TRANSICAO = 0.3  # segundos de fusao entre uma cena e a outra
DESACELERA_ATE = 1.25
PALAVRAS_POR_SEGUNDO = 2.6
DURACAO_MINIMA = 1.5
LEGENDA_Y = 1460  # acima da faixa que o Instagram cobre (legenda do post, botoes)
FRASE_Y = 230


# -- As cenas no post ----------------------------------------------------------------------
def cenas(post) -> list[dict]:
    return [dict(c) for c in video_ia.roteiro(post).get("cenas") or []]


def _gravar(post, lista: list[dict]) -> None:
    extras = dict(post.extras or {})
    extras["video_ia"] = {**(extras.get("video_ia") or {}), "cenas": lista}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])


def _cena(post, n: int) -> tuple[list[dict], dict]:
    lista = cenas(post)
    if not 1 <= n <= len(lista):
        raise video.VideoIndisponivel(f"o video nao tem a cena {n}.")
    return lista, lista[n - 1]


def _existe(caminho: str) -> bool:
    return bool(caminho) and default_storage.exists(caminho)


def editar(post, n: int, **campos) -> None:
    """Muda o roteiro da cena (fala, imagem, movimento, frase, segundos). A
    imagem e o clipe ficam: a tela diz o que refazer."""
    lista, cena = _cena(post, n)
    for campo in ("fala", "imagem", "movimento", "texto"):
        if campo in campos:
            cena[campo] = " ".join(str(campos[campo] or "").split())[:600]
    if "segundos" in campos:
        cena["segundos"] = video_ia._segundos(campos["segundos"])
    _gravar(post, lista)


def para_a_tela(post) -> list[dict]:
    saida = []
    for n, cena in enumerate(cenas(post), start=1):
        quadro, clipe = cena.get("quadro", ""), cena.get("clipe", "")
        saida.append(
            {
                **cena,
                "n": n,
                "tem_quadro": _existe(quadro),
                "tem_clipe": _existe(clipe),
                "imagem_mudou": _existe(quadro)
                and cena.get("quadro_de") == "gerado"
                and cena.get("quadro_do_texto", cena.get("imagem")) != cena.get("imagem"),
                "movimento_mudou": _existe(clipe)
                and cena.get("clipe_do_movimento", cena.get("movimento")) != cena.get("movimento"),
                "duracao": round(duracao_estimada(cena), 1),
            }
        )
    return saida


def duracao_estimada(cena: dict) -> float:
    """O tempo da cena pela fala (antes de ter o audio): ~2,6 palavras/s."""
    palavras = len((cena.get("fala") or "").split())
    pela_fala = palavras / PALAVRAS_POR_SEGUNDO + video.PAUSA if palavras else 0
    return max(pela_fala, float(cena.get("segundos") or 0), DURACAO_MINIMA)


# -- A imagem inicial -----------------------------------------------------------------------
def _png(imagem) -> bytes:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


def _cobrir(imagem, tamanho: tuple[int, int]):
    """A imagem cobrindo o tamanho inteiro, cortada no meio."""
    from PIL import ImageOps

    return ImageOps.fit(imagem.convert("RGB"), tamanho, centering=(0.5, 0.5))


def _abrir(arquivo):
    from PIL import Image, ImageOps

    with arquivo.open("rb") as aberto:
        imagem = ImageOps.exif_transpose(Image.open(aberto))
        imagem.load()
    return imagem.convert("RGB")


def _lugar_da_cena(post, cena: dict):
    """(lugar, "janela" | "fundo") citado na imagem da cena, ou (None, "")."""
    texto = cena.get("imagem") or ""
    achados = identidade.citados(texto, video_ia.lugares(post.destino))
    if not achados:
        return None, ""
    from apps.social.iscas import normalizar

    return achados[0], "janela" if "janela" in normalizar(texto) else "fundo"


def _janela(lugar, cor: str, *, a_direita: bool) -> tuple[bytes, bytes]:
    """(base, mascara) da cena com a foto do lugar numa janela no alto: a foto
    fica (opaca na mascara) e o resto e pintado (transparente)."""
    from PIL import Image, ImageDraw

    largura, altura = QUADRO
    caixa = (int(largura * 0.46), int(altura * 0.24))
    x = largura - caixa[0] - int(largura * 0.07) if a_direita else int(largura * 0.07)
    y = int(altura * 0.07)
    base = Image.new("RGB", QUADRO, cor)
    base.paste(_cobrir(_abrir(lugar.imagem), caixa), (x, y))
    mascara = Image.new("RGBA", QUADRO, (0, 0, 0, 0))
    ImageDraw.Draw(mascara).rectangle((x, y, x + caixa[0], y + caixa[1]), fill=(0, 0, 0, 255))
    return _png(base), _png(mascara)


def _fundo(lugar, com_gente: bool) -> tuple[bytes, bytes | None]:
    """(base, mascara) da cena de fundo: a foto inteira; com personagem, a
    metade de baixo e pintada (transparente) e a de cima fica."""
    from PIL import Image, ImageDraw

    base = _cobrir(_abrir(lugar.imagem), QUADRO)
    if not com_gente:
        return _png(base), None
    largura, altura = QUADRO
    mascara = Image.new("RGBA", QUADRO, (0, 0, 0, 0))
    ImageDraw.Draw(mascara).rectangle((0, 0, largura, int(altura * 0.45)), fill=(0, 0, 0, 255))
    return _png(base), _png(mascara)


def _referencias(post, cena: dict) -> list[bytes]:
    """As imagens dos personagens (e objetos e cenarios) citados, ate 3."""
    saida = []
    for elemento in identidade.citados(
        cena.get("imagem") or "", identidade.elementos(post.destino)
    ):
        if elemento.tipo == elemento.Tipo.LUGAR or not elemento.imagem:
            continue
        saida.append(_png(_abrir(elemento.imagem)))
        if len(saida) == 3:
            break
    return saida


def _guardar(post, nome: str, conteudo: bytes) -> str:
    caminho = f"social/{post.pk}/{nome}"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    return default_storage.save(caminho, ContentFile(conteudo))


def gerar_quadro(post, n: int) -> str:
    """A imagem inicial da cena n pelo gerador daqui (com as referencias dos
    personagens e a foto do lugar, quando o worker tem a rota de edicao)."""
    lista, cena = _cena(post, n)
    lugar, como = _lugar_da_cena(post, cena)
    personagens = [
        e
        for e in identidade.citados(cena.get("imagem") or "", identidade.elementos(post.destino))
        if e.tipo != e.Tipo.LUGAR
    ]
    aviso = ""
    if lugar is not None and como == "fundo" and not personagens:
        conteudo, de = _png(_cobrir(_abrir(lugar.imagem), QUADRO)), "lugar"
    else:
        prompts = identidade.prompts_da_cena(
            post,
            {"visual": cena.get("imagem", ""), "movimento": cena.get("movimento", "")},
            chave=f"cena-{n}",
            prompt="social_quadro",
        )
        cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
        base = mascara = None
        if lugar is not None and como == "janela":
            texto = cena.get("imagem") or ""
            base, mascara = _janela(lugar, cores["fundo"], a_direita="direita" in texto.lower())
        elif lugar is not None:
            base, mascara = _fundo(lugar, com_gente=True)
        conteudo, aviso = fontes.gerar_quadro(
            prompts["imagem"],
            tamanho=f"{QUADRO[0]}x{QUADRO[1]}",
            referencias=_referencias(post, cena),
            base=base,
            mascara=mascara,
        )
        de = "gerado"
    post.refresh_from_db()
    lista, cena = _cena(post, n)
    cena.update(
        quadro=_guardar(post, f"cena-{n}-quadro.png", conteudo),
        quadro_de=de,
        quadro_do_texto=cena.get("imagem", ""),
        aviso=aviso,
    )
    cena.pop("clipe", None)  # imagem nova, animacao nova
    _gravar(post, lista)
    return cena["quadro"]


def enviar_quadro(post, n: int, arquivo) -> str:
    """A imagem inicial feita fora (ou uma foto): vira o quadro da cena n."""
    from PIL import UnidentifiedImageError

    try:
        imagem = _abrir(arquivo)
    except (UnidentifiedImageError, OSError) as exc:
        raise video.VideoIndisponivel("envie uma imagem JPG, PNG ou WebP.") from exc
    lista, cena = _cena(post, n)
    cena.update(
        quadro=_guardar(post, f"cena-{n}-quadro.png", _png(_cobrir(imagem, QUADRO))),
        quadro_de="enviado",
        quadro_do_texto=cena.get("imagem", ""),
        aviso="",
    )
    cena.pop("clipe", None)
    _gravar(post, lista)
    return cena["quadro"]


# -- A animacao -----------------------------------------------------------------------------
def segundos_pedidos(cena: dict) -> int:
    """Quantos segundos pedir ao worker: o tempo da cena, arredondado para cima
    (ele arredonda os trechos para baixo; a montagem acerta o resto)."""
    return max(2, min(math.ceil(duracao_estimada(cena)), 6))


def animar(post, n: int, *, nova_seed: bool = False) -> str:
    """O clipe da cena n a partir do quadro. `nova_seed`: outra variacao do
    mesmo movimento (refazer a animacao)."""
    lista, cena = _cena(post, n)
    if not _existe(cena.get("quadro", "")):
        raise video.VideoIndisponivel(f"a cena {n} ainda nao tem imagem inicial.")
    seed = cena.get("seed")
    if nova_seed or seed is None:
        seed = random.randint(1, 2**31 - 1)  # noqa: S311 - so variacao, nao seguranca
    prompts = identidade.prompts_da_cena(
        post,
        {"visual": cena.get("imagem", ""), "movimento": cena.get("movimento", "")},
        chave=f"cena-{n}",
        prompt="social_quadro",
    )
    with default_storage.open(cena["quadro"], "rb") as arquivo:
        quadro = arquivo.read()
    clipe = fontes.animar(
        quadro,
        prompts["movimento"] or prompts["imagem"],
        segundos=segundos_pedidos(cena),
        tamanho=f"{QUADRO[0]}x{QUADRO[1]}",
        seed=seed,
    )
    post.refresh_from_db()
    lista, cena = _cena(post, n)
    cena.update(
        clipe=_guardar(post, f"cena-{n}-clipe.mp4", clipe),
        seed=seed,
        clipe_do_movimento=cena.get("movimento", ""),
    )
    _gravar(post, lista)
    return cena["clipe"]


def faltam(post) -> list[int]:
    """As cenas sem clipe."""
    return [n for n, c in enumerate(cenas(post), start=1) if not _existe(c.get("clipe", ""))]


# -- A montagem -----------------------------------------------------------------------------
def _segmento(pasta: Path, n: int, cena: dict, post, cores: dict, ffmpeg: str, ffprobe: str):
    """Uma cena: o clipe (ou o quadro, aproximando devagar) no tempo da fala,
    com a legenda e a frase por cima e a voz. Devolve (arquivo, duracao)."""
    largura, altura = video.LARGURA, video.ALTURA
    audio = None
    fala = (cena.get("fala") or "").strip()
    if fala and fontes.tem_voz():
        audio = video._guardar_em(pasta, f"{n}.mp3", video._audio(post, fala))
        # O gesto planejado tem o seu tempo: fala curta nao corta a cena.
        duracao = max(video._duracao(ffprobe, audio) + video.PAUSA, cena.get("segundos") or 0)
    else:
        duracao = duracao_estimada(cena)
    duracao = max(duracao, DURACAO_MINIMA)
    if _existe(cena.get("clipe", "")):
        clipe = video._copiar_do_armazenamento(pasta, f"{n}-clipe.mp4", cena["clipe"])
        tem = max(video._duracao(ffprobe, clipe), 0.1)
        fator = min(max(duracao / tem, 1.0), DESACELERA_ATE)
        entradas = ["-i", str(clipe)]
        fundo = (
            f"[0:v]scale={largura}:{altura}:force_original_aspect_ratio=increase,"
            f"crop={largura}:{altura},fps=30,setpts={fator:.4f}*PTS,"
            f"tpad=stop_mode=clone:stop_duration={duracao:.2f},"
            f"trim=duration={duracao:.2f},setsar=1[bg]"
        )
    elif _existe(cena.get("quadro", "")):
        quadro = video._copiar_do_armazenamento(pasta, f"{n}-quadro.png", cena["quadro"])
        quadros = max(1, int(duracao * 30))
        entradas = ["-loop", "1", "-i", str(quadro)]
        fundo = (
            f"[0:v]scale={largura * 2}:{altura * 2},zoompan=z='min(1+0.0004*on,1.12)':"
            f"d={quadros}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={largura}x{altura}:"
            "fps=30,setsar=1[bg]"
        )
    else:
        raise video.VideoIndisponivel(f"a cena {n} ainda nao tem imagem nem animacao.")
    entradas += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    filtros, rotulo, proxima = [fundo], "bg", 2
    sobreposicoes = []
    if cena.get("texto") and post.destino.texto_no_video == "frases":
        png = video._guardar_em(
            pasta,
            f"{n}-frase.png",
            video.png_do_texto(cena["texto"], cores, tamanho=64, cor=cores["destaque"]),
        )
        sobreposicoes.append((png, FRASE_Y, 0.2, duracao))
    if fala and post.destino.texto_no_video == "legendas":
        trechos = video.trechos_da_legenda(fala)
        falado = duracao - video.PAUSA
        for i, (trecho, (inicio, fim)) in enumerate(
            zip(trechos, video.tempos_da_legenda(trechos, falado), strict=True)
        ):
            png = video._guardar_em(
                pasta, f"{n}-l{i}.png", video.png_do_texto(trecho, cores, cor=cores["destaque"])
            )
            sobreposicoes.append((png, LEGENDA_Y, inicio, fim))
    for i, (png, y, inicio, fim) in enumerate(sobreposicoes):
        entradas += ["-loop", "1", "-i", str(png)]
        filtros.append(
            f"[{rotulo}][{proxima}:v]overlay=0:{y}:enable='between(t,{inicio:.2f},{fim:.2f})'[s{i}]"
        )
        rotulo, proxima = f"s{i}", proxima + 1
    filtros.append(f"[{rotulo}]format=yuv420p[saida]")
    filtros.append("[1:a]apad[som]")
    saida = pasta / f"cena-{n}.mp4"
    video._rodar(
        [
            ffmpeg, "-y", *entradas,
            "-filter_complex", ";".join(filtros),
            "-map", "[saida]", "-map", "[som]", "-t", f"{duracao:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            str(saida),
        ]
    )  # fmt: skip
    return saida, duracao


def _juntar(ffmpeg: str, pasta: Path, partes: list[tuple[Path, float]]) -> Path:
    """As cenas coladas com uma fusao curta (imagem e som)."""
    final = pasta / "video.mp4"
    if len(partes) == 1:
        return partes[0][0]
    entradas, filtros = [], []
    for arquivo, _duracao in partes:
        entradas += ["-i", str(arquivo)]
    video_atual, som_atual, decorrido = "0:v", "0:a", partes[0][1]
    for i in range(1, len(partes)):
        deslocamento = decorrido - TRANSICAO * i
        filtros.append(
            f"[{video_atual}][{i}:v]xfade=transition=fade:duration={TRANSICAO}:"
            f"offset={deslocamento:.2f}[v{i}]"
        )
        filtros.append(f"[{som_atual}][{i}:a]acrossfade=d={TRANSICAO}[a{i}]")
        video_atual, som_atual = f"v{i}", f"a{i}"
        decorrido += partes[i][1]
    video._rodar(
        [
            ffmpeg, "-y", *entradas,
            "-filter_complex", ";".join(filtros),
            "-map", f"[{video_atual}]", "-map", f"[{som_atual}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(final),
        ]
    )  # fmt: skip
    return final


def montar(post) -> str:
    """O video do post: todas as cenas no tempo das falas, com a voz e as
    legendas. Cena sem animacao ainda entra com o quadro aproximando devagar
    (da para ver o video antes de animar tudo)."""
    ffmpeg, ffprobe = video._ffmpeg()
    lista = cenas(post)
    if not lista:
        raise video.VideoIndisponivel("o post nao tem cenas de video.")
    cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        partes = [
            _segmento(pasta, n, cena, post, cores, ffmpeg, ffprobe)
            for n, cena in enumerate(lista, start=1)
        ]
        final = _juntar(ffmpeg, pasta, partes)
        return video_ia.guardar(post, final.read_bytes(), ".mp4", de="cenas")


# -- O pedido das imagens iniciais para uma IA de imagem de fora ---------------------------
PEDIDO_DAS_IMAGENS = """\
Gere as imagens iniciais (o PRIMEIRO quadro) das cenas abaixo, uma por cena, na
ordem, vertical 9:16 (1080 x 1920). Cada uma vai ser animada depois (um gesto
lento a partir dela), entao:

- NAO escreva nenhum texto, letra, numero ou logotipo na imagem.
- A pose e a do COMECO do gesto descrito; boca fechada (a voz e de narrador).
- Estilo (o mesmo em todas): {estilo}.
- Cores da marca: fundo {fundo}, destaque {destaque}.
- Nada de pessoas reais reconheciveis ou marcas de terceiros.
{elementos}
"""


def pedido_das_imagens(post) -> str:
    """O texto para colar numa IA de imagem (com as referencias anexadas): as
    imagens iniciais de todas as cenas, para enviar cada uma na sua cena."""
    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    texto = PEDIDO_DAS_IMAGENS.format(
        estilo=identidade.descricao_do_estilo(destino, identidade.estilo_do_post(post))
        or "o das imagens da conta",
        fundo=cores["fundo"],
        destaque=cores["destaque"],
        elementos=(
            "- Personagens e lugares fixos (vou anexar as imagens; desenhe sempre igual):\n"
            + identidade.ficha(destino)
            + "\n"
            if identidade.elementos(destino)
            else ""
        ),
    )
    for n, cena in enumerate(cenas(post), start=1):
        texto += f"\nCena {n}: {cena.get('imagem', '')}"
        if cena.get("movimento"):
            texto += f" (o gesto que vem depois: {cena['movimento']})"
    return texto + "\n"
