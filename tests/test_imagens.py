"""Padronizacao de imagem na entrada.

Toda imagem que trafega no contrato e WebP. A conversao acontece ao RECEBER o
arquivo, e nao ao envia-lo: converter a cada publicacao gastaria CPU em toda
entrega, deixaria dois formatos no disco e abriria a chance de um caminho
esquecer a conversao.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from apps.content.imagens import LADO_MAXIMO, ImagemInvalida, converter_para_webp, dimensoes


def _imagem(formato: str, tamanho=(50, 50), modo: str = "RGB") -> io.BytesIO:
    memoria = io.BytesIO()
    Image.new(modo, tamanho, (200, 30, 30) if modo == "RGB" else (200, 30, 30, 128)).save(
        memoria, format=formato
    )
    memoria.seek(0)
    return memoria


@pytest.mark.parametrize("formato", ["JPEG", "PNG", "WEBP", "GIF", "BMP"])
def test_formatos_comuns_viram_webp(formato):
    convertida = converter_para_webp(_imagem(formato), nome="foto")

    assert convertida.name == "foto.webp"
    assert dimensoes(io.BytesIO(convertida.read())) == (50, 50)


def test_imagem_grande_e_reduzida():
    """Acima disto a foto e maior do que qualquer lugar onde ela aparece."""
    convertida = converter_para_webp(_imagem("JPEG", tamanho=(4000, 3000)))

    largura, altura = dimensoes(io.BytesIO(convertida.read()))
    assert max(largura, altura) == LADO_MAXIMO
    # A proporcao original e mantida: cortar mudaria o enquadramento da foto.
    assert round(largura / altura, 2) == round(4000 / 3000, 2)


def test_transparencia_sobrevive():
    """Achatar aqui deixaria uma foto de perfil recortada com fundo preto."""
    convertida = converter_para_webp(_imagem("PNG", modo="RGBA"))

    with Image.open(io.BytesIO(convertida.read())) as imagem:
        assert imagem.mode in ("RGBA", "RGBa")


def test_arquivo_que_nao_e_imagem_e_recusado():
    """Um arquivo que nao abre aqui nao vai abrir no site do cliente, e
    descobrir isso na publicacao custa uma tentativa."""
    with pytest.raises(ImagemInvalida):
        converter_para_webp(io.BytesIO(b"nem de longe uma imagem"))


def test_webp_e_menor_que_o_png_equivalente():
    original = _imagem("PNG", tamanho=(800, 600))
    tamanho_original = len(original.getvalue())

    convertida = converter_para_webp(original)

    assert len(convertida.read()) < tamanho_original
