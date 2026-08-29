"""Padronizacao de imagem: tudo vira WebP na entrada.

A conversao acontece **ao receber o arquivo**, e nao ao envia-lo. Guardar o
original e converter a cada publicacao gastaria CPU em toda entrega, deixaria
dois formatos no disco e abriria a chance de um caminho esquecer a conversao —
que e como um PNG de 4 MB acaba trafegando para o site de um cliente.

WebP porque e o unico formato que atende os tres lados de uma vez: compressao
melhor que JPEG na mesma qualidade percebida, transparencia como o PNG, e
suporte universal em navegador desde 2020. Escolher por tipo de imagem exigiria
decidir caso a caso, e a decisao erraria.
"""

from __future__ import annotations

import io

from django.core.files.base import ContentFile

# Acima disto a foto e maior do que qualquer lugar onde ela aparece. Reduzir
# antes de guardar evita carregar megabytes para exibir 200 pixels.
LADO_MAXIMO = 1600

# 82 e o joelho da curva: acima disso o arquivo cresce rapido e a diferenca
# visual some. Medido no formato, nao chutado por gosto.
QUALIDADE = 82

FORMATOS_ACEITOS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}


class ImagemInvalida(ValueError):
    """O arquivo enviado nao e uma imagem que da para converter."""


def converter_para_webp(arquivo, *, nome: str = "imagem") -> ContentFile:
    """Le qualquer imagem comum e devolve um WebP pronto para gravar.

    Levanta `ImagemInvalida` em vez de deixar passar: um arquivo que nao abre
    aqui nao vai abrir no site do cliente, e descobrir isso na publicacao custa
    uma tentativa e um erro que aponta para o lugar errado.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        arquivo.seek(0)
        imagem = Image.open(arquivo)
        imagem.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImagemInvalida(
            "nao foi possivel ler este arquivo como imagem. Envie JPEG, PNG, WebP ou GIF."
        ) from exc

    if imagem.format and imagem.format.upper() not in FORMATOS_ACEITOS:
        raise ImagemInvalida(f"formato {imagem.format} nao suportado.")

    # RGBA sobrevive: o WebP guarda transparencia, e achatar aqui deixaria uma
    # foto de perfil recortada com fundo preto.
    if imagem.mode not in ("RGB", "RGBA"):
        imagem = imagem.convert("RGBA" if "A" in imagem.getbands() else "RGB")

    imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO))

    saida = io.BytesIO()
    imagem.save(saida, format="WEBP", quality=QUALIDADE, method=6)
    return ContentFile(saida.getvalue(), name=f"{nome}.webp")


def dimensoes(arquivo) -> tuple[int, int]:
    """Largura e altura, sem carregar a imagem inteira na memoria."""
    from PIL import Image

    arquivo.seek(0)
    with Image.open(arquivo) as imagem:
        return imagem.size
