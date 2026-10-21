"""O roteiro do video (Reels) e o que se pede as IAs sobre ele.

O video e feito AQUI, cena a cena (`cenas.py`): cada cena e uma animacao curta
do worker (2 a 4 s, a partir de uma imagem inicial), com a fala em voz off e a
legenda por codigo. Por isso o roteiro vem picado em cenas, e as regras que vao
para quem escreve (a nossa IA ou a outra) sao as do que o worker faz bem:

- de 2 a 4 s por cena, UM gesto lento, camera parada ou aproximando devagar;
- a fala e voz em off, curta (cabe no tempo da cena); ninguem fala em cena;
- a imagem inicial e o primeiro quadro, sem texto, com os personagens e os
  lugares reais da conta chamados pelo nome;
- a primeira cena e o gancho (prende nos 2 primeiros segundos) e a ultima, a
  chamada (o padrao da conta, na Identidade visual).

Cada cena no post: {"segundos", "fala", "imagem", "movimento", "texto"} (texto:
a frase na tela, so na conta que usa frase). O que a geracao acrescenta (a
imagem gerada, o clipe, a seed) fica na mesma cena, em `cenas.py`.

Tambem: o pedido para uma IA de video de fora (Sora, Veo, Kling...), com o
roteiro ou pedindo que ela o monte, e o MP4 feito fora (`enviar`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.social import identidade, laminas

logger = logging.getLogger("publibot.social")

# Duracao do video por rede, em segundos (minimo, maximo).
DURACAO = {"instagram": (15, 30), "linkedin": (20, 60), "gmn": (10, 30)}
TIPOS = {".mp4", ".mov", ".webm", ".m4v"}
TAMANHO_MAXIMO = 200 * 1024 * 1024
CENAS_MAXIMAS = 12
SEGUNDOS_DA_CENA = (2, 4)
PALAVRAS_POR_CENA = 10

TEXTO_NO_VIDEO = {
    "": "SEM nenhum texto escrito na imagem (nada de letras, legendas ou logotipo)",
    "legendas": (
        "com LEGENDAS da fala embaixo, curtas (ate 5 palavras por vez), grandes e legiveis, "
        "na cor de destaque da marca"
    ),
    "frases": (
        "com FRASES CURTAS na tela nos momentos marcados no roteiro (exatamente o texto "
        "indicado, grande e legivel), nada alem delas"
    ),
}


def _duracao(destino) -> tuple[int, int]:
    return DURACAO.get(destino.rede, (15, 30))


def _chamada(destino) -> str:
    return (destino.chamada_do_video or destino.chamada_final or "").strip()


def lugares(destino) -> list:
    """Os lugares reais (com foto) que a conta tem."""
    return [e for e in identidade.elementos(destino) if e.tipo == e.Tipo.LUGAR and bool(e.imagem)]


def regras_das_cenas(destino) -> str:
    """As regras do roteiro em cenas, iguais para a nossa IA e para a outra."""
    minimo, maximo = _duracao(destino)
    menor, maior = SEGUNDOS_DA_CENA
    chamada = _chamada(destino)
    texto = (
        "- frase na tela: a conta usa; no maximo 6 palavras, so nas cenas em que ajuda.\n"
        if destino.texto_no_video == "frases"
        else "- frase na tela: deixe vazio (a conta nao usa).\n"
    )
    nomes = ", ".join(e.nome for e in lugares(destino))
    return (
        "Regras das CENAS (cada cena vira uma animacao curta feita a partir de uma imagem "
        "inicial, e as cenas sao coladas na ordem):\n"
        f"- de 4 a {CENAS_MAXIMAS} cenas, {minimo} a {maximo} s no total; a 1a e o GANCHO "
        "(prende nos 2 primeiros segundos, sem introducao) e a ultima e a CHAMADA"
        f"{f' ({chamada})' if chamada else ''};\n"
        f"- cada cena de {menor} a {maior} s, com UM gesto lento (alguem leva a mao a "
        "testa, folhas sobem da mesa) e a camera parada ou aproximando devagar, sem cortes;\n"
        f"- a fala e voz em off (narrador), ate {PALAVRAS_POR_CENA} palavras por cena; "
        "ninguem fala em cena (boca fechada);\n"
        "- a imagem inicial e o PRIMEIRO quadro: quem esta, onde, a pose e a expressao no "
        "comeco do gesto; personagens e lugares fixos PELO NOME; nenhum texto, letra ou "
        "numero na imagem;\n"
        + (
            f"- lugares reais com foto ({nomes}): cite pelo nome e diga se aparece 'na "
            "janela' ou 'de fundo';\n"
            if nomes
            else ""
        )
        + texto
    )


def instrucao(post) -> str:
    """Para a nossa IA que escreve o post: o formato e, quando e video, as cenas.
    Nas redes sem laminas, a cena da foto do post."""
    from apps.social import redacao

    destino = post.destino
    if not redacao.com_laminas(post):
        return (
            "FOTO: devolva 'foto': a cena da imagem do post, concreta (quem, onde, o que "
            "faz), sem texto na imagem, com personagens e lugares fixos pelo nome.\n"
        )
    if destino.video == destino.Video.NUNCA:
        return ""
    cenas = (
        "devolva 'video' com 'cenas': cada uma com 'segundos', 'fala', 'imagem' (a imagem "
        "inicial), 'movimento' e 'texto' (a frase na tela, ou vazio). "
        + regras_das_cenas(destino).replace("\n", " ")
    )
    orientacao = destino.orientacao_videos.strip()
    if orientacao:
        cenas += f" Orientacoes da conta para os videos: {orientacao}."
    if (post.extras or {}).get("formato_pedido") == "video" or (
        destino.video == destino.Video.SEMPRE
    ):
        return f"VIDEO: este post vai TAMBEM como video: {cenas}\n"
    return (
        "FORMATO: devolva 'formato_sugerido': 'video' quando a ideia ganha em movimento e "
        "som (pergunta direta ao publico, caso ou historia, mito x verdade, demonstracao, "
        "passo a passo curto) ou o foco e alcance; 'carrossel' quando e para salvar e "
        "consultar (lista, checklist, tabela, numeros). Diga o porque em "
        f"'por_que_formato'. So quando sugerir video, {cenas}\n"
    )


def _segundos(valor) -> int:
    numero = re.search(r"\d+(?:[.,]\d+)?", str(valor or ""))
    return min(round(float(numero.group().replace(",", "."))), 20) if numero else 0


def ler_roteiro(bruto) -> dict:
    """O roteiro do JSON (da nossa IA ou da outra), limpo; {} se nao veio.
    Aceita o formato anterior (gancho + cenas com 'mostra')."""
    if not isinstance(bruto, dict):
        return {}
    entrada = [c for c in (bruto.get("cenas") or []) if isinstance(c, dict)]
    if str(bruto.get("gancho") or "").strip():
        entrada.insert(0, {"imagem": bruto["gancho"], "segundos": 2})
    cenas = []
    for cena in entrada[:CENAS_MAXIMAS]:
        imagem = str(cena.get("imagem") or cena.get("mostra") or "").strip()
        if not imagem:
            continue
        cenas.append(
            {
                "segundos": _segundos(cena.get("segundos")),
                "fala": " ".join(str(cena.get("fala") or "").split())[:300],
                "imagem": imagem[:600],
                "movimento": str(cena.get("movimento") or "").strip()[:300],
                "texto": str(cena.get("texto") or "").strip()[:80],
            }
        )
    return {"cenas": cenas} if cenas else {}


def roteiro(post) -> dict:
    return (post.extras or {}).get("video_ia") or {}


def roteiro_em_texto(item: dict) -> str:
    linhas = []
    for n, cena in enumerate(item.get("cenas") or [], start=1):
        tempo = f" ({cena['segundos']} s)" if cena.get("segundos") else ""
        linha = f"Cena {n}{tempo}"
        if cena.get("fala"):
            linha += f"\n   Fala: {cena['fala']}"
        linha += f"\n   Imagem inicial: {cena.get('imagem', '')}"
        if cena.get("movimento"):
            linha += f"\n   Movimento: {cena['movimento']}"
        if cena.get("texto"):
            linha += f'\n   Frase na tela: "{cena["texto"]}"'
        linhas.append(linha)
    return "\n".join(linhas)


PEDIDO = """\
Faca UM video curto para o {rede} de um negocio, vertical 9:16 (1080 x 1920),
de {minimo} a {maximo} segundos, com narracao em off em portugues do Brasil.

Regras:
- Os 2 PRIMEIROS segundos prendem a atencao (e o que faz a pessoa parar de
  rolar): comece no gancho, sem introducao, sem tela de titulo.
- Termine na chamada{chamada}.
- Texto na tela: {texto}.
- Estilo visual: {estilo}.
- Cores da marca: fundo {fundo}, destaque {destaque}.
- Nada de pessoas reais reconheciveis, marcas de terceiros ou imagem chocante.
{regras}{elementos}"""

SEM_ROTEIRO = """
Assunto do post (o video pode contar de outro jeito, mas a ideia e esta):
{assunto}

Monte voce o roteiro em cenas curtas seguindo as regras acima, me mostre e
depois gere o video.
"""


def pedido(post) -> str:
    """O texto para colar numa IA de video de fora, com a identidade inteira da
    conta. Com roteiro, ele vai junto; sem, o assunto e o pedido de roteiro."""
    from apps.social.models import ConfiguracaoSocial
    from apps.social.redes import rede

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    minimo, maximo = _duracao(destino)
    chamada = _chamada(destino)
    regras = ConfiguracaoSocial.carregar().regras.strip()
    estilo = identidade.descricao_do_estilo(destino, identidade.estilo_do_post(post), para="video")
    texto = PEDIDO.format(
        rede=rede(destino.rede).nome,
        minimo=minimo,
        maximo=maximo,
        chamada=f": {chamada}" if chamada else " (convide a seguir o perfil ou a ler o artigo)",
        texto=TEXTO_NO_VIDEO.get(destino.texto_no_video, TEXTO_NO_VIDEO[""]),
        estilo=estilo or "o que combinar com o assunto, sobrio",
        fundo=cores["fundo"],
        destaque=cores["destaque"],
        regras=f"- Regras do negocio: {regras}\n" if regras else "",
        elementos=(
            "- Personagens, cenarios e lugares FIXOS da conta (sempre exatamente assim; vou "
            "anexar as imagens de referencia):\n" + identidade.ficha(destino) + "\n"
            if identidade.elementos(destino)
            else ""
        ),
    )
    item = roteiro(post)
    if item:
        return texto + "\nRoteiro:\n" + roteiro_em_texto(item) + "\n"
    assunto = "\n".join(p for p in (post.artigo_titulo, post.gancho, (post.texto or "")[:800]) if p)
    return texto + SEM_ROTEIRO.format(assunto=assunto or "(sem texto ainda)")


# -- O video pronto (feito fora) -----------------------------------------------------------
class VideoInvalido(ValueError):
    pass


def guardar(post, conteudo: bytes, extensao: str, *, de: str) -> str:
    """O video vira o video do post, que passa a ir como video (Reels)."""
    caminho = f"social/{post.pk}/video-{de}{extensao}"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    caminho = default_storage.save(caminho, ContentFile(conteudo))
    post.extras = {**(post.extras or {}), "video": caminho, "formato": "video", "video_de": de}
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)  # o que vai publicado passa a ser o video
    return caminho


def enviar(post, arquivo) -> str:
    """O video feito fora vira o video do post."""
    extensao = Path(arquivo.name or "").suffix.lower()
    if extensao not in TIPOS:
        raise VideoInvalido("envie um video .mp4, .mov, .webm ou .m4v.")
    if arquivo.size > TAMANHO_MAXIMO:
        raise VideoInvalido(f"video grande demais (maximo {TAMANHO_MAXIMO // 1024 // 1024} MB).")
    return guardar(post, arquivo.read(), extensao, de="ia")


def tirar(post) -> None:
    """O post volta ao carrossel (o video, enviado ou montado, sai)."""
    extras = dict(post.extras or {})
    caminho = extras.pop("video", "")
    if caminho and default_storage.exists(caminho):
        default_storage.delete(caminho)
    extras.pop("video_de", None)
    if extras.get("formato") == "video":
        extras.pop("formato")
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)
