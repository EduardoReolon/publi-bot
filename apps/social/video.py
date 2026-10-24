"""As pecas de video que o resto usa: o ffmpeg, a voz guardada por texto e as
legendas da fala.

Quem monta o video e `cenas.py` (o video por cenas: imagem inicial, animacao
pelo worker, voz em off e legenda por cena). O video narrado das laminas, que
vivia aqui, saiu: as cenas o substituem.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.social import fontes, laminas

LARGURA, ALTURA = 1080, 1920
PAUSA = 0.35  # segundos de respiro depois de cada fala


class VideoIndisponivel(RuntimeError):
    """Falta o ffmpeg, a voz, a imagem ou a animacao de uma cena."""


def _ffmpeg() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise VideoIndisponivel("o ffmpeg nao esta instalado no servidor (o deploy instala).")
    return ffmpeg, ffprobe


def voz_do_post(post) -> str:
    """A voz da narracao: a escolhida neste post ou, sem ela, a da conta
    (Identidade visual). Vazio nas duas: a padrao do servico."""
    return ((post.extras or {}).get("voz") or "").strip() or post.destino.voz


def caminho_da_convertida(post, gravada: bytes) -> str:
    """Onde fica a fala gravada ja na voz do video (por voz+gravacao)."""
    voz = voz_do_post(post)
    chave = hashlib.sha256(f"{voz}\n".encode() + gravada).hexdigest()[:32]
    return f"social/audios/convertida-{chave}.mp3"


def caminho_da_fala(post, fala: str, *, antes: str = "", depois: str = "") -> str:
    """Onde fica a voz gerada da fala (por voz+texto+vizinhas; ver `_audio`)."""
    voz = voz_do_post(post)
    contexto = "" if fontes.voz_do_worker(voz) else f"\n{antes}\n{depois}"
    chave = hashlib.sha256(f"{voz}\n{fala}{contexto}".encode()).hexdigest()[:32]
    return f"social/audios/{chave}.mp3"


def _fala_convertida(post, gravada: bytes) -> bytes:
    """A fala gravada (a entonacao) na voz do video, guardada por
    voz+gravacao: montar de novo nao converte de novo."""
    voz = voz_do_post(post)
    caminho = caminho_da_convertida(post, gravada)
    if default_storage.exists(caminho):
        with default_storage.open(caminho, "rb") as arquivo:
            return arquivo.read()
    audio = fontes.converter_fala(gravada, voz)
    default_storage.save(caminho, ContentFile(audio))
    return audio


def _audio(post, fala: str, *, antes: str = "", depois: str = "") -> bytes:
    """O audio da fala, guardado por voz+texto+vizinhas (so paga a fala que
    mudou). As vizinhas mudam a entonacao (ElevenLabs), por isso entram na chave;
    a voz do worker nao as usa, e mudar uma fala nao refaz as outras."""
    voz = voz_do_post(post)
    caminho = caminho_da_fala(post, fala, antes=antes, depois=depois)
    if default_storage.exists(caminho):
        with default_storage.open(caminho, "rb") as arquivo:
            return arquivo.read()
    audio = fontes.falar(fala, voz, antes=antes, depois=depois)
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


def _guardar_em(pasta: Path, nome: str, conteudo: bytes) -> Path:
    arquivo = pasta / nome
    arquivo.write_bytes(conteudo)
    return arquivo


def _copiar_do_armazenamento(pasta: Path, nome: str, caminho: str) -> Path:
    with default_storage.open(caminho, "rb") as arquivo:
        return _guardar_em(pasta, nome, arquivo.read())


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


def png_do_texto(trecho: str, cores: dict, *, tamanho: int = 52, cor: str = "") -> bytes:
    """O trecho em letra grande, numa faixa escura arredondada (a legenda, ou a
    frase da cena quando a conta usa frase na tela)."""
    import io

    from PIL import Image, ImageDraw

    fonte = laminas._fonte(tamanho, negrito=True)
    altura_da_faixa = max(ALTURA_DA_LEGENDA, int(tamanho * 3.3))
    imagem = Image.new("RGBA", (LARGURA, altura_da_faixa), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    linhas = laminas._quebrar(desenho, trecho, fonte, LARGURA - 200)[:2]
    altura_da_linha = int(fonte.size * 1.2)
    altura = altura_da_linha * len(linhas)
    largura = max(desenho.textlength(linha, font=fonte) for linha in linhas)
    topo = (altura_da_faixa - altura) // 2
    desenho.rounded_rectangle(
        ((LARGURA - largura) / 2 - 28, topo - 14, (LARGURA + largura) / 2 + 28, topo + altura + 14),
        radius=22,
        fill=(0, 0, 0, 175),
    )
    for n, linha in enumerate(linhas):
        x = (LARGURA - desenho.textlength(linha, font=fonte)) / 2
        desenho.text((x, topo + n * altura_da_linha), linha, font=fonte, fill=cor or cores["texto"])
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()
