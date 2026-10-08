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


def caminho_da_fonte(*, negrito: bool = False) -> str:
    """O arquivo da fonte das laminas ("" se o sistema nao tem nenhuma)."""
    import os

    return next((c for c in FONTES[negrito] if os.path.exists(c)), "")


def _fonte(tamanho: int, *, negrito: bool = False):
    from PIL import ImageFont

    if caminho := caminho_da_fonte(negrito=negrito):
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


# Onde a linha comeca dentro do bloco, pelo alinhamento.
POSICAO_DA_LINHA = {"esquerda": 0.0, "centro": 0.5, "direita": 1.0}


def _bloco(
    desenho,
    texto,
    fonte,
    cor,
    y: int,
    *,
    espaco: float = 1.25,
    x: int = MARGEM,
    largura: int = LARGURA - 2 * MARGEM,
    alinhamento: str = "esquerda",
) -> int:
    peso = POSICAO_DA_LINHA.get(alinhamento, 0.0)
    for linha in _quebrar(desenho, texto, fonte, largura):
        sobra = largura - desenho.textlength(linha, font=fonte)
        desenho.text((x + int(sobra * peso), y), linha, font=fonte, fill=cor)
        y += int(fonte.size * espaco)
    return y


def _altura(
    desenho, texto, fonte, espaco: float = 1.25, largura: int = LARGURA - 2 * MARGEM
) -> int:
    return len(_quebrar(desenho, texto, fonte, largura)) * int(fonte.size * espaco)


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


# Ajuste da 1a lamina (foto e texto), guardado em `extras["ajuste_da_capa"]`.
# Um dicionario de proposito: um controle novo e so mais uma chave aqui, com o
# padrao e o limite dela; quem nao tem a chave fica no padrao, e o que ja foi
# gerado continua igual. O editor (templates/social/editar_lamina.html) desenha
# a mesma conta no navegador; o servidor e quem grava.
#  - zoom, x, y: a foto (x/y: 0 = encostada a esquerda/em cima, 1 = ao contrario);
#  - escuro, degrade: o escurecimento (no degrade, mais forte embaixo);
#  - tamanho_titulo, tamanho_texto: em px, no quadro de 1080;
#  - texto_x, texto_y, largura: o bloco de texto, em fracao do quadro
#    (texto_y < 0: automatico, encostado embaixo);
#  - alinhamento, cor_titulo ("" = a cor da conta).
AJUSTE_PADRAO = {
    "zoom": 1.0,
    "x": 0.5,
    "y": 0.5,
    "escuro": 0.55,
    "degrade": 0.0,
    "tamanho_titulo": 78.0,
    "tamanho_texto": 46.0,
    "texto_x": MARGEM / LARGURA,
    "texto_y": -1.0,
    "largura": (LARGURA - 2 * MARGEM) / LARGURA,
    "alinhamento": "esquerda",
    "cor_titulo": "",
}
LIMITES_DO_AJUSTE = {
    "zoom": (1.0, 4.0),
    "x": (0.0, 1.0),
    "y": (0.0, 1.0),
    "escuro": (0.0, 0.9),
    "degrade": (0.0, 1.0),
    "tamanho_titulo": (36.0, 160.0),
    "tamanho_texto": (24.0, 90.0),
    "texto_x": (0.0, 0.6),
    "texto_y": (-1.0, 0.95),
    "largura": (0.4, 1.0),
}
ESCOLHAS_DO_AJUSTE = {"alinhamento": tuple(POSICAO_DA_LINHA)}


def ajuste_limpo(dados) -> dict:
    """O ajuste com so as chaves conhecidas, cada uma dentro do seu limite."""
    import re

    dados = dados or {}
    saida = dict(AJUSTE_PADRAO)
    for chave, (minimo, maximo) in LIMITES_DO_AJUSTE.items():
        try:
            valor = float(dados.get(chave, saida[chave]))
        except (TypeError, ValueError):
            continue
        saida[chave] = min(max(valor, minimo), maximo)
    for chave, opcoes in ESCOLHAS_DO_AJUSTE.items():
        if dados.get(chave) in opcoes:
            saida[chave] = dados[chave]
    cor = str(dados.get("cor_titulo") or "")
    saida["cor_titulo"] = cor if re.fullmatch(r"#[0-9a-fA-F]{6}", cor) else ""
    # O bloco nao sai do quadro pela direita.
    saida["texto_x"] = min(saida["texto_x"], max(0.0, 1.0 - saida["largura"]))
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
    if not ajuste["degrade"]:
        return Image.blend(imagem, escuro, ajuste["escuro"])
    # Degrade: nada em cima, ate 1,5x o escuro embaixo (onde fica o texto).
    fundo = min(0.95, ajuste["escuro"] * 1.5)
    mascara = Image.linear_gradient("L").resize((LARGURA, ALTURA))
    mascara = mascara.point(lambda v: int(v * fundo))
    return Image.composite(escuro, imagem, mascara)


# Entre o titulo e o texto da lamina.
ESPACO_DO_TEXTO = 40


def com_a_chamada(laminas: list[dict], chamada: str) -> list[dict]:
    """As laminas com a chamada final (no lugar da ultima, se ela ja fala da bio)."""
    itens = list(laminas)
    if chamada:
        if itens and "bio" in f"{itens[-1].get('titulo', '')} {itens[-1].get('texto', '')}".lower():
            itens[-1] = {"titulo": chamada, "texto": ""}
        else:
            itens.append({"titulo": chamada, "texto": ""})
    return itens


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
    itens = com_a_chamada(laminas, chamada)
    total = len(itens)
    primeira = ajuste_limpo(ajuste)
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
        if n == 1:
            # A primeira segue o ajuste do editor (tamanho, posicao, alinhamento).
            grande = _fonte(int(primeira["tamanho_titulo"]), negrito=True)
            normal = _fonte(int(primeira["tamanho_texto"]))
            largura = int(LARGURA * primeira["largura"])
            x = int(LARGURA * primeira["texto_x"])
            alinhamento = primeira["alinhamento"]
        else:
            grande = _fonte(78 if n == total else 64, negrito=True)
            normal = _fonte(46)
            largura, x, alinhamento = LARGURA - 2 * MARGEM, MARGEM, "esquerda"
        altura = _altura(desenho, titulo, grande, largura=largura) + (
            ESPACO_DO_TEXTO + _altura(desenho, texto, normal, largura=largura) if texto else 0
        )
        if n == 1:
            # Embaixo (a capa aparece em cima), ou onde a pessoa pos o texto.
            automatico = ALTURA - MARGEM * 2 - altura
            y = automatico if primeira["texto_y"] < 0 else int(ALTURA * primeira["texto_y"])
        else:
            y = (ALTURA - altura) // 2
        cor_do_titulo = cores["texto"] if n in (1, total) else cores["destaque"]
        if n == 1 and primeira["cor_titulo"]:
            cor_do_titulo = primeira["cor_titulo"]
        bloco = {"x": x, "largura": largura, "alinhamento": alinhamento}
        y = _bloco(desenho, titulo, grande, cor_do_titulo, y, **bloco)
        if texto:
            _bloco(desenho, texto, normal, cores["texto"], y + ESPACO_DO_TEXTO, **bloco)
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


def foto_de_fundo(post, artigo) -> str:
    """A foto da 1a lamina: a que a pessoa enviou, ou a capa do artigo."""
    propria = (post.extras or {}).get("imagem_propria") or ""
    if propria and default_storage.exists(propria):
        return propria
    return artigo.capa


def dados_do_editor(post, artigo) -> dict:
    """O que o editor da 1a lamina precisa para desenhar no navegador a mesma
    conta do servidor (`desenhar`): quadro, cores, textos e o ajuste salvo."""
    extras = post.extras or {}
    laminas = extras.get("laminas") or [{"titulo": artigo.titulo, "texto": ""}]
    itens = com_a_chamada(laminas, post.destino.chamada_final or CHAMADA)
    return {
        "largura": LARGURA,
        "altura": ALTURA,
        "margem": MARGEM,
        "espaco_do_texto": ESPACO_DO_TEXTO,
        "cores": {**CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}},
        "marca": (post.destino.conta_nome or post.destino.nome)[:40],
        "total": len(itens),
        "titulo": laminas[0].get("titulo", ""),
        "texto": laminas[0].get("texto", ""),
        "ajuste": ajuste_limpo(extras.get("ajuste_da_capa")),
        "padrao": dict(AJUSTE_PADRAO),
        "limites": LIMITES_DO_AJUSTE,
        "tem_foto": bool(foto_de_fundo(post, artigo)),
    }


def previa_da_primeira(
    post, artigo, ajuste: dict, *, largura: int = 540, titulo=None, texto=None
) -> bytes:
    """A 1a lamina com o ajuste pedido, em tamanho de tela, sem gravar nada.
    `titulo`/`texto`: os do editor, ainda nao salvos."""
    from PIL import Image

    laminas = [
        dict(lamina)
        for lamina in (post.extras or {}).get("laminas") or [{"titulo": artigo.titulo, "texto": ""}]
    ]
    if titulo is not None:
        laminas[0]["titulo"] = titulo
    if texto is not None:
        laminas[0]["texto"] = texto
    png = desenhar(
        laminas,
        capa=foto_de_fundo(post, artigo),
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
    capa = foto_de_fundo(post, artigo)
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
