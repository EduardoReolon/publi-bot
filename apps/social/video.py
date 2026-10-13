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
    "em 2 segundos) — e 'formato_sugerido': 'video' quando a ideia ganha com voz "
    "(pergunta direta ao publico, caso ou historia, mito x verdade, passo a passo curto, "
    "explicacao) ou o foco e alcance; 'carrossel' quando e para salvar e consultar "
    "(lista, checklist, tabela, numeros). Diga o porque em "
    "'por_que_formato'. A narracao inteira em ate {palavras} palavras (~60 s).\n"
)


class VideoIndisponivel(RuntimeError):
    """Falta o ffmpeg, a voz ou as laminas."""


def instrucao(post) -> str:
    if post.destino.video == post.destino.Video.NUNCA:
        return ""
    return INSTRUCAO.format(palavras=PALAVRAS_MAXIMAS)


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


def montar(post) -> str:
    """Monta o video do post e o deixa no formato video. Devolve o caminho."""
    ffmpeg, ffprobe = _ffmpeg()
    artigo = laminas.artigo_do_post(post)
    if artigo is None or not (post.extras or {}).get("laminas"):
        raise VideoIndisponivel("o post nao tem laminas para virar video.")
    pngs = laminas.pngs_do_carrossel(post, artigo)
    textos = falas(post)
    fundo = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
    cor = fundo["fundo"].replace("#", "0x")
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        lista = []
        for n, (png, fala) in enumerate(zip(pngs, textos, strict=False), start=1):
            imagem = pasta / f"{n}.png"
            imagem.write_bytes(png)
            segmento = pasta / f"{n}.mp4"
            if fala:
                audio = pasta / f"{n}.mp3"
                audio.write_bytes(_audio(post, fala))
                duracao = _duracao(ffprobe, audio) + PAUSA
                entrada_de_audio = ["-i", str(audio)]
            else:
                duracao = SEM_FALA
                entrada_de_audio = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
            _rodar(
                [
                    ffmpeg, "-y", "-loop", "1", "-framerate", "30", "-i", str(imagem),
                    *entrada_de_audio,
                    "-filter_complex",
                    f"[0:v]scale=1080:1350,pad={LARGURA}:{ALTURA}:0:(oh-ih)/2:color={cor},"
                    "format=yuv420p,fade=t=in:st=0:d=0.25[v];[1:a]apad[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{duracao:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", "30",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                    str(segmento),
                ]
            )  # fmt: skip
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


def voltar_ao_carrossel(post) -> None:
    extras = dict(post.extras or {})
    if extras.get("formato") == "video":
        extras.pop("formato")
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)
