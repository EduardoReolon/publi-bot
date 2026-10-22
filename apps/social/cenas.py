"""O video por cenas, feito aqui: cada cena do roteiro (`video_ia`) vira uma
animacao curta do worker, com a fala em voz off e a legenda por codigo, e as
cenas sao coladas com uma transicao curta.

Cada cena, no post (`extras["video_ia"]["cenas"][i]`), alem do roteiro:

- `quadro`: a imagem inicial (o primeiro quadro), e `quadro_de`: "gerado",
  "enviado" ou "lugar" (a foto de um lugar real, usada como esta);
  `quadro_do_texto`: a descricao com que foi gerada (a tela avisa quando a
  descricao mudou e a imagem nao);
- `animando`: o trabalho pedido ao worker ({id, seed, desde}), conferido de
  minuto em minuto sem prender ninguem esperando: desligar o worker no meio
  perde so a cena em curso (pedida de novo, com a mesma seed, quando ele volta);
- `clipe`: a animacao do quadro, `seed` (a mesma imagem + o mesmo movimento +
  a mesma seed repetem o clipe; refazer a animacao troca a seed) e
  `clipe_do_movimento`;
- `aviso`: o que a ultima geracao nao conseguiu.

A fala nao entra no clipe: a voz e a legenda entram na montagem. Por isso
mudar a fala custa so montar de novo (segundos), e nao animar de novo.

A duracao de cada cena e a da fala (mais um respiro), ou os segundos da cena
no roteiro, o que for maior. O worker gera em trechos
(o FramePack, em pedacos de ~1,1 s) e pode devolver menos que o pedido: o clipe
curto e desacelerado ate 25% e, se ainda faltar, segura o ultimo quadro; o
longo e cortado.

A foto de um lugar real (Identidade visual > "Lugar real (foto)"):
- "de fundo": a cena parte da propria foto (sem personagem, e ela; com
  personagem, a metade de cima da foto fica e a de baixo e pintada com ele);
- "na janela": o PubliBot poe a foto num retangulo (a janela) e o gerador pinta
  a cena em volta, sem tocar nela (a mascara). Os dois pedem a rota de edicao
  do worker (docs/WORKER_IMAGEM_E_CENAS.md); sem ela, a cena sai so pela
  descricao, com aviso.
"""

from __future__ import annotations

import io
import logging
import math
import random
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.social import fontes, identidade, laminas, video, video_ia

logger = logging.getLogger("publibot.social")

QUADRO = (720, 1280)  # o 9:16 que o worker e a Sora animam
TRANSICAO = 0.3  # segundos de fusao entre uma cena e a outra
DESACELERA_ATE = 1.25
PALAVRAS_POR_SEGUNDO = 2.6
DURACAO_MINIMA = 1.5
LEGENDA_Y = 1460  # acima da faixa que o Instagram cobre (legenda do post, botoes)
FRASE_Y = 230


# -- As cenas no post ----------------------------------------------------------------------
def cenas(post) -> list[dict]:
    return [dict(c) for c in video_ia.roteiro(post).get("cenas") or []]


def _gravar(post, lista: list[dict]) -> None:
    extras = dict(post.extras or {})
    extras["video_ia"] = {**(extras.get("video_ia") or {}), "cenas": lista}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])


def _cena(post, n: int) -> tuple[list[dict], dict]:
    lista = cenas(post)
    if not 1 <= n <= len(lista):
        raise video.VideoIndisponivel(f"o video nao tem a cena {n}.")
    return lista, lista[n - 1]


def _existe(caminho: str) -> bool:
    return bool(caminho) and default_storage.exists(caminho)


def editar(post, n: int, **campos) -> None:
    """Muda o roteiro da cena (fala, imagem, movimento, frase, segundos). A
    imagem e o clipe ficam: a tela diz o que refazer."""
    lista, cena = _cena(post, n)
    for campo in ("fala", "imagem", "movimento", "texto"):
        if campo in campos:
            cena[campo] = " ".join(str(campos[campo] or "").split())[:600]
    if "segundos" in campos:
        cena["segundos"] = video_ia._segundos(campos["segundos"])
    _gravar(post, lista)


def para_a_tela(post) -> list[dict]:
    saida = []
    for n, cena in enumerate(cenas(post), start=1):
        quadro, clipe = cena.get("quadro", ""), cena.get("clipe", "")
        saida.append(
            {
                **cena,
                "n": n,
                "tem_quadro": _existe(quadro),
                "tem_clipe": _existe(clipe),
                "animando": bool(cena.get("animando")),
                "animando_desde": _hora((cena.get("animando") or {}).get("desde")),
                "animando_no": (cena.get("animando") or {}).get("modelo", ""),
                "imagem_mudou": _existe(quadro)
                and cena.get("quadro_de") == "gerado"
                and cena.get("quadro_do_texto", cena.get("imagem")) != cena.get("imagem"),
                "movimento_mudou": _existe(clipe)
                and cena.get("clipe_do_movimento", cena.get("movimento")) != cena.get("movimento"),
                "duracao": round(duracao_estimada(cena), 1),
            }
        )
    return saida


def _hora(quando: str | None) -> str:
    """'14:05' (na hora local) de um isoformat, ou ''."""
    from datetime import datetime

    if not quando:
        return ""
    return timezone.localtime(datetime.fromisoformat(quando)).strftime("%H:%M")


def duracao_estimada(cena: dict) -> float:
    """O tempo da cena pela fala (antes de ter o audio): ~2,6 palavras/s."""
    palavras = len((cena.get("fala") or "").split())
    pela_fala = palavras / PALAVRAS_POR_SEGUNDO + video.PAUSA if palavras else 0
    return max(pela_fala, float(cena.get("segundos") or 0), DURACAO_MINIMA)


# -- A imagem inicial -----------------------------------------------------------------------
def _png(imagem) -> bytes:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


def _cobrir(imagem, tamanho: tuple[int, int]):
    """A imagem cobrindo o tamanho inteiro, cortada no meio."""
    from PIL import ImageOps

    return ImageOps.fit(imagem.convert("RGB"), tamanho, centering=(0.5, 0.5))


def _abrir(arquivo):
    from PIL import Image, ImageOps

    with arquivo.open("rb") as aberto:
        imagem = ImageOps.exif_transpose(Image.open(aberto))
        imagem.load()
    return imagem.convert("RGB")


def _lugar_da_cena(post, cena: dict):
    """(lugar, "janela" | "fundo") citado na imagem da cena, ou (None, "")."""
    texto = cena.get("imagem") or ""
    achados = identidade.citados(texto, video_ia.lugares(post.destino))
    if not achados:
        return None, ""
    from apps.social.iscas import normalizar

    return achados[0], "janela" if "janela" in normalizar(texto) else "fundo"


def _janela(lugar, cor: str, *, a_direita: bool) -> tuple[bytes, bytes]:
    """(base, mascara) da cena com a foto do lugar numa janela no alto: a foto
    fica (opaca na mascara) e o resto e pintado (transparente)."""
    from PIL import Image, ImageDraw

    largura, altura = QUADRO
    caixa = (int(largura * 0.46), int(altura * 0.24))
    x = largura - caixa[0] - int(largura * 0.07) if a_direita else int(largura * 0.07)
    y = int(altura * 0.07)
    base = Image.new("RGB", QUADRO, cor)
    base.paste(_cobrir(_abrir(lugar.imagem), caixa), (x, y))
    mascara = Image.new("RGBA", QUADRO, (0, 0, 0, 0))
    ImageDraw.Draw(mascara).rectangle((x, y, x + caixa[0], y + caixa[1]), fill=(0, 0, 0, 255))
    return _png(base), _png(mascara)


def _fundo(lugar, com_gente: bool) -> tuple[bytes, bytes | None]:
    """(base, mascara) da cena de fundo: a foto inteira; com personagem, a
    metade de baixo e pintada (transparente) e a de cima fica."""
    from PIL import Image, ImageDraw

    base = _cobrir(_abrir(lugar.imagem), QUADRO)
    if not com_gente:
        return _png(base), None
    largura, altura = QUADRO
    mascara = Image.new("RGBA", QUADRO, (0, 0, 0, 0))
    ImageDraw.Draw(mascara).rectangle((0, 0, largura, int(altura * 0.45)), fill=(0, 0, 0, 255))
    return _png(base), _png(mascara)


# A referencia vai para o IP-Adapter, que copia a imagem INTEIRA: o fundo, a
# composicao e quantas vezes a figura aparece. A folha de personagem (fundo
# branco, a mesma pessoa de 4 lados) virava cena branca com o Carlos repetido.
# Por isso vai so a 1a figura da folha, e com forca menor; o cenario, que nao
# tem uma figura para recortar, vai so pela descricao (no prompt).
FORCA_DA_REFERENCIA = 0.45
NEGATIVO_DA_REFERENCIA = (
    "character sheet, multiple views, turnaround, duplicate person, collage, split screen, "
    "white background, blank background"
)


def primeira_figura(imagem):
    """A 1a figura (a da esquerda, no alto) de uma folha de referencia, num
    quadrado: a folha tem a mesma figura varias vezes lado a lado sobre um fundo
    liso. Foto comum (sem fundo liso ou com uma figura so) volta inteira, no
    quadrado. Algoritmo: a cor do fundo vem dos cantos; colunas com algo
    diferente dela sao figura, e um vao de fundo separa uma figura da outra."""
    from PIL import Image, ImageChops, ImageOps

    imagem = imagem.convert("RGB")
    largura, altura = imagem.size
    cantos = [imagem.getpixel(p) for p in [(0, 0), (largura - 1, 0), (0, altura - 1)]]
    fundo = tuple(sorted(c[i] for c in cantos)[1] for i in range(3))
    diferenca = ImageChops.difference(imagem, Image.new("RGB", imagem.size, fundo)).convert("L")
    figura = diferenca.point(lambda v: 255 if v > 30 else 0)
    if figura.getbbox() is None or sum(figura.histogram()[255:]) > 0.6 * largura * altura:
        return ImageOps.pad(imagem, (max(imagem.size),) * 2, color=fundo)  # foto: inteira
    # A fileira de cima (a folha de personagem traz as expressoes embaixo).
    linhas = [figura.crop((0, y, largura, y + 1)).getbbox() is not None for y in range(altura)]
    topo = linhas.index(True)
    fim = topo
    while fim < altura and (linhas[fim] or any(linhas[fim : fim + altura // 25])):
        fim += 1
    fileira = figura.crop((0, topo, largura, fim))
    colunas = [
        fileira.crop((x, 0, x + 1, fim - topo)).getbbox() is not None for x in range(largura)
    ]
    vao = max(4, largura // 50)
    inicio = colunas.index(True)
    x = inicio
    while x < largura and (colunas[x] or any(colunas[x : x + vao])):
        x += 1
    caixa = figura.crop((inicio, topo, x, fim)).getbbox()
    margem = max(8, (x - inicio) // 10)
    recorte = imagem.crop(
        (
            max(0, inicio + caixa[0] - margem),
            max(0, topo + caixa[1] - margem),
            min(largura, inicio + caixa[2] + margem),
            min(altura, topo + caixa[3] + margem),
        )
    )
    return ImageOps.pad(recorte, (max(recorte.size),) * 2, color=fundo)


def principal_da_cena(post, cena: dict):
    """O elemento que vira a referencia da cena: o 1o PERSONAGEM citado no texto
    (pela posicao no texto, nao pela ordem do cadastro); sem personagem, o 1o
    objeto. So um: o IP-Adapter mistura as referencias numa so aparencia, e com
    duas pessoas saia a roupa de um com o rosto do outro. Os demais vao pelo texto."""
    from apps.social.iscas import normalizar

    texto = f" {normalizar(cena.get('imagem') or '')} "
    com_imagem = [
        e
        for e in identidade.citados(cena.get("imagem") or "", identidade.elementos(post.destino))
        if e.imagem and e.tipo in (e.Tipo.PERSONAGEM, e.Tipo.OBJETO)
    ]
    com_imagem.sort(
        key=lambda e: (e.tipo != e.Tipo.PERSONAGEM, texto.find(f" {normalizar(e.nome)} "))
    )
    return com_imagem[0] if com_imagem else None


def _referencias(post, cena: dict) -> list[bytes]:
    """A figura do elemento principal da cena (a 1a da folha), ou nenhuma."""
    principal = principal_da_cena(post, cena)
    return [_png(primeira_figura(_abrir(principal.imagem)))] if principal else []


def usa_referencia(post, cena: dict) -> bool:
    """A imagem inicial da cena leva referencia de personagem (no worker, outro
    modelo de imagem: as cenas com e sem referencia sao geradas em grupos)."""
    return principal_da_cena(post, cena) is not None


def _guardar(post, nome: str, conteudo: bytes) -> str:
    caminho = f"social/{post.pk}/{nome}"
    if default_storage.exists(caminho):
        default_storage.delete(caminho)
    return default_storage.save(caminho, ContentFile(conteudo))


def gerar_quadro(post, n: int) -> str:
    """A imagem inicial da cena n pelo gerador daqui (com as referencias dos
    personagens e a foto do lugar, quando o worker tem a rota de edicao)."""
    lista, cena = _cena(post, n)
    lugar, como = _lugar_da_cena(post, cena)
    personagens = [
        e
        for e in identidade.citados(cena.get("imagem") or "", identidade.elementos(post.destino))
        if e.tipo != e.Tipo.LUGAR
    ]
    aviso = ""
    if lugar is not None and como == "fundo" and not personagens:
        conteudo, de = _png(_cobrir(_abrir(lugar.imagem), QUADRO)), "lugar"
    else:
        prompts = identidade.prompts_da_cena(
            post,
            {"visual": cena.get("imagem", ""), "movimento": cena.get("movimento", "")},
            chave=f"cena-{n}",
            prompt="social_quadro",
        )
        cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
        base = mascara = None
        if lugar is not None and como == "janela":
            texto = cena.get("imagem") or ""
            base, mascara = _janela(lugar, cores["fundo"], a_direita="direita" in texto.lower())
        elif lugar is not None:
            base, mascara = _fundo(lugar, com_gente=True)
        conteudo, aviso = fontes.gerar_quadro(
            prompts["imagem"],
            tamanho=f"{QUADRO[0]}x{QUADRO[1]}",
            referencias=_referencias(post, cena),
            base=base,
            mascara=mascara,
            forca=FORCA_DA_REFERENCIA,
            negativo=NEGATIVO_DA_REFERENCIA,
        )
        de = "gerado"
    post.refresh_from_db()
    lista, cena = _cena(post, n)
    cena.update(
        quadro=_guardar(post, f"cena-{n}-quadro.png", conteudo),
        quadro_de=de,
        quadro_do_texto=cena.get("imagem", ""),
        aviso=aviso,
    )
    cena.pop("clipe", None)  # imagem nova, animacao nova
    cena.pop("animando", None)
    _gravar(post, lista)
    return cena["quadro"]


def enviar_quadro(post, n: int, arquivo) -> str:
    """A imagem inicial feita fora (ou uma foto): vira o quadro da cena n."""
    from PIL import UnidentifiedImageError

    try:
        imagem = _abrir(arquivo)
    except (UnidentifiedImageError, OSError) as exc:
        raise video.VideoIndisponivel("envie uma imagem JPG, PNG ou WebP.") from exc
    lista, cena = _cena(post, n)
    cena.update(
        quadro=_guardar(post, f"cena-{n}-quadro.png", _png(_cobrir(imagem, QUADRO))),
        quadro_de="enviado",
        quadro_do_texto=cena.get("imagem", ""),
        aviso="",
    )
    cena.pop("clipe", None)
    _gravar(post, lista)
    return cena["quadro"]


# -- A animacao -----------------------------------------------------------------------------
# O que o worker gera e caro (na placa de 8 GB, ~9,5 min por trecho do
# FramePack): pede-se o MENOR clipe que, desacelerado ate 25% e com o ultimo
# quadro parado ate meio segundo, cobre o tempo da cena. Uma cena de 3 s pede 2
# trechos, e nao 3.
TRECHO = 1.2  # segundos por trecho; o worker faz round(seconds / 1,2) trechos (contrato 2.12)
SEGURA_ATE = 0.5  # o ultimo quadro parado no fim do gesto
# A resolucao da animacao: a do "balde" 9:16 do FramePack (nada e redimensionado
# no worker, e o tempo cresce com os pixels). A montagem amplia para 1080x1920.
TAMANHO_DA_ANIMACAO = settings.ANIMACAO_TAMANHO
# Animacao pedida e nao conferida ha mais que isto: desiste da cena (aviso).
ESPERA_MAXIMA_HORAS = 6


def segundos_pedidos(cena: dict) -> int:
    """Os segundos a pedir: o clipe minimo (veja acima) em trechos inteiros. O
    worker faz `round(seconds / TRECHO)` trechos; `round(trechos * TRECHO)`
    segundos voltam exatamente nesses trechos (2 -> 2 s, 3 -> 4 s, 4 -> 5 s)."""
    clipe = max((duracao_estimada(cena) - SEGURA_ATE) / DESACELERA_ATE, TRECHO)
    trechos = min(math.ceil(clipe / TRECHO - 1e-6), 5)
    return max(1, round(trechos * TRECHO))


def retomar_animacoes() -> int:
    """De 5 em 5 minutos (publicar_posts): confere as animacoes em curso de todos
    os posts. A conferencia normal e da tarefa `gerar_cenas`, que volta de minuto
    em minuto; mas uma implantacao no meio pode segurar essa volta por horas, e o
    clipe pronto no worker nao aparecia na tela. Devolve quantos clipes chegaram."""
    from datetime import timedelta

    from apps.inference.providers.base import ProviderError
    from apps.social.models import Post

    chegaram = 0
    recentes = Post.objects.select_related("destino").filter(
        extras__has_key="video_ia",
        atualizado_em__gte=timezone.now() - timedelta(hours=ESPERA_MAXIMA_HORAS + 2),
    )
    for post in recentes:
        for n, cena in enumerate(cenas(post), start=1):
            if not cena.get("animando") or cena.get("parada"):
                continue
            try:
                situacao = conferir_animacao(post, n)
                if situacao == "perdida":
                    pedir_animacao(post, n)
                chegaram += situacao == "pronta"
            except (video.VideoIndisponivel, ProviderError) as exc:
                post.refresh_from_db()
                post.avisos = [*(post.avisos or []), f"cena {n}: {exc}"]
                post.save(update_fields=["avisos", "atualizado_em"])
            post.refresh_from_db()
    return chegaram


def parar_animacao(post, n: int) -> None:
    """Esquece a animacao em curso da cena n e marca a cena como parada: a
    tarefa nao confere nem pede de novo (nem se o worker reiniciar) ate um pedido
    explicito (`liberar`). O worker termina o que ja comecou, ou para ao reiniciar."""
    lista, cena = _cena(post, n)
    cena.pop("animando", None)
    cena["parada"] = True
    _gravar(post, lista)


def liberar(post, n: int | None = None) -> None:
    """Tira a marca de parada (da cena n, ou de todas): pedido explicito de animar."""
    lista = cenas(post)
    for i, cena in enumerate(lista, start=1):
        if n in (None, i):
            cena.pop("parada", None)
    _gravar(post, lista)


def pedir_animacao(post, n: int, *, nova_seed: bool = False, maior: bool = False) -> str:
    """Pede a animacao da cena n ao worker e guarda o trabalho na cena, sem
    esperar (`conferir_animacao` busca depois). `nova_seed`: outra variacao do
    mesmo movimento (refazer a animacao). `maior`: no modelo maior, com a MESMA
    seed (refazer em qualidade maxima, mantendo a composicao). Um trabalho perdido
    (o worker reiniciou) e pedido de novo no modelo em que tinha sido pedido."""
    lista, cena = _cena(post, n)
    maior = maior or cena.pop("pedir_no_maior", False)
    if not _existe(cena.get("quadro", "")):
        raise video.VideoIndisponivel(f"a cena {n} ainda nao tem imagem inicial.")
    seed = cena.get("seed")
    if nova_seed or seed is None:
        seed = random.randint(1, 2**31 - 1)  # noqa: S311 - so variacao, nao seguranca
    prompts = identidade.prompts_da_cena(
        post,
        {"visual": cena.get("imagem", ""), "movimento": cena.get("movimento", "")},
        chave=f"cena-{n}",
        prompt="social_quadro",
    )
    with default_storage.open(cena["quadro"], "rb") as arquivo:
        quadro = arquivo.read()
    trabalho = fontes.pedir_animacao(
        quadro,
        prompts["movimento"] or prompts["imagem"],
        segundos=segundos_pedidos(cena),
        tamanho=TAMANHO_DA_ANIMACAO,
        seed=seed,
        maior=maior,
    )
    identificador = trabalho["id"]
    post.refresh_from_db()
    lista, cena = _cena(post, n)
    cena["animando"] = {
        "id": identificador,
        # A usada, que o worker devolve (a mesma que pedimos, ou a sorteada).
        "seed": trabalho.get("seed") or seed,
        "modelo": trabalho.get("model") or "",
        "maior": maior,
        "movimento": cena.get("movimento", ""),
        "desde": timezone.now().isoformat(),
    }
    _gravar(post, lista)
    return identificador


def conferir_animacao(post, n: int) -> str:
    """Como esta a animacao pedida da cena n: "pronta" (o clipe foi gravado),
    "esperando" (na fila, gerando, ou o worker fora do ar agora: confere-se
    depois), "perdida" (o worker reiniciou e esqueceu: pede-se de novo, com a
    mesma seed) ou "nenhuma". Falha de verdade levanta `VideoIndisponivel`."""
    from datetime import datetime, timedelta

    from apps.inference.providers.base import ProviderTransientError

    lista, cena = _cena(post, n)
    animando = cena.get("animando") or {}
    if not animando:
        return "nenhuma"
    desde = datetime.fromisoformat(animando["desde"])
    try:
        situacao = fontes.situacao_da_animacao(animando["id"])
    except ProviderTransientError:
        situacao = {"status": "fora_do_ar"}
    estado = situacao.get("status")
    if estado == "completed":
        clipe = fontes.baixar_animacao(animando["id"])
        post.refresh_from_db()
        lista, cena = _cena(post, n)
        cena.update(
            clipe=_guardar(post, f"cena-{n}-clipe.mp4", clipe),
            seed=animando["seed"],
            modelo=animando.get("modelo", ""),
            no_maior=bool(animando.get("maior")),
            clipe_do_movimento=animando.get("movimento", ""),
            aviso="",
        )
        cena.pop("animando", None)
        _gravar(post, lista)
        return "pronta"
    if estado in ("failed", "sumiu") or timezone.now() - desde > timedelta(
        hours=ESPERA_MAXIMA_HORAS
    ):
        cena.pop("animando", None)
        if estado == "sumiu":
            cena["seed"] = animando["seed"]  # pede de novo a mesma variacao
            cena["pedir_no_maior"] = bool(animando.get("maior"))  # e no mesmo modelo
            _gravar(post, lista)
            return "perdida"
        _gravar(post, lista)
        motivo = (situacao.get("error") or {}).get("message") or "o worker nao terminou a tempo"
        raise video.VideoIndisponivel(f"a animacao da cena {n} falhou: {motivo}")
    return "esperando"


def faltam(post) -> list[int]:
    """As cenas sem clipe (as que estao animando inclusive)."""
    return [n for n, c in enumerate(cenas(post), start=1) if not _existe(c.get("clipe", ""))]


# -- A montagem -----------------------------------------------------------------------------
def _segmento(pasta: Path, n: int, cena: dict, post, cores: dict, ffmpeg: str, ffprobe: str):
    """Uma cena: o clipe (ou o quadro, aproximando devagar) no tempo da fala,
    com a legenda e a frase por cima e a voz. Devolve (arquivo, duracao)."""
    largura, altura = video.LARGURA, video.ALTURA
    audio = None
    fala = (cena.get("fala") or "").strip()
    if fala and fontes.tem_voz():
        audio = video._guardar_em(pasta, f"{n}.mp3", video._audio(post, fala))
        # O gesto planejado tem o seu tempo: fala curta nao corta a cena.
        duracao = max(video._duracao(ffprobe, audio) + video.PAUSA, cena.get("segundos") or 0)
    else:
        duracao = duracao_estimada(cena)
    duracao = max(duracao, DURACAO_MINIMA)
    if _existe(cena.get("clipe", "")):
        clipe = video._copiar_do_armazenamento(pasta, f"{n}-clipe.mp4", cena["clipe"])
        tem = max(video._duracao(ffprobe, clipe), 0.1)
        fator = min(max(duracao / tem, 1.0), DESACELERA_ATE)
        entradas = ["-i", str(clipe)]
        fundo = (
            f"[0:v]scale={largura}:{altura}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={largura}:{altura},fps=30,setpts={fator:.4f}*PTS,"
            f"tpad=stop_mode=clone:stop_duration={duracao:.2f},"
            f"trim=duration={duracao:.2f},setsar=1[bg]"
        )
    elif _existe(cena.get("quadro", "")):
        quadro = video._copiar_do_armazenamento(pasta, f"{n}-quadro.png", cena["quadro"])
        quadros = max(1, int(duracao * 30))
        entradas = ["-loop", "1", "-i", str(quadro)]
        fundo = (
            f"[0:v]scale={largura * 2}:{altura * 2},zoompan=z='min(1+0.0004*on,1.12)':"
            f"d={quadros}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={largura}x{altura}:"
            "fps=30,setsar=1[bg]"
        )
    else:
        raise video.VideoIndisponivel(f"a cena {n} ainda nao tem imagem nem animacao.")
    entradas += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    filtros, rotulo, proxima = [fundo], "bg", 2
    sobreposicoes = []
    if cena.get("texto") and post.destino.texto_no_video == "frases":
        png = video._guardar_em(
            pasta,
            f"{n}-frase.png",
            video.png_do_texto(cena["texto"], cores, tamanho=64, cor=cores["destaque"]),
        )
        sobreposicoes.append((png, FRASE_Y, 0.2, duracao))
    if fala and post.destino.texto_no_video == "legendas":
        trechos = video.trechos_da_legenda(fala)
        falado = duracao - video.PAUSA
        for i, (trecho, (inicio, fim)) in enumerate(
            zip(trechos, video.tempos_da_legenda(trechos, falado), strict=True)
        ):
            png = video._guardar_em(
                pasta, f"{n}-l{i}.png", video.png_do_texto(trecho, cores, cor=cores["destaque"])
            )
            sobreposicoes.append((png, LEGENDA_Y, inicio, fim))
    for i, (png, y, inicio, fim) in enumerate(sobreposicoes):
        entradas += ["-loop", "1", "-i", str(png)]
        filtros.append(
            f"[{rotulo}][{proxima}:v]overlay=0:{y}:enable='between(t,{inicio:.2f},{fim:.2f})'[s{i}]"
        )
        rotulo, proxima = f"s{i}", proxima + 1
    filtros.append(f"[{rotulo}]format=yuv420p[saida]")
    filtros.append("[1:a]apad[som]")
    saida = pasta / f"cena-{n}.mp4"
    video._rodar(
        [
            ffmpeg, "-y", *entradas,
            "-filter_complex", ";".join(filtros),
            "-map", "[saida]", "-map", "[som]", "-t", f"{duracao:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            str(saida),
        ]
    )  # fmt: skip
    return saida, duracao


def _juntar(ffmpeg: str, pasta: Path, partes: list[tuple[Path, float]]) -> Path:
    """As cenas coladas com uma fusao curta (imagem e som)."""
    final = pasta / "video.mp4"
    if len(partes) == 1:
        return partes[0][0]
    entradas, filtros = [], []
    for arquivo, _duracao in partes:
        entradas += ["-i", str(arquivo)]
    video_atual, som_atual, decorrido = "0:v", "0:a", partes[0][1]
    for i in range(1, len(partes)):
        deslocamento = decorrido - TRANSICAO * i
        filtros.append(
            f"[{video_atual}][{i}:v]xfade=transition=fade:duration={TRANSICAO}:"
            f"offset={deslocamento:.2f}[v{i}]"
        )
        filtros.append(f"[{som_atual}][{i}:a]acrossfade=d={TRANSICAO}[a{i}]")
        video_atual, som_atual = f"v{i}", f"a{i}"
        decorrido += partes[i][1]
    video._rodar(
        [
            ffmpeg, "-y", *entradas,
            "-filter_complex", ";".join(filtros),
            "-map", f"[{video_atual}]", "-map", f"[{som_atual}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(final),
        ]
    )  # fmt: skip
    return final


def montar(post) -> str:
    """O video do post: todas as cenas no tempo das falas, com a voz e as
    legendas. Cena sem animacao ainda entra com o quadro aproximando devagar
    (da para ver o video antes de animar tudo)."""
    ffmpeg, ffprobe = video._ffmpeg()
    lista = cenas(post)
    if not lista:
        raise video.VideoIndisponivel("o post nao tem cenas de video.")
    cores = {**laminas.CORES, **{k: v for k, v in (post.destino.cores or {}).items() if v}}
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        partes = [
            _segmento(pasta, n, cena, post, cores, ffmpeg, ffprobe)
            for n, cena in enumerate(lista, start=1)
        ]
        final = _juntar(ffmpeg, pasta, partes)
        return video_ia.guardar(post, final.read_bytes(), ".mp4", de="cenas")


# -- O pedido das imagens iniciais para uma IA de imagem de fora ---------------------------
PEDIDO_DAS_IMAGENS = """\
Gere as imagens iniciais (o PRIMEIRO quadro) das cenas abaixo, uma por cena, na
ordem, vertical 9:16 (1080 x 1920). Cada uma vai ser animada depois (um gesto
lento a partir dela), entao:

- NAO escreva nenhum texto, letra, numero ou logotipo na imagem.
- A pose e a do COMECO do gesto descrito; boca fechada (a voz e de narrador).
- Estilo (o mesmo em todas): {estilo}.
- Cores da marca: fundo {fundo}, destaque {destaque}.
- Nada de pessoas reais reconheciveis ou marcas de terceiros.
{elementos}
"""


def pedido_das_imagens(post) -> str:
    """O texto para colar numa IA de imagem (com as referencias anexadas): as
    imagens iniciais de todas as cenas, para enviar cada uma na sua cena."""
    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    texto = PEDIDO_DAS_IMAGENS.format(
        estilo=identidade.descricao_do_estilo(destino, identidade.estilo_do_post(post))
        or "o das imagens da conta",
        fundo=cores["fundo"],
        destaque=cores["destaque"],
        elementos=(
            "- Personagens e lugares fixos (vou anexar as imagens; desenhe sempre igual):\n"
            + identidade.ficha(destino)
            + "\n"
            if identidade.elementos(destino)
            else ""
        ),
    )
    for n, cena in enumerate(cenas(post), start=1):
        texto += f"\nCena {n}: {cena.get('imagem', '')}"
        if cena.get("movimento"):
            texto += f" (o gesto que vem depois: {cena['movimento']})"
    return texto + "\n"
