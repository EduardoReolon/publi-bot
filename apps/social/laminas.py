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


# A fonte embutida no Pillow (Aileron) nao tem acento nem aspas curvas: "ação"
# saia com quadradinhos. Usa a primeira fonte do sistema que existir (o
# release.sh instala a DejaVu); sem nenhuma, a embutida, e `_desenhavel`
# troca o que ela nao tem.
FONTES = {
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ),
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ),
}
# Equivalentes simples, para o caractere que a fonte nao tem.
SUBSTITUTOS = {
    "\u2014": "-",  # travessao
    "\u2013": "-",  # meia-risca
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u2026": "...",
    "\u2022": "-",  # marcador
    "\u2192": "->",
    "\u2190": "<-",
    "\u2713": "v",
    "\u2714": "v",
    "\u00d7": "x",
}


def _fonte(tamanho: int, *, negrito: bool = False):
    import os

    from PIL import ImageFont

    for caminho in FONTES[negrito]:
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default(size=tamanho)


def _tem(fonte, caractere: str) -> bool:
    """A fonte desenha o caractere (e nao o quadradinho de "nao tenho")."""
    if caractere.isspace():
        return True
    chave = (fonte.getname(), fonte.size, caractere)
    if chave not in _TEM:
        desenho = fonte.getmask(caractere)
        vazio = fonte.getmask("\U0010fffd")
        _TEM[chave] = desenho.size != vazio.size or bytes(desenho) != bytes(vazio)
    return _TEM[chave]


_TEM: dict = {}


def _desenhavel(texto: str, fonte) -> str:
    """O texto so com o que a fonte desenha: o que falta vira um equivalente
    (aspas curvas -> retas, letra acentuada -> sem acento) ou sai (emoji)."""
    import unicodedata

    saida = []
    for caractere in texto or "":
        if _tem(fonte, caractere):
            saida.append(caractere)
            continue
        troca = SUBSTITUTOS.get(caractere)
        if troca is None:
            troca = "".join(
                c for c in unicodedata.normalize("NFKD", caractere) if not unicodedata.combining(c)
            )
        saida.append("".join(c for c in troca if _tem(fonte, c)))
    return "".join(saida)


def _quebrar(desenho, texto: str, fonte, largura: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in _desenhavel(texto, fonte).split():
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
    desenho.text(
        (MARGEM, ALTURA - MARGEM),
        _desenhavel(marca[:40], pequena),
        font=pequena,
        fill=cores["destaque"],
    )
    contador = f"{n}/{total}"
    largura = desenho.textlength(contador, font=pequena)
    desenho.text(
        (LARGURA - MARGEM - largura, ALTURA - MARGEM), contador, font=pequena, fill=cores["texto"]
    )


# Ajuste da foto da 1a lamina, guardado em `extras["ajuste_da_capa"]`. Um
# dicionario de proposito: um controle novo (brilho, girar, outra lamina) e so
# mais uma chave aqui, com o padrao e o limite dela; quem nao tem a chave fica
# no padrao, e o que ja foi gerado continua igual.
AJUSTE_PADRAO = {"zoom": 1.0, "x": 0.5, "y": 0.5, "escuro": 0.55}
LIMITES_DO_AJUSTE = {"zoom": (1.0, 4.0), "x": (0.0, 1.0), "y": (0.0, 1.0), "escuro": (0.0, 0.9)}


def ajuste_limpo(dados) -> dict:
    """O ajuste com so as chaves conhecidas, cada uma dentro do seu limite."""
    saida = dict(AJUSTE_PADRAO)
    for chave, (minimo, maximo) in LIMITES_DO_AJUSTE.items():
        try:
            valor = float((dados or {}).get(chave, saida[chave]))
        except (TypeError, ValueError):
            continue
        saida[chave] = min(max(valor, minimo), maximo)
    return saida


def _capa(caminho: str, cores: dict, ajuste: dict | None = None):
    """A foto de fundo da 1a lamina: cobre o quadro, com o zoom e a posicao do
    ajuste (x/y: 0 = encostada a esquerda/em cima, 1 = a direita/embaixo)."""
    from PIL import Image

    ajuste = ajuste_limpo(ajuste)
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
    escala = max(LARGURA / imagem.width, ALTURA / imagem.height) * ajuste["zoom"]
    imagem = imagem.resize((int(imagem.width * escala) + 1, int(imagem.height * escala) + 1))
    x = int((imagem.width - LARGURA) * ajuste["x"])
    y = int((imagem.height - ALTURA) * ajuste["y"])
    imagem = imagem.crop((x, y, x + LARGURA, y + ALTURA))
    escuro = Image.new("RGB", (LARGURA, ALTURA), (0, 0, 0))
    return Image.blend(imagem, escuro, ajuste["escuro"])


def desenhar(
    laminas: list[dict],
    *,
    capa: str = "",
    cores: dict | None = None,
    marca: str = "",
    chamada: str = "",
    ajuste: dict | None = None,
    apenas: int | None = None,
) -> list[bytes]:
    """As laminas em PNG. `laminas[0]` e o gancho, sobre a capa (com o
    `ajuste` de enquadramento). `apenas`: so a lamina n (a previa)."""
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
        if apenas is not None and n != apenas:
            continue
        imagem = (
            _capa(capa, cores, ajuste)
            if n == 1
            else Image.new("RGB", (LARGURA, ALTURA), cores["fundo"])
        )
        desenho = ImageDraw.Draw(imagem)
        titulo, texto = item.get("titulo", ""), item.get("texto", "")
        grande = _fonte(78 if n == 1 or n == total else 64, negrito=True)
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


# Quantas fotos cada rede leva num post (o LinkedIn e o Google, uma; o resto
# segue no Instagram, em carrossel).
FOTOS_POR_REDE = {"instagram": 10, "linkedin": 1, "gmn": 1}


def _endereco(post, n: int) -> str:
    return fontes.endereco_publico(reverse("social:midia_publica", args=[post.chave_publica, n]))


def _fotos_reais(post) -> list[dict]:
    """As fotos (ou o video) do material proprio, na proporcao da rede."""
    from apps.social import fotos
    from apps.social.models import Midia

    midias = list(post.entrada.midias.order_by("tirada_em", "criada_em"))
    if not midias:
        return []
    rede_ = post.destino.rede
    videos = [m for m in midias if m.tipo == Midia.Tipo.VIDEO]
    so_fotos = [m for m in midias if m.tipo == Midia.Tipo.FOTO]
    if videos and (rede_ == "instagram" or not so_fotos):
        # Video vai sozinho (reels no Instagram; nas outras, so para copiar).
        return [{"caminho": videos[0].arquivo.name, "url": _endereco(post, 1), "tipo": "video"}]
    escolhidas = so_fotos[: FOTOS_POR_REDE.get(rede_, 1)]
    saida = []
    for n, midia in enumerate(escolhidas, start=1):
        caminho = f"social/{post.pk}/{n}.jpg"
        if default_storage.exists(caminho):
            default_storage.delete(caminho)
        default_storage.save(
            caminho, ContentFile(fotos.versao_para(midia, rede_, carrossel=len(escolhidas) > 1))
        )
        saida.append(
            {
                "caminho": caminho,
                "url": _endereco(post, n),
                "tipo": "foto",
                "alt": (midia.descricao or midia.nota or "").strip()[:500],
            }
        )
    return saida


def _alt_da_lamina(laminas: list[dict], n: int, chamada: str) -> str:
    if n <= len(laminas):
        lamina = laminas[n - 1]
        return " ".join(x for x in [lamina.get("titulo", ""), lamina.get("texto", "")] if x)[:500]
    return chamada[:500]


def guardar_imagem_propria(post, arquivo) -> str:
    """A foto que a pessoa enviou no lugar da capa do artigo (fundo da 1a
    lamina, ou a imagem do post nas redes de capa). Desvira pela EXIF e grava
    em JPEG; devolve o caminho, guardado em `extras["imagem_propria"]`."""
    from PIL import Image, ImageOps

    imagem = ImageOps.exif_transpose(Image.open(arquivo)).convert("RGB")
    imagem.thumbnail((2400, 2400))
    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG", quality=88, optimize=True)
    caminho = f"social/{post.pk}/propria.jpg"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    default_storage.save(caminho, ContentFile(buffer.getvalue()))
    return caminho


def _foto_de_fundo(post, artigo) -> str:
    """A foto da 1a lamina: a que a pessoa enviou, ou a capa do artigo."""
    propria = (post.extras or {}).get("imagem_propria") or ""
    if propria and default_storage.exists(propria):
        return propria
    return artigo.capa


def previa_da_primeira(post, artigo, ajuste: dict, *, largura: int = 540) -> bytes:
    """A 1a lamina com o ajuste pedido, em tamanho de tela: o que a tela de
    enquadramento mostra enquanto a pessoa mexe, sem gravar nada."""
    from PIL import Image

    laminas = (post.extras or {}).get("laminas") or [{"titulo": artigo.titulo, "texto": ""}]
    png = desenhar(
        laminas,
        capa=_foto_de_fundo(post, artigo),
        cores=post.destino.cores,
        marca=post.destino.conta_nome or post.destino.nome,
        chamada=post.destino.chamada_final or CHAMADA,
        ajuste=ajuste,
        apenas=1,
    )[0]
    imagem = Image.open(io.BytesIO(png))
    imagem.thumbnail((largura, largura * ALTURA // LARGURA))
    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def preparar_imagens(post, artigo: fontes.ArtigoParaRedes) -> None:
    """Grava as imagens do post e o endereco publico de cada uma."""
    r = rede(post.destino.rede)
    imagens = []
    capa = _foto_de_fundo(post, artigo)
    propria = capa if capa != artigo.capa else ""
    if post.entrada_id and (reais := _fotos_reais(post)):
        imagens = reais
    elif r.formato.imagem == "capa" and propria:
        imagens = [{"caminho": propria, "url": _endereco(post, 1), "alt": artigo.titulo[:500]}]
    elif r.formato.imagem == "capa" and artigo.capa:
        imagens = [{"caminho": artigo.capa, "url": artigo.capa_url, "alt": artigo.titulo[:500]}]
    elif r.formato.imagem == "laminas":
        laminas = (post.extras or {}).get("laminas") or []
        if laminas:
            chamada = post.destino.chamada_final or CHAMADA
            pngs = desenhar(
                laminas,
                capa=capa,
                ajuste=(post.extras or {}).get("ajuste_da_capa"),
                cores=post.destino.cores,
                marca=post.destino.conta_nome or post.destino.nome,
                chamada=chamada,
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
                        # Texto alternativo (leitor de tela): o que a lamina diz.
                        "alt": _alt_da_lamina(laminas, n, chamada),
                    }
                )
    post.imagens = imagens
    post.save(update_fields=["imagens", "atualizado_em"])
