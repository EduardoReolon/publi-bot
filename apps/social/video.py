"""Video (Reels / Shorts): as laminas do carrossel narradas por uma voz de IA.

O fluxo, na pratica:

1. Quem escreve o post (a nossa IA ou a outra) devolve, em cada lamina, a
   `fala` — o que a voz diz naquela lamina, como quem conversa (nao le a
   lamina; completa) — e `formato_sugerido`: video quando a ideia ganha com
   narracao (historia, passo a passo, explicacao) ou o foco e alcance (o Reels
   e o que mais chega a quem nao segue); carrossel quando e para salvar e
   consultar (lista, checklist, numeros).
2. A conta decide (`Destino.video`): a IA so sugere, o video sai sozinho quando
   ela sugere, ou nunca.
3. `montar`: cada fala vira audio pela conexao de voz (ElevenLabs, OpenAI,
   Google — `fontes.falar`), guardado por texto+voz (refazer o video depois de
   mexer numa lamina so paga a fala que mudou); cada lamina fica na tela o
   tempo da fala dela, no quadro 9:16 (1080 x 1920, a lamina 4:5 no meio, na
   cor de fundo da conta); o ffmpeg junta tudo num MP4 (H.264 + AAC).
4. O post passa ao formato video: a imagem publicada e o video, e o Instagram
   recebe como Reels (o publicador ja publica video).

Sem conexao de voz, o roteiro (`roteiro`) sai para gravar fora (a propria voz,
CapCut, HeyGen) e o video pronto entra pelo post proprio.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.social import fontes, laminas

logger = logging.getLogger("publibot.social")

LARGURA, ALTURA = 1080, 1920
PAUSA = 0.35  # segundos de respiro depois de cada fala
SEM_FALA = 2.5  # segundos de uma lamina sem fala
PALAVRAS_MAXIMAS = 160  # ~60 s de fala: o tamanho bom de um Reels de conteudo
FORMATOS = ("carrossel", "video")

INSTRUCAO = (
    "VIDEO: devolva tambem, em cada lamina, 'fala' — o que uma voz diz naquela lamina, "
    "como quem conversa (1 a 2 frases, nao leia a lamina: complete; a primeira prende "
    "em 2 segundos) — e 'movimento': o que se mexe na cena se ela virar animacao, curto "
    "e lento, sem cortes (ex.: 'o macaco pisca e aponta para a placa; a camera se "
    "aproxima devagar'). A narracao inteira em ate {palavras} palavras (~60 s).{orientacao}\n"
)
INSTRUCAO_DO_FORMATO = (
    "Devolva 'formato_sugerido': 'video' quando a ideia ganha com voz (pergunta direta ao "
    "publico, caso ou historia, mito x verdade, passo a passo curto, explicacao) ou o foco "
    "e alcance; 'carrossel' quando e para salvar e consultar (lista, checklist, tabela, "
    "numeros). Diga o porque em 'por_que_formato'.\n"
)


class VideoIndisponivel(RuntimeError):
    """Falta o ffmpeg, a voz ou as laminas."""


def instrucao(post) -> str:
    destino = post.destino
    if destino.video == destino.Video.NUNCA:
        return ""
    orientacao = destino.orientacao_videos.strip()
    texto = INSTRUCAO.format(
        palavras=PALAVRAS_MAXIMAS,
        orientacao=f" Orientacoes da conta para os videos: {orientacao}." if orientacao else "",
    )
    if (post.extras or {}).get("formato_pedido") == "video":
        texto += "Este post vai como VIDEO (foi o formato pedido): capriche nas falas.\n"
    elif destino.video != destino.Video.SEMPRE:  # sempre video: nao ha o que sugerir
        texto += INSTRUCAO_DO_FORMATO
    return texto


def sai_sozinho(post) -> bool:
    """O video sai sem ninguem clicar: a conta quer sempre video, ou quer
    quando a IA sugere e ela sugeriu."""
    destino, extras = post.destino, post.extras or {}
    if not extras.get("laminas"):
        return False
    if destino.video == destino.Video.SEMPRE:
        return True
    return destino.video == destino.Video.AUTOMATICO and extras.get("formato_sugerido") == "video"


def falas(post) -> list[str]:
    """A fala de cada lamina (a chamada final incluida). Sem fala escrita, o
    titulo e o texto da lamina."""
    itens = laminas.com_a_chamada(
        (post.extras or {}).get("laminas") or [], post.destino.chamada_final or laminas.CHAMADA
    )
    saida = []
    for item in itens:
        fala = (item.get("fala") or "").strip()
        if not fala:
            partes = [item.get("titulo", "").strip(), item.get("texto", "").strip()]
            fala = ". ".join(p.rstrip(".") for p in partes if p)
        saida.append(fala)
    return saida


def palavras_da_narracao(post) -> int:
    return sum(len(f.split()) for f in falas(post))


def avisos(extras: dict) -> list[str]:
    """O que conferir no video sugerido (por algoritmo)."""
    if extras.get("formato_sugerido") != "video":
        return []
    total = sum(len((x.get("fala") or "").split()) for x in extras.get("laminas") or [])
    if total > PALAVRAS_MAXIMAS * 1.2:
        return [
            f"narracao longa ({total} palavras, ~{total * 60 // 150} s): Reels de conteudo "
            "rende ate ~60 s"
        ]
    return []


def roteiro(post) -> str:
    """O roteiro para gravar fora (a propria voz, CapCut, HeyGen): a fala e a
    imagem de cada lamina, em ordem."""
    itens = laminas.com_a_chamada(
        (post.extras or {}).get("laminas") or [], post.destino.chamada_final or laminas.CHAMADA
    )
    linhas = []
    for n, (item, fala) in enumerate(zip(itens, falas(post), strict=False), start=1):
        tela = " — ".join(p for p in (item.get("titulo", ""), item.get("texto", "")) if p)
        linhas.append(
            f"{n}. TELA: {tela}\n   FALA: {fala}"
            + (f"\n   IMAGEM: {item['visual']}" if item.get("visual") else "")
            + (f"\n   MOVIMENTO: {item['movimento']}" if item.get("movimento") else "")
        )
    return "\n".join(linhas)


# -- Montar -------------------------------------------------------------------------------
def _ffmpeg() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise VideoIndisponivel("o ffmpeg nao esta instalado no servidor (o deploy instala).")
    return ffmpeg, ffprobe


def _audio(post, fala: str) -> bytes:
    """O audio da fala, guardado por voz+texto (so paga a fala que mudou)."""
    voz = post.destino.voz
    chave = hashlib.sha256(f"{voz}\n{fala}".encode()).hexdigest()[:32]
    caminho = f"social/audios/{chave}.mp3"
    if default_storage.exists(caminho):
        with default_storage.open(caminho, "rb") as arquivo:
            return arquivo.read()
    audio = fontes.falar(fala, voz)
    default_storage.save(caminho, ContentFile(audio))
    return audio


def _duracao(ffprobe: str, arquivo: Path) -> float:
    saida = subprocess.run(  # noqa: S603 - ffmpeg do sistema, argumentos montados aqui
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(arquivo),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(saida.stdout.strip() or 0)


def _rodar(comando: list[str]) -> None:
    resultado = subprocess.run(comando, capture_output=True, text=True)  # noqa: S603
    if resultado.returncode != 0:
        raise VideoIndisponivel(f"o ffmpeg falhou: {resultado.stderr[-500:]}")


# -- Legendas ------------------------------------------------------------------------------
PALAVRAS_POR_LEGENDA = 5
LETRAS_POR_LEGENDA = 30
ALTURA_DA_LEGENDA = 170


def trechos_da_legenda(fala: str) -> list[str]:
    """A fala em pedacos curtos (ate 5 palavras / ~30 letras): uma legenda
    por vez, como nos Reels, para ler sem esforco."""
    trechos, atual = [], []
    for palavra in fala.split():
        candidato = " ".join([*atual, palavra])
        if atual and (len(atual) >= PALAVRAS_POR_LEGENDA or len(candidato) > LETRAS_POR_LEGENDA):
            trechos.append(" ".join(atual))
            atual = []
        atual.append(palavra)
        if palavra[-1:] in ".!?;:" and len(atual) >= 2:  # a pausa da frase fecha o trecho
            trechos.append(" ".join(atual))
            atual = []
    if atual:
        trechos.append(" ".join(atual))
    return trechos


def tempos_da_legenda(trechos: list[str], duracao: float) -> list[tuple[float, float]]:
    """O tempo de cada trecho, proporcional ao numero de letras (a voz leva
    mais tempo no trecho mais longo)."""
    total = sum(len(t) for t in trechos) or 1
    tempos, inicio = [], 0.0
    for trecho in trechos:
        fim = inicio + duracao * len(trecho) / total
        tempos.append((round(inicio, 2), round(fim, 2)))
        inicio = fim
    return tempos


def _png_da_legenda(trecho: str, cores: dict) -> bytes:
    """O trecho em letra grande, branca, numa faixa escura arredondada."""
    import io

    from PIL import Image, ImageDraw

    fonte = laminas._fonte(52, negrito=True)
    imagem = Image.new("RGBA", (LARGURA, ALTURA_DA_LEGENDA), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    linhas = laminas._quebrar(desenho, trecho, fonte, LARGURA - 200)[:2]
    altura_da_linha = int(fonte.size * 1.2)
    altura = altura_da_linha * len(linhas)
    largura = max(desenho.textlength(linha, font=fonte) for linha in linhas)
    topo = (ALTURA_DA_LEGENDA - altura) // 2
    desenho.rounded_rectangle(
        ((LARGURA - largura) / 2 - 28, topo - 14, (LARGURA + largura) / 2 + 28, topo + altura + 14),
        radius=22,
        fill=(0, 0, 0, 175),
    )
    for n, linha in enumerate(linhas):
        x = (LARGURA - desenho.textlength(linha, font=fonte)) / 2
        desenho.text((x, topo + n * altura_da_linha), linha, font=fonte, fill=cores["texto"])
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


# -- Montar -------------------------------------------------------------------------------
# A lamina 4:5 no quadro 9:16, um pouco menor (90%) e mais para cima: fora das
# faixas que o Instagram cobre (o nome da conta em cima; a legenda do post, os
# botoes e o audio embaixo). A legenda falada fica logo abaixo da lamina.
ESCALA = 0.9
CAIXA = (round(laminas.LARGURA * ESCALA / 2) * 2, round(laminas.ALTURA * ESCALA / 2) * 2)
CAIXA_X, CAIXA_Y = (LARGURA - CAIXA[0]) // 2, 190
LEGENDA_Y = CAIXA_Y + CAIXA[1] + 20
ZOOM_POR_SEGUNDO = 0.012  # o fundo parado aproxima devagar (efeito Ken Burns)
TEXTO_ENTRA = (0.15, 0.45)  # o texto aparece aos 0,15 s, em 0,45 s


def _segmento(
    ffmpeg: str,
    pasta: Path,
    n: int,
    *,
    fundo: Path,
    clipe: Path | None,
    texto: Path,
    audio: Path | None,
    duracao: float,
    legendas: list[tuple[Path, float, float]],
    cor: str,
) -> Path:
    """Uma lamina: o fundo (clipe em loop ou imagem com zoom lento), o texto
    entrando por cima, as legendas da fala e o audio."""
    largura, altura = CAIXA
    quadros = max(1, int(duracao * 30))
    entradas = ["-f", "lavfi", "-i", f"color=c={cor}:s={LARGURA}x{ALTURA}:r=30:d={duracao:.2f}"]
    if clipe:
        entradas += ["-stream_loop", "-1", "-i", str(clipe), "-loop", "1", "-i", str(fundo)]
        filtros = [
            f"[1:v]scale={largura}:{altura}:force_original_aspect_ratio=increase,"
            f"crop={largura}:{altura},fps=30,setsar=1[clipe]",
            f"[2:v]scale={largura}:{altura},format=rgba[veu]",
            "[clipe][veu]overlay=format=auto[bg]",
        ]
        proxima = 3
    else:
        zoom = f"min(1+{ZOOM_POR_SEGUNDO / 30:.6f}*on,1.15)"
        entradas += ["-i", str(fundo)]
        filtros = [
            f"[1:v]scale={largura * 2}:{altura * 2},zoompan=z='{zoom}':d={quadros}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={largura}x{altura}:fps=30,"
            "setsar=1[bg]"
        ]
        proxima = 2
    entradas += ["-loop", "1", "-i", str(texto)]
    entrada_do_texto = proxima
    proxima += 1
    if audio:
        entradas += ["-i", str(audio)]
    else:
        entradas += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    entrada_do_audio = proxima
    proxima += 1
    comeco, duracao_da_entrada = TEXTO_ENTRA
    filtros += [
        f"[{entrada_do_texto}:v]scale={largura}:{altura},format=rgba,"
        f"fade=t=in:st={comeco}:d={duracao_da_entrada}:alpha=1[txt]",
        f"[0:v][bg]overlay={CAIXA_X}:{CAIXA_Y}[a0]",
        f"[a0][txt]overlay={CAIXA_X}:{CAIXA_Y}[v0]",
    ]
    rotulo = "v0"
    for i, (png, inicio, fim) in enumerate(legendas):
        entradas += ["-loop", "1", "-i", str(png)]
        filtros.append(
            f"[{rotulo}][{proxima}:v]overlay=0:{LEGENDA_Y}:"
            f"enable='between(t,{inicio:.2f},{fim:.2f})'[v{i + 1}]"
        )
        rotulo = f"v{i + 1}"
        proxima += 1
    filtros.append(f"[{rotulo}]format=yuv420p[saida]")
    filtros.append(f"[{entrada_do_audio}:a]apad[som]")
    segmento = pasta / f"{n}.mp4"
    _rodar(
        [
            ffmpeg, "-y", *entradas,
            "-filter_complex", ";".join(filtros),
            "-map", "[saida]", "-map", "[som]", "-t", f"{duracao:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            str(segmento),
        ]
    )  # fmt: skip
    return segmento


def _guardar_em(pasta: Path, nome: str, conteudo: bytes) -> Path:
    arquivo = pasta / nome
    arquivo.write_bytes(conteudo)
    return arquivo


def _copiar_do_armazenamento(pasta: Path, nome: str, caminho: str) -> Path:
    with default_storage.open(caminho, "rb") as arquivo:
        return _guardar_em(pasta, nome, arquivo.read())


def montar(post) -> str:
    """Monta o video do post e o deixa no formato video. Devolve o caminho.

    Cada lamina em camadas: o fundo (a imagem, aproximando devagar, ou o clipe
    de video da lamina, em loop, escurecido), o texto da lamina por cima
    (nitido, entrando em fade — e o mesmo texto do carrossel), a legenda da
    fala embaixo (sincronizada pelo tamanho de cada trecho) e a narracao."""
    ffmpeg, ffprobe = _ffmpeg()
    artigo = laminas.artigo_do_post(post)
    if artigo is None or not (post.extras or {}).get("laminas"):
        raise VideoIndisponivel("o post nao tem laminas para virar video.")
    fundos = laminas.pngs_do_carrossel(post, artigo, camada="fundo")
    textos = laminas.pngs_do_carrossel(post, artigo, camada="texto")
    clipes = laminas.clipes_das_laminas(post)
    cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
    cor = cores["fundo"].replace("#", "0x")
    com_legenda = post.destino.legendas
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        lista = []
        for n, (fundo, texto, fala) in enumerate(
            zip(fundos, textos, falas(post), strict=False), start=1
        ):
            audio, legendas = None, []
            if fala:
                audio = _guardar_em(pasta, f"{n}.mp3", _audio(post, fala))
                falado = _duracao(ffprobe, audio)
                duracao = falado + PAUSA
                if com_legenda:
                    trechos = trechos_da_legenda(fala)
                    for i, (trecho, (inicio, fim)) in enumerate(
                        zip(trechos, tempos_da_legenda(trechos, falado), strict=True)
                    ):
                        png = _guardar_em(pasta, f"{n}-l{i}.png", _png_da_legenda(trecho, cores))
                        legendas.append((png, inicio, fim))
            else:
                duracao = SEM_FALA
            clipe = (
                _copiar_do_armazenamento(pasta, f"{n}-clipe{Path(clipes[n]).suffix}", clipes[n])
                if n in clipes
                else None
            )
            segmento = _segmento(
                ffmpeg,
                pasta,
                n,
                fundo=_guardar_em(pasta, f"{n}-fundo.png", fundo),
                clipe=clipe,
                texto=_guardar_em(pasta, f"{n}-texto.png", texto),
                audio=audio,
                duracao=duracao,
                legendas=legendas,
                cor=cor,
            )
            lista.append(f"file '{segmento}'")
        (pasta / "lista.txt").write_text("\n".join(lista))
        final = pasta / "video.mp4"
        _rodar(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(pasta / "lista.txt"),
                "-c", "copy", "-movflags", "+faststart", str(final),
            ]
        )  # fmt: skip
        caminho = f"social/{post.pk}/video.mp4"
        if default_storage.exists(caminho):
            default_storage.delete(caminho)
        default_storage.save(caminho, ContentFile(final.read_bytes()))
    post.extras = {**(post.extras or {}), "video": caminho, "formato": "video"}
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.preparar_imagens(post, artigo)
    return caminho


# -- Clipes (o fundo animado de uma lamina) ------------------------------------------------
TIPOS_DE_CLIPE = {".mp4", ".mov", ".webm", ".m4v"}
CLIPE_MAXIMO = 80 * 1024 * 1024
QUADRO_DA_ANIMACAO = (720, 1280)  # o 9:16 que Sora e worker aceitam


def imagem_da_lamina(post, n: int) -> str:
    """O caminho da imagem de fundo da lamina n, sem texto e sem escurecer
    (a que vai ser animada). "" se ela so tem a cor da conta."""
    artigo = laminas.artigo_do_post(post)
    if n == 1:
        return laminas.foto_de_fundo(post, artigo) if artigo else ""
    chamada = post.destino.chamada_final or laminas.CHAMADA
    itens = laminas.com_a_chamada((post.extras or {}).get("laminas") or [], chamada)
    fundo = laminas.fundos_das_laminas(post, itens).get(n)
    return fundo["caminho"] if fundo else ""


def _no_quadro_da_animacao(caminho: str, cor: str) -> bytes:
    """A imagem 4:5 inteira no meio do quadro 9:16, com a cor da conta em volta:
    o clipe volta 9:16 e o video recorta o meio, que e a imagem original."""
    import io

    from PIL import Image

    largura, altura = QUADRO_DA_ANIMACAO
    with default_storage.open(caminho, "rb") as arquivo:
        imagem = Image.open(arquivo).convert("RGB")
        imagem.load()
    caixa = (largura, round(largura * laminas.ALTURA / laminas.LARGURA))
    escala = max(caixa[0] / imagem.width, caixa[1] / imagem.height)
    imagem = imagem.resize((int(imagem.width * escala) + 1, int(imagem.height * escala) + 1))
    x, y = (imagem.width - caixa[0]) // 2, (imagem.height - caixa[1]) // 2
    imagem = imagem.crop((x, y, x + caixa[0], y + caixa[1]))
    quadro = Image.new("RGB", (largura, altura), cor)
    quadro.paste(imagem, (0, (altura - caixa[1]) // 2))
    buffer = io.BytesIO()
    quadro.save(buffer, format="PNG")
    return buffer.getvalue()


def _guardar_clipe(post, n: int, conteudo: bytes, extensao: str = ".mp4") -> str:
    caminho = f"social/{post.pk}/clipe-{n}{extensao}"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    caminho = default_storage.save(caminho, ContentFile(conteudo))
    extras = dict(post.extras or {})
    extras["clipes"] = {**(extras.get("clipes") or {}), str(n): caminho}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    return caminho


def animar_lamina(post, n: int) -> str:
    """Anima a imagem da lamina n (o worker ou a Sora) e guarda o clipe."""
    from apps.social import identidade

    caminho = imagem_da_lamina(post, n)
    if not caminho:
        raise VideoIndisponivel(
            f"a lamina {n} nao tem imagem para animar: gere ou envie a imagem antes."
        )
    cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
    prompt = identidade.prompts_da_lamina(post, n).get("movimento") or (
        "subtle slow motion of the scene, static camera, no cuts"
    )
    clipe = fontes.animar(_no_quadro_da_animacao(caminho, cores["fundo"]), prompt)
    return _guardar_clipe(post, n, clipe)


def enviar_clipe(post, n: int, arquivo) -> str:
    """O clipe que a pessoa fez fora (Sora, Runway, Kling, CapCut) para a lamina n."""
    extensao = Path(getattr(arquivo, "name", "") or "clipe.mp4").suffix.lower() or ".mp4"
    if extensao not in TIPOS_DE_CLIPE:
        raise VideoIndisponivel(f"formato {extensao} nao aceito (mp4, mov, webm).")
    if getattr(arquivo, "size", 0) > CLIPE_MAXIMO:
        raise VideoIndisponivel("clipe grande demais (ate 80 MB).")
    return _guardar_clipe(post, n, arquivo.read(), extensao)


def tirar_clipe(post, n: int) -> None:
    extras = dict(post.extras or {})
    clipes = dict(extras.get("clipes") or {})
    caminho = clipes.pop(str(n), "")
    if caminho and default_storage.exists(caminho):
        default_storage.delete(caminho)
    extras["clipes"] = clipes
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])


def voltar_ao_carrossel(post) -> None:
    extras = dict(post.extras or {})
    if extras.get("formato") == "video":
        extras.pop("formato")
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)
