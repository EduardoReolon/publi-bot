"""As fotos e videos da propria pessoa: receber, avaliar, agrupar, recortar.

Tudo por algoritmo (Pillow e numpy), sem modelo:

* **normalizar** — gira pela orientacao da camera, reduz para no maximo 2048
  px e grava SEM os metadados (a foto do celular traz a localizacao de onde
  foi tirada; isso nunca sai daqui);
* **qualidade** — nitidez (variancia do Laplaciano: foto tremida tem pouca
  borda) e brilho (media da luminancia). Abaixo do minimo, a foto e
  descartada com o motivo — e a pessoa pode usar mesmo assim;
* **quase iguais** — assinatura dHash (64 bits); duas fotos com ate
  `QUASE_IGUAL` bits diferentes sao a mesma cena: fica a mais nitida;
* **atendimento** — fotos tiradas com ate `INTERVALO_DO_GRUPO` de diferenca
  sao do mesmo atendimento e viram um carrossel;
* **recorte** — cada rede aceita uma faixa de proporcao; fora dela, corta pelo
  centro. Carrossel do Instagram vai todo em 4:5 (a rede corta tudo pela
  proporcao da primeira).
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from datetime import datetime, timedelta

import numpy as np
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.social.models import Midia

logger = logging.getLogger("publibot.social")

LADO_MAXIMO = 2048
QUALIDADE_JPEG = 88
QUASE_IGUAL = 6
INTERVALO_DO_GRUPO = timedelta(minutes=20)
FOTOS_POR_CARROSSEL = 10
VIDEO_MAXIMO = 200 * 1024 * 1024
EXTENSOES_DE_VIDEO = {".mp4", ".mov", ".m4v"}
# (menor, maior) largura/altura que cada rede mostra sem cortar.
PROPORCOES = {"instagram": (0.8, 1.91), "linkedin": (0.5, 2.0), "gmn": (0.75, 1.78)}
CARROSSEL = 0.8  # 4:5


class MidiaInvalida(ValueError):
    pass


# -- Medidas ---------------------------------------------------------------------------
def nitidez(cinza: np.ndarray) -> float:
    """Variancia do Laplaciano (4 vizinhos) na imagem em tons de cinza."""
    c = cinza.astype(float)
    lap = -4 * c[1:-1, 1:-1] + c[:-2, 1:-1] + c[2:, 1:-1] + c[1:-1, :-2] + c[1:-1, 2:]
    return float(lap.var())


def assinatura(imagem) -> str:
    """dHash: 9x8 em cinza, cada bit diz se o pixel e mais claro que o vizinho."""
    from PIL import Image

    pequena = np.asarray(imagem.convert("L").resize((9, 8), Image.Resampling.LANCZOS), float)
    bits = (pequena[:, 1:] > pequena[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def distancia(a: str, b: str) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _quando_foi_tirada(imagem) -> datetime | None:
    try:
        exif = imagem.getexif()
        texto = exif.get_ifd(0x8769).get(36867) or exif.get(306)
    except Exception:
        return None
    if not texto:
        return None
    try:
        return timezone.make_aware(datetime.strptime(str(texto).strip()[:19], "%Y:%m:%d %H:%M:%S"))
    except ValueError:
        return None


# -- Receber ----------------------------------------------------------------------------
def receber(arquivo, *, banco: bool, nota: str = "", autorizada: bool = False) -> Midia:
    """Grava a foto (normalizada) ou o video, com as medidas. `arquivo`: o que
    veio do formulario (tem .name, .read() e .size)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    nome = getattr(arquivo, "name", "") or "arquivo"
    extensao = os.path.splitext(nome)[1].lower()
    tipo = getattr(arquivo, "content_type", "") or ""
    midia = Midia(banco=banco, nota=nota.strip()[:2000], autorizada=autorizada)

    if tipo.startswith("video/") or extensao in EXTENSOES_DE_VIDEO:
        if getattr(arquivo, "size", 0) > VIDEO_MAXIMO:
            raise MidiaInvalida(f"{nome}: video acima de 200 MB.")
        midia.tipo = Midia.Tipo.VIDEO
        # O arquivo vai em pedacos para o disco (video de celular e grande).
        midia.arquivo.save(f"v{extensao or '.mp4'}", arquivo, save=False)
        midia.save()
        return midia

    try:
        imagem = Image.open(io.BytesIO(arquivo.read()))
        imagem.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise MidiaInvalida(
            f"{nome}: formato que o PubliBot nao le (use JPG ou PNG; no iPhone, a foto enviada "
            "pelo navegador ja vai como JPG)."
        ) from exc
    midia.tirada_em = _quando_foi_tirada(imagem)
    imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    midia.largura, midia.altura = imagem.size
    cinza = np.asarray(
        imagem.convert("L").resize((512, max(int(512 * imagem.height / imagem.width), 1)))
    )
    midia.nitidez = round(nitidez(cinza), 1)
    midia.brilho = round(float(cinza.mean()), 1)
    midia.assinatura = assinatura(imagem)
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)  # sem exif
    midia.arquivo.save("f.jpg", ContentFile(saida.getvalue()), save=False)
    midia.save()
    return midia


def avaliar(midia: Midia, *, nitidez_minima: float, brilho: tuple[float, float]) -> Midia:
    """Descarta foto tremida, escura ou estourada (com o motivo)."""
    if midia.tipo != Midia.Tipo.FOTO or midia.situacao != Midia.Situacao.NOVA:
        return midia
    motivo = ""
    if midia.nitidez is not None and midia.nitidez < nitidez_minima:
        motivo = f"tremida ou fora de foco (nitidez {midia.nitidez:.0f})"
    elif midia.brilho is not None and midia.brilho < brilho[0]:
        motivo = f"escura demais (brilho {midia.brilho:.0f})"
    elif midia.brilho is not None and midia.brilho > brilho[1]:
        motivo = f"clara demais (brilho {midia.brilho:.0f})"
    if motivo:
        midia.situacao = Midia.Situacao.DESCARTADA
        midia.motivo = motivo
        midia.save(update_fields=["situacao", "motivo"])
    return midia


def tirar_quase_iguais(midias: list[Midia]) -> int:
    """Das fotos quase iguais entre si (e com as do banco), fica a mais nitida."""
    banco = list(
        Midia.objects.filter(banco=True, tipo=Midia.Tipo.FOTO, situacao=Midia.Situacao.NOVA)
        .exclude(assinatura="")
        .order_by("-nitidez")
    )
    descartadas = 0
    mantidas: list[Midia] = []
    for midia in sorted(banco, key=lambda m: -(m.nitidez or 0)):
        igual = next(
            (m for m in mantidas if distancia(m.assinatura, midia.assinatura) <= QUASE_IGUAL), None
        )
        if igual is None:
            mantidas.append(midia)
            continue
        midia.situacao = Midia.Situacao.DESCARTADA
        midia.motivo = "quase igual a outra foto (ficou a mais nitida)"
        midia.save(update_fields=["situacao", "motivo"])
        descartadas += 1
    return descartadas


def agrupar() -> int:
    """Junta as fotos do banco ainda sem grupo por atendimento (horario da foto,
    ou do envio quando a foto nao traz). Devolve quantos grupos novos."""
    soltas = list(
        Midia.objects.filter(banco=True, grupo__isnull=True, situacao=Midia.Situacao.NOVA)
    )
    soltas.sort(key=lambda m: m.tirada_em or m.criada_em)
    grupos = 0
    atual, ultimo, tamanho = None, None, 0
    for midia in soltas:
        quando = midia.tirada_em or midia.criada_em
        if (
            atual is None
            or midia.tipo == Midia.Tipo.VIDEO
            or quando - ultimo > INTERVALO_DO_GRUPO
            or tamanho >= FOTOS_POR_CARROSSEL
        ):
            atual, tamanho = uuid.uuid4(), 0
            grupos += 1
        midia.grupo = atual
        midia.save(update_fields=["grupo"])
        ultimo, tamanho = quando, tamanho + 1
        if midia.tipo == Midia.Tipo.VIDEO:
            atual = None  # video vai sozinho (reels)
    return grupos


# -- Recorte por rede --------------------------------------------------------------------
def versao_para(midia: Midia, rede: str, *, carrossel: bool = False) -> bytes:
    """A foto em JPEG na proporcao que a rede mostra sem cortar."""
    from PIL import Image

    with midia.arquivo.open("rb") as arquivo:
        imagem = Image.open(arquivo)
        imagem.load()
    imagem = imagem.convert("RGB")
    menor, maior = PROPORCOES.get(rede, (0.5, 2.0))
    alvo = CARROSSEL if carrossel and rede == "instagram" else None
    proporcao = imagem.width / imagem.height
    if alvo is None:
        alvo = min(max(proporcao, menor), maior)
    if abs(proporcao - alvo) > 0.01:
        if proporcao > alvo:  # larga demais: corta dos lados
            largura = int(imagem.height * alvo)
            x = (imagem.width - largura) // 2
            imagem = imagem.crop((x, 0, x + largura, imagem.height))
        else:  # alta demais: corta em cima e embaixo
            altura = int(imagem.width / alvo)
            y = (imagem.height - altura) // 2
            imagem = imagem.crop((0, y, imagem.width, y + altura))
    imagem.thumbnail((1440, 1440))
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
    return saida.getvalue()
