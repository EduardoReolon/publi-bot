"""As imagens do post: a capa do artigo, ou o carrossel montado aqui.

O carrossel e montado por algoritmo (Pillow), nao por IA de imagem: modelo de
imagem escreve texto mal (letras trocadas). Cada lamina e um modelo fixo com
as cores da conta e uma frase curta que o modelo de TEXTO escreveu:

1. a capa do artigo, escurecida, com o gancho por cima;
2. uma ideia por lamina (titulo + frase);
3. a chamada final ("Leia o artigo completo — link na bio").

1080 x 1350 (4:5), o formato que ocupa mais tela no feed do Instagram.
"""

from __future__ import annotations

import io
import logging

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse

from apps.social import fontes
from apps.social.redes import rede

logger = logging.getLogger("publibot.social")

LARGURA, ALTURA = 1080, 1350
MARGEM = 90
CORES = {"fundo": "#0f172a", "texto": "#ffffff", "destaque": "#38bdf8"}
CHAMADA = "Leia o artigo completo — link na bio"


def _fonte(tamanho: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=tamanho)


def _quebrar(desenho, texto: str, fonte, largura: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in (texto or "").split():
        teste = f"{atual} {palavra}".strip()
        if desenho.textlength(teste, font=fonte) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _bloco(desenho, texto, fonte, cor, y: int, *, espaco: float = 1.25) -> int:
    for linha in _quebrar(desenho, texto, fonte, LARGURA - 2 * MARGEM):
        desenho.text((MARGEM, y), linha, font=fonte, fill=cor)
        y += int(fonte.size * espaco)
    return y


def _altura(desenho, texto, fonte, espaco: float = 1.25) -> int:
    return len(_quebrar(desenho, texto, fonte, LARGURA - 2 * MARGEM)) * int(fonte.size * espaco)


def _rodape(desenho, cores: dict, marca: str, n: int, total: int) -> None:
    pequena = _fonte(30)
    desenho.text((MARGEM, ALTURA - MARGEM), marca[:40], font=pequena, fill=cores["destaque"])
    contador = f"{n}/{total}"
    largura = desenho.textlength(contador, font=pequena)
    desenho.text(
        (LARGURA - MARGEM - largura, ALTURA - MARGEM), contador, font=pequena, fill=cores["texto"]
    )


def _capa(caminho: str, cores: dict):
    from PIL import Image

    fundo = Image.new("RGB", (LARGURA, ALTURA), cores["fundo"])
    if not caminho:
        return fundo
    try:
        with default_storage.open(caminho, "rb") as arquivo:
            imagem = Image.open(arquivo).convert("RGB")
            imagem.load()
    except Exception as exc:
        logger.info("Capa %s indisponivel para a lamina: %s", caminho, exc)
        return fundo
    escala = max(LARGURA / imagem.width, ALTURA / imagem.height)
    imagem = imagem.resize((int(imagem.width * escala) + 1, int(imagem.height * escala) + 1))
    x = (imagem.width - LARGURA) // 2
    y = (imagem.height - ALTURA) // 2
    imagem = imagem.crop((x, y, x + LARGURA, y + ALTURA))
    escuro = Image.new("RGB", (LARGURA, ALTURA), (0, 0, 0))
    return Image.blend(imagem, escuro, 0.55)


def desenhar(
    laminas: list[dict],
    *,
    capa: str = "",
    cores: dict | None = None,
    marca: str = "",
    chamada: str = "",
) -> list[bytes]:
    """As laminas em PNG. `laminas[0]` e o gancho, sobre a capa."""
    from PIL import Image, ImageDraw

    cores = {**CORES, **{k: v for k, v in (cores or {}).items() if v}}
    itens = list(laminas)
    if chamada:
        if itens and "bio" in f"{itens[-1].get('titulo', '')} {itens[-1].get('texto', '')}".lower():
            itens[-1] = {"titulo": chamada, "texto": ""}
        else:
            itens.append({"titulo": chamada, "texto": ""})
    total = len(itens)
    saida = []
    for n, item in enumerate(itens, start=1):
        imagem = (
            _capa(capa, cores) if n == 1 else Image.new("RGB", (LARGURA, ALTURA), cores["fundo"])
        )
        desenho = ImageDraw.Draw(imagem)
        titulo, texto = item.get("titulo", ""), item.get("texto", "")
        grande = _fonte(78 if n == 1 or n == total else 64)
        normal = _fonte(46)
        altura = _altura(desenho, titulo, grande) + (
            40 + _altura(desenho, texto, normal) if texto else 0
        )
        # A primeira, embaixo (a capa aparece em cima); as outras, no meio.
        y = ALTURA - MARGEM * 2 - altura if n == 1 else (ALTURA - altura) // 2
        cor_do_titulo = cores["texto"] if n in (1, total) else cores["destaque"]
        y = _bloco(desenho, titulo, grande, cor_do_titulo, y)
        if texto:
            _bloco(desenho, texto, normal, cores["texto"], y + 40)
        _rodape(desenho, cores, marca, n, total)
        buffer = io.BytesIO()
        imagem.save(buffer, format="PNG", optimize=True)
        saida.append(buffer.getvalue())
    return saida


def caminho_da_imagem(post, n: int) -> str:
    return f"social/{post.pk}/{n}.png"


def preparar_imagens(post, artigo: fontes.ArtigoParaRedes) -> None:
    """Grava as imagens do post e o endereco publico de cada uma."""
    r = rede(post.destino.rede)
    imagens = []
    if r.formato.imagem == "capa" and artigo.capa:
        imagens = [{"caminho": artigo.capa, "url": artigo.capa_url}]
    elif r.formato.imagem == "laminas":
        laminas = (post.extras or {}).get("laminas") or []
        if laminas:
            pngs = desenhar(
                laminas,
                capa=artigo.capa,
                cores=post.destino.cores,
                marca=post.destino.conta_nome or post.destino.nome,
                chamada=post.destino.chamada_final or CHAMADA,
            )
            for n, png in enumerate(pngs, start=1):
                caminho = caminho_da_imagem(post, n)
                if default_storage.exists(caminho):
                    default_storage.delete(caminho)
                default_storage.save(caminho, ContentFile(png))
                imagens.append(
                    {
                        "caminho": caminho,
                        "url": fontes.endereco_publico(
                            reverse("social:imagem", args=[post.chave_publica, n])
                        ),
                    }
                )
    post.imagens = imagens
    post.save(update_fields=["imagens", "atualizado_em"])
