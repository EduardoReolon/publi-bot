"""Identidade visual da conta: o pedido de imagens das laminas para uma IA de
imagem (ChatGPT, Gemini, Ideogram, Midjourney, Firefly, Canva...).

A IA de imagem faz a CENA (foto, ilustracao, quadrinho) e o PubliBot escreve o
texto por cima: modelo de imagem ainda troca letras, sobretudo em portugues, e
o texto da lamina precisa sair exato (numero, fonte citada). Por isso o pedido
diz "sem nenhum texto na imagem" e deixa espaco para ele.

O estilo e o da conta (`Destino.estilo_das_imagens`, `descricao_do_estilo`,
`cenarios_de_foto`) e vai igual em todo pedido: e o que faz o perfil ter uma
linha. Laminas com fundo padrao da conta (`FundoDeLamina`) ficam de fora — ja
tem imagem.
"""

from __future__ import annotations

from apps.social import laminas

ESTILOS = {
    "foto": "foto realista, luz natural, aparencia de foto de verdade (nao de banco de imagens)",
    "ilustracao": "ilustracao (desenho), sem aparencia de foto",
    "quadrinhos": (
        "cena de historia em quadrinhos: personagens desenhados, expressoes claras, "
        "um momento por quadro (sem baloes de fala e sem texto)"
    ),
    "apresentacao": (
        "slide de apresentacao: fundo limpo, formas geometricas, icones e graficos simples, "
        "sem cena com pessoas; muito espaco livre para o texto"
    ),
    "misto": "foto realista nos cenarios indicados abaixo; ilustracao (desenho) no resto",
}
# Os que a IA pode escolher post a post (o "misto" e uma regra da conta, nao um
# estilo de post).
ESCOLHIVEIS = ["foto", "ilustracao", "quadrinhos", "apresentacao"]
NOMES = {
    "foto": "foto realista",
    "ilustracao": "ilustracao",
    "quadrinhos": "quadrinhos",
    "apresentacao": "apresentacao (slide)",
}
QUANDO = {
    "foto": "ambiente, equipamento, resultado real, confianca",
    "ilustracao": "explicar uma ideia abstrata ou um processo, leve",
    "quadrinhos": "contar uma situacao do dia a dia com personagens, com leveza",
    "apresentacao": "dado, lista, comparacao, passo a passo para salvar",
}


def estilos_da_conta(destino) -> list[str]:
    """Os estilos que a IA pode escolher nesta conta (os permitidos; nenhum
    marcado: todos), o preferido primeiro."""
    permitidos = [e for e in (destino.estilos_permitidos or []) if e in ESCOLHIVEIS]
    lista = permitidos or list(ESCOLHIVEIS)
    preferido = destino.estilo_das_imagens
    if preferido == "misto":
        preferido = "foto" if "foto" in lista else ""
    if preferido in lista:
        lista.remove(preferido)
        lista.insert(0, preferido)
    return lista


def estilo_do_post(post) -> str:
    """O estilo das imagens deste post: o que a IA escolheu (se a conta
    permite) ou o preferido da conta ("" = so cores e texto, sem cena)."""
    escolhido = (post.extras or {}).get("estilo") or ""
    if escolhido in estilos_da_conta(post.destino):
        return escolhido
    return post.destino.estilo_das_imagens


def descricao_do_estilo(destino, estilo: str, *, para: str = "imagem") -> str:
    """O estilo e as orientacoes da conta, numa frase, para um prompt.
    `para`: "imagem" (as orientacoes so das imagens) ou "video"."""
    partes = [ESTILOS.get(estilo, "")]
    if estilo in ("foto", "misto") and destino.cenarios_de_foto.strip():
        partes.append(f"foto realista so quando: {destino.cenarios_de_foto.strip()}")
    partes.append(destino.descricao_do_estilo.strip())
    especifica = destino.orientacao_videos if para == "video" else destino.orientacao_imagens
    partes.append(especifica.strip())
    return ". ".join(p.rstrip(". ") for p in partes if p)


def resumo_dos_estilos(destino) -> str:
    """Os estilos que a conta aceita e as orientacoes, para quem propoe posts
    (a escolha final e de quem escreve cada um)."""
    lista = estilos_da_conta(destino)
    texto = "estilos de imagem permitidos: " + "; ".join(
        f"'{e}' ({NOMES[e]}: bom para {QUANDO[e]})" for e in lista
    )
    for rotulo, valor in (
        ("orientacoes gerais", destino.descricao_do_estilo),
        ("so nas imagens", destino.orientacao_imagens),
        ("so nos videos", destino.orientacao_videos),
    ):
        if valor.strip():
            texto += f"; {rotulo}: {valor.strip()}"
    return texto


def instrucao_de_estilo(destino, post=None) -> str:
    """Para quem escreve o post: o estilo de imagem a escolher, entre os da
    conta, e por que (vai em 'estilo' e 'por_que_estilo'). Se a proposta ja
    pediu um estilo, e ele."""
    lista = estilos_da_conta(destino)
    gerais = destino.descricao_do_estilo.strip()
    imagens = destino.orientacao_imagens.strip()
    pedido = ((post.extras or {}).get("estilo_pedido") or "") if post is not None else ""
    if pedido in lista:
        lista = [pedido]
    if len(lista) == 1:
        texto = f"Estilo das imagens deste post: {ESTILOS[lista[0]]}."
    else:
        opcoes = "; ".join(f"'{e}' ({NOMES[e]}: bom para {QUANDO[e]})" for e in lista)
        texto = (
            "ESTILO DAS IMAGENS: escolha UM para este post, o que melhor mostra a ideia, e "
            f"devolva em 'estilo' (a chave) e 'por_que_estilo' (uma frase). Opcoes: {opcoes}."
        )
    if gerais:
        texto += f" Orientacoes da conta: {gerais}."
    if imagens:
        texto += f" Nas imagens: {imagens}."
    return texto


PEDIDO = """\
Gere {quantas} imagem(ns) para um carrossel do Instagram: uma por lamina, na
ordem, no formato vertical 4:5 (1080 x 1350 px).

Regras para TODAS:
- NAO escreva nenhum texto, letra, numero ou logotipo na imagem: o texto entra
  depois, por cima. {espaco}
- Estilo (o mesmo em todas, para o perfil ter uma linha): {estilo}.
- Cores da marca: fundo {fundo}, destaque {destaque}. Use como base da paleta.
- Nada de pessoas reais reconheciveis, marcas de terceiros ou imagem chocante.
{regras}{elementos}
Assunto do post: {assunto}

"""


def elementos(destino) -> list:
    return list(destino.elementos.filter(ativo=True))


def ficha(destino) -> str:
    """Os personagens, cenarios e objetos fixos da conta, um por linha."""
    return "\n".join(
        f"- {e.nome} ({e.get_tipo_display().lower()}): {e.descricao.strip()}"
        for e in elementos(destino)
    )


def expandir(cena: str, lista: list) -> str:
    """A cena com a descricao inteira de cada elemento fixo chamado pelo nome
    (para o gerador de imagem, que nao sabe quem e o "Gugu")."""
    from apps.social.iscas import normalizar

    alvo = f" {normalizar(cena)} "
    citados = [e for e in lista if f" {normalizar(e.nome)} " in alvo]
    return cena + "".join(f"; {e.nome}: {e.descricao.strip()}" for e in citados)


def _laminas(post) -> list[dict]:
    extras = post.extras or {}
    chamada = post.destino.chamada_final or laminas.CHAMADA
    return laminas.com_a_chamada(extras.get("laminas") or [], chamada)


def situacao_das_laminas(post) -> list[dict]:
    """Cada lamina e o fundo dela agora: [{"n", "titulo", "texto", "fundo"}],
    fundo = "enviado", "padrao: <nome>" ou "" (so a cor da conta)."""
    itens = _laminas(post)
    extras = post.extras or {}
    enviados = extras.get("fundos") or {}
    modelos = list(post.destino.fundos.all())
    saida = []
    for n, item in enumerate(itens, start=1):
        if (n == 1 and extras.get("imagem_propria")) or str(n) in enviados:
            fundo = "enviado"
        elif modelo := laminas.escolher_fundo(modelos, n, len(itens), item):
            fundo = f"padrao: {modelo}"
        else:
            fundo = ""
        prompts = (extras.get("prompts") or {}).get(str(n)) or {}
        saida.append(
            {
                "n": n,
                "titulo": item.get("titulo", ""),
                "texto": item.get("texto", ""),
                "visual": item.get("visual", ""),
                "movimento": item.get("movimento", ""),
                "fundo": fundo,
                "prompt_imagem": prompts.get("imagem", ""),
                "prompt_movimento": prompts.get("movimento", ""),
                "opcoes": len((extras.get("opcoes") or {}).get(str(n)) or []),
                "clipe": str(n) in (extras.get("clipes") or {}),
            }
        )
    return saida


def _ficha_do_pedido(destino) -> str:
    lista = elementos(destino)
    if not lista:
        return ""
    com_imagem = [e.nome for e in lista if e.imagem]
    return (
        "- Personagens e cenarios FIXOS da conta (desenhe sempre igual a descricao; quando "
        "a lamina chamar um pelo nome, e ele):\n"
        + ficha(destino)
        + "\n"
        + (
            f"- Se voce aceita imagem de referencia, vou anexar as de: {', '.join(com_imagem)}"
            " — siga-as.\n"
            if com_imagem
            else ""
        )
    )


def pedido(post) -> str:
    """O texto para colar na IA de imagem: as laminas sem fundo padrao."""
    from apps.social.models import ConfiguracaoSocial

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    estado = situacao_das_laminas(post)
    faltam = [x for x in estado if not x["fundo"].startswith("padrao")]
    estilo = descricao_do_estilo(destino, estilo_do_post(post)) or (
        "o que combinar com o assunto, sobrio"
    )
    regras = ConfiguracaoSocial.carregar().regras.strip()
    texto = PEDIDO.format(
        quantas=len(faltam),
        espaco=(
            "Deixe a metade de baixo da capa e o centro das outras com pouco detalhe, "
            "para o texto ser lido."
        ),
        estilo=estilo,
        fundo=cores["fundo"],
        destaque=cores["destaque"],
        regras=f"- Regras do negocio: {regras}\n" if regras else "",
        elementos=_ficha_do_pedido(destino),
        assunto=post.artigo_titulo or post.gancho or "(post proprio)",
    )
    for x in faltam:
        papel = "capa" if x["n"] == 1 else "ultima" if x["n"] == len(estado) else "meio"
        sobre = " — ".join(p for p in (x["titulo"], x["texto"]) if p)
        if x["visual"]:
            texto += f"Lamina {x['n']} ({papel}): {x['visual']} (a lamina diz: {sobre})\n"
        else:
            texto += f"Lamina {x['n']} ({papel}): uma cena que mostre a ideia de: {sobre}\n"
    padrao = [str(x["n"]) for x in estado if x["fundo"].startswith("padrao")]
    if padrao:
        texto += f"\n(As laminas {', '.join(padrao)} usam o fundo padrao da conta: nao gere.)\n"
    return texto


# -- Prompts de cada lamina (imagem e animacao) -------------------------------------------
# A cena (em portugues, escrita por quem escreveu o post) vira dois prompts em
# ingles: o da IMAGEM (para o gerador do PubliBot ou qualquer outro: Midjourney,
# Stable Diffusion, Ideogram...) e o do MOVIMENTO (para animar a imagem: o worker,
# Sora, Runway, Kling, Veo...). Ficam guardados no post, por lamina, e aparecem
# para copiar; so sao refeitos quando a cena, o estilo ou o movimento mudam.
ESQUEMA_DA_CENA = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}, "movimento": {"type": "string"}},
    "required": ["prompt"],
}

PROMPT_DA_CENA = {
    "descricao": "Transforma a cena de uma lamina nos prompts de imagem e de animacao.",
    "variaveis": ["estilo", "cena", "cores", "movimento", "estilo_video"],
    "temperatura": 0.3,
    "sistema": (
        "You write prompts for image generators (Stable Diffusion, Midjourney family) and "
        "for image-to-video models. Given a scene described in Portuguese and the visual "
        "style of a brand, write:\n"
        "- 'prompt': ONE English image prompt, 30 to 60 words: subject, action, setting, "
        "composition (vertical 4:5, calm empty area in the lower half or center for "
        "overlaid text), lighting, style and palette. When a fixed character or setting is "
        "described, repeat its full visual description every time, word for word, so "
        "different generators draw it the same way.\n"
        "- 'movimento': ONE English prompt to animate that same image, 15 to 35 words: what "
        "moves (subtle, slow), the camera (static or a slow push-in), no cuts, no new "
        "characters, the characters keep exactly the same look.\n"
        "Concrete nouns, no abstract ideas. NEVER ask for text, letters, numbers, logos or "
        "recognizable real people.\n\n"
        'Answer ONLY with JSON: {"prompt": "...", "movimento": "..."}'
    ),
    "usuario": (
        "Style: {estilo}\nBrand colors: {cores}\nScene (Portuguese): {cena}\n"
        "Motion wanted (Portuguese, may be empty): {movimento}\n"
        "Video guidelines (Portuguese, may be empty): {estilo_video}"
    ),
}


def _cena(item: dict) -> str:
    sobre = " — ".join(p for p in (item.get("titulo", ""), item.get("texto", "")) if p)
    return item.get("visual") or f"uma cena concreta que mostre: {sobre}"


def _pedido_da_cena(post, item: dict) -> dict:
    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    estilo = estilo_do_post(post)
    return {
        "estilo": descricao_do_estilo(destino, estilo) or "clean modern editorial illustration",
        "cena": expandir(_cena(item), elementos(destino)),
        "cores": f"background {cores['fundo']}, accent {cores['destaque']}",
        "movimento": item.get("movimento", ""),
        "estilo_video": descricao_do_estilo(destino, "", para="video"),
    }


def prompts_da_lamina(post, n: int, *, refazer: bool = False) -> dict:
    """{"imagem", "movimento"} da lamina n, em ingles. Guardados no post; o
    modelo so e chamado quando a cena, o estilo ou o movimento mudaram."""
    import json
    import re

    from apps.social import fontes

    itens = _laminas(post)
    if not 1 <= n <= len(itens):
        raise ValueError(f"o post nao tem a lamina {n}")
    pedido = _pedido_da_cena(post, itens[n - 1])
    assinatura = json.dumps(pedido, sort_keys=True)
    guardados = ((post.extras or {}).get("prompts") or {}).get(str(n)) or {}
    if guardados.get("de") == assinatura and not refazer:
        return guardados
    bruto = fontes.executar("social_cena", pedido, json_schema=ESQUEMA_DA_CENA)
    dados = json.loads(re.sub(r"^```\w*\n|```$", "", bruto.strip()).strip())
    novos = {
        "imagem": str(dados.get("prompt") or "").strip(),
        "movimento": str(dados.get("movimento") or "").strip(),
        "de": assinatura,
    }
    extras = dict(post.extras or {})
    extras["prompts"] = {**(extras.get("prompts") or {}), str(n): novos}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    return novos


def usar_imagem(post, n: int, conteudo: bytes) -> str:
    """Grava a imagem como fundo da lamina n (a 1a vira a foto da capa)."""
    import io

    caminho = laminas.guardar_imagem_propria(
        post, io.BytesIO(conteudo), "propria" if n == 1 else f"fundo-{n}"
    )
    extras = dict(post.extras or {})
    if n == 1:
        extras["imagem_propria"] = caminho
        extras.pop("ajuste_da_capa", None)
    else:
        extras["fundos"] = {**(extras.get("fundos") or {}), str(n): caminho}
    opcoes = dict(extras.get("opcoes") or {})
    opcoes.pop(str(n), None)
    extras["opcoes"] = opcoes
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    return caminho


OPCOES_POR_LAMINA = 3


def gerar_imagens(post, *, apenas: int | None = None, opcoes: int = 1) -> int:
    """Gera, com o gerador do PubliBot, a imagem de cada lamina que ainda nao
    tem imagem enviada nem fundo padrao (ou so a lamina `apenas`, mesmo que ja
    tenha: e o "refazer so esta"). Com `opcoes` > 1, guarda as opcoes para a
    pessoa escolher (`escolher_opcao`) em vez de trocar direto. Devolve quantas
    laminas gerou. Gerador ocupado: levanta (a tarefa tenta mais tarde) e o
    que ja saiu fica."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from apps.social import fontes

    feitas = 0
    for x in situacao_das_laminas(post):
        if apenas is not None and x["n"] != apenas:
            continue
        if apenas is None and x["fundo"]:
            continue
        prompt = prompts_da_lamina(post, x["n"])["imagem"]
        if opcoes <= 1:
            usar_imagem(post, x["n"], fontes.gerar_imagem(prompt))
        else:
            caminhos = []
            for i, conteudo in enumerate(fontes.gerar_imagens(prompt, quantidade=opcoes)):
                nome = f"social/{post.pk}/opcao-{x['n']}-{i}.png"
                if default_storage.exists(nome):
                    default_storage.delete(nome)
                caminhos.append(default_storage.save(nome, ContentFile(conteudo)))
            extras = dict(post.extras or {})
            extras["opcoes"] = {**(extras.get("opcoes") or {}), str(x["n"]): caminhos}
            post.extras = extras
            post.save(update_fields=["extras", "atualizado_em"])
        feitas += 1
    return feitas


def opcoes_da_lamina(post, n: int) -> list[str]:
    from django.core.files.storage import default_storage

    lista = ((post.extras or {}).get("opcoes") or {}).get(str(n)) or []
    return [c for c in lista if default_storage.exists(c)]


def escolher_opcao(post, n: int, i: int) -> bool:
    """A opcao i da lamina n vira o fundo dela (as outras saem)."""
    from django.core.files.storage import default_storage

    lista = opcoes_da_lamina(post, n)
    if not 0 <= i < len(lista):
        return False
    with default_storage.open(lista[i], "rb") as arquivo:
        usar_imagem(post, n, arquivo.read())
    for caminho in lista:
        default_storage.delete(caminho)
    return True


# -- Animacao (o fundo da lamina em movimento, para o video) --------------------------------
PEDIDO_DE_ANIMACAO = """Anime cada imagem abaixo (uma por lamina, na ordem) para um video curto do
Instagram (Reels). Eu envio a imagem de cada lamina; voce devolve um clipe de
cada uma.

Regras para TODOS os clipes:
- {segundos} segundos, vertical 4:5 ou 9:16, sem cortes, comecando exatamente
  na imagem enviada.
- Movimento lento e sutil; camera parada ou aproximando devagar.
- NAO acrescente texto, letras, legendas ou logotipo: o texto entra depois, por cima.
- Os personagens continuam IGUAIS a imagem (rosto, roupa, cores) do comeco ao fim.
{orientacoes}{elementos}
"""


def pedido_de_animacao(post) -> str:
    """O texto para colar numa IA de video (Sora, Runway, Kling, Veo, Pika...)
    junto com a imagem de cada lamina."""
    destino = post.destino
    orientacoes = descricao_do_estilo(destino, "", para="video")
    texto = PEDIDO_DE_ANIMACAO.format(
        segundos=5,
        orientacoes=f"- Orientacoes da conta: {orientacoes}\n" if orientacoes else "",
        elementos=(
            "- Personagens e cenarios fixos (mantenha exatamente assim):\n" + ficha(destino) + "\n"
            if elementos(destino)
            else ""
        ),
    )
    for n, item in enumerate(_laminas(post), start=1):
        guardado = (((post.extras or {}).get("prompts") or {}).get(str(n)) or {}).get("movimento")
        movimento = item.get("movimento") or guardado or "movimento sutil da cena, camera parada"
        texto += f"\nLamina {n} (envio a imagem {n}): {movimento}"
        if guardado and guardado != movimento:
            texto += f"\n   (em ingles, se preferir: {guardado})"
    return texto + "\n"
