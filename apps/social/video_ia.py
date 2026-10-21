"""Video por IA (Reels): um clipe feito por uma IA de video, a partir de um
roteiro proprio — que pode contar algo bem diferente das laminas.

O fluxo:

1. Quem escreve o post sugere o formato. Quando sugere video (ou a conta quer
   sempre), devolve tambem o ROTEIRO (`extras["video_ia"]`): o gancho dos 2
   primeiros segundos (obrigatorio: e o que segura quem rola o feed), as cenas
   (o que se ve, o que se ouve, o texto na tela se a conta usa) e a chamada do
   fim (o padrao da conta, na Identidade visual).
2. `pedido`: o texto para colar numa IA de video (Sora, Veo, Kling, Runway, ou
   o ChatGPT/Gemini que gera video), com a identidade da conta inteira:
   estilo, orientacoes gerais e de video, personagens fixos, cores, regras do
   negocio e o que fazer com texto no video. Sem roteiro (a IA nao sugeriu
   video), o pedido leva o post e pede para ela montar o roteiro com as mesmas
   regras.
3. O MP4 pronto volta pelo post (`enviar`) e o post vai como video.
4. `gerar_aqui` (so pelo botao): a imagem do comeco pelo gerador do PubliBot e
   o movimento pela conexao de animacao (o worker). Numa placa pequena leva
   muito tempo por poucos segundos: fica para quando houver placa forte.

O video narrado das laminas (`video.py`) continua no codigo, fora da tela.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.social import identidade, laminas

logger = logging.getLogger("publibot.social")

# Duracao do clipe por rede, em segundos (minimo, maximo).
DURACAO = {"instagram": (15, 30), "linkedin": (20, 60), "gmn": (10, 30)}
TIPOS = {".mp4", ".mov", ".webm", ".m4v"}
TAMANHO_MAXIMO = 200 * 1024 * 1024
CENAS_MAXIMAS = 8

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


def instrucao(post) -> str:
    """Para quem escreve o post: sugerir o formato e, quando for video, o
    roteiro. Vazio se a conta nao quer video."""
    destino = post.destino
    if destino.video == destino.Video.NUNCA:
        return ""
    minimo, maximo = _duracao(destino)
    chamada = _chamada(destino)
    roteiro = (
        "devolva 'video' com o ROTEIRO de um clipe de {minimo} a {maximo} s feito por IA de "
        "video — pode contar a ideia de um jeito diferente das laminas (uma cena, um "
        "dialogo, uma demonstracao): 'gancho' (o que se ve e se ouve nos 2 PRIMEIROS "
        "segundos: obrigatorio, e o que faz parar de rolar), 'cenas' (de 2 a {cenas}, cada "
        "uma com 'mostra' — o que se ve, concreto —, 'fala' — o que se ouve: narracao, "
        "dialogo ou som —, 'texto' — {texto} — e 'segundos') e 'chamada' (como termina{padrao})."
    ).format(
        minimo=minimo,
        maximo=maximo,
        cenas=CENAS_MAXIMAS,
        texto=(
            "a frase curta que aparece na tela, se houver"
            if destino.texto_no_video == "frases"
            else "deixe vazio (a conta nao usa frase na tela)"
        ),
        padrao=f"; padrao da conta: {chamada}" if chamada else "",
    )
    orientacao = destino.orientacao_videos.strip()
    if orientacao:
        roteiro += f" Orientacoes da conta para os videos: {orientacao}."
    pedido = (post.extras or {}).get("formato_pedido") == "video"
    if pedido or destino.video == destino.Video.SEMPRE:
        return f"VIDEO: este post vai TAMBEM como video: {roteiro}\n"
    return (
        "FORMATO: devolva 'formato_sugerido': 'video' quando a ideia ganha em movimento e "
        "som (pergunta direta ao publico, caso ou historia, mito x verdade, demonstracao, "
        "passo a passo curto) ou o foco e alcance; 'carrossel' quando e para salvar e "
        "consultar (lista, checklist, tabela, numeros). Diga o porque em "
        f"'por_que_formato'. So quando sugerir video, {roteiro}\n"
    )


def ler_roteiro(bruto) -> dict:
    """O roteiro do JSON (da nossa IA ou da outra), limpo; {} se nao veio."""
    if not isinstance(bruto, dict):
        return {}
    cenas = []
    for cena in (bruto.get("cenas") or [])[:CENAS_MAXIMAS]:
        if not isinstance(cena, dict) or not str(cena.get("mostra") or "").strip():
            continue
        numero = re.search(r"\d+(?:[.,]\d+)?", str(cena.get("segundos") or ""))
        segundos = min(round(float(numero.group().replace(",", "."))), 20) if numero else 0
        cenas.append(
            {
                "mostra": str(cena["mostra"]).strip()[:400],
                "fala": str(cena.get("fala") or "").strip()[:300],
                "texto": str(cena.get("texto") or "").strip()[:80],
                "segundos": segundos,
            }
        )
    gancho = str(bruto.get("gancho") or "").strip()[:300]
    if not (gancho or cenas):
        return {}
    return {"gancho": gancho, "cenas": cenas, "chamada": str(bruto.get("chamada") or "")[:200]}


def roteiro(post) -> dict:
    return (post.extras or {}).get("video_ia") or {}


def roteiro_em_texto(item: dict) -> str:
    linhas = []
    if item.get("gancho"):
        linhas.append(f"0-2 s (GANCHO): {item['gancho']}")
    for n, cena in enumerate(item.get("cenas") or [], start=1):
        tempo = f" ({cena['segundos']} s)" if cena.get("segundos") else ""
        linha = f"Cena {n}{tempo}: {cena['mostra']}"
        if cena.get("fala"):
            linha += f"\n   Ouve-se: {cena['fala']}"
        if cena.get("texto"):
            linha += f'\n   Texto na tela: "{cena["texto"]}"'
        linhas.append(linha)
    if item.get("chamada"):
        linhas.append(f"Fim (CHAMADA): {item['chamada']}")
    return "\n".join(linhas)


PEDIDO = """\
Faca UM video curto para o {rede} de um negocio, vertical 9:16 (1080 x 1920),
de {minimo} a {maximo} segundos, falado em portugues do Brasil.

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

Monte voce o roteiro (o gancho, de 2 a {cenas} cenas e a chamada) seguindo as
regras acima, me mostre o roteiro e depois gere o video.
"""


def pedido(post) -> str:
    """O texto para colar numa IA de video, com a identidade inteira da conta.
    Com roteiro, ele vai junto; sem, o assunto do post e o pedido de roteiro."""
    from apps.social.models import ConfiguracaoSocial
    from apps.social.redes import rede

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    minimo, maximo = _duracao(destino)
    chamada = (roteiro(post).get("chamada") or _chamada(destino)).strip()
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
            "- Personagens e cenarios FIXOS da conta (sempre exatamente assim; se voce aceita "
            "imagem de referencia, vou anexar):\n" + identidade.ficha(destino) + "\n"
            if identidade.elementos(destino)
            else ""
        ),
    )
    item = roteiro(post)
    if item:
        return texto + "\nRoteiro:\n" + roteiro_em_texto(item) + "\n"
    assunto = "\n".join(
        p
        for p in (
            post.artigo_titulo,
            post.gancho,
            (post.texto or "")[:800],
        )
        if p
    )
    return texto + SEM_ROTEIRO.format(assunto=assunto or "(sem texto ainda)", cenas=CENAS_MAXIMAS)


# -- O video pronto ------------------------------------------------------------------------
class VideoInvalido(ValueError):
    pass


def _guardar(post, conteudo: bytes, extensao: str) -> str:
    caminho = f"social/{post.pk}/video-ia{extensao}"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    caminho = default_storage.save(caminho, ContentFile(conteudo))
    post.extras = {**(post.extras or {}), "video": caminho, "formato": "video", "video_de": "ia"}
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)  # o que vai publicado passa a ser o video
    return caminho


def enviar(post, arquivo) -> str:
    """O video feito fora vira o video do post (que passa a ir como video)."""
    extensao = Path(arquivo.name or "").suffix.lower()
    if extensao not in TIPOS:
        raise VideoInvalido("envie um video .mp4, .mov, .webm ou .m4v.")
    if arquivo.size > TAMANHO_MAXIMO:
        raise VideoInvalido(f"video grande demais (maximo {TAMANHO_MAXIMO // 1024 // 1024} MB).")
    return _guardar(post, arquivo.read(), extensao)


def gerar_aqui(post) -> str:
    """O clipe pelo gerador do PubliBot: a imagem do comeco (a cena do gancho)
    e o movimento pela conexao de animacao. Leva minutos (ou muito mais):
    chamar de tarefa, nunca da tela."""
    from apps.social import fontes

    item = roteiro(post)
    cenas = item.get("cenas") or []
    cena = item.get("gancho") or (cenas[0]["mostra"] if cenas else "") or post.gancho
    movimento = (cenas[0].get("fala") if cenas else "") or ""
    prompts = identidade.prompts_da_cena(
        post, {"visual": cena, "movimento": movimento}, chave="video"
    )
    imagem = fontes.gerar_imagem(prompts["imagem"], tamanho="720x1280")
    clipe = fontes.animar(imagem, prompts["movimento"] or prompts["imagem"], segundos=4)
    return _guardar(post, clipe, ".mp4")


def tirar(post) -> None:
    extras = dict(post.extras or {})
    if extras.get("video_de") == "ia":
        caminho = extras.pop("video", "")
        if caminho and default_storage.exists(caminho):
            default_storage.delete(caminho)
        extras.pop("video_de", None)
        if extras.get("formato") == "video":
            extras.pop("formato")
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    laminas.refazer_imagens(post)
