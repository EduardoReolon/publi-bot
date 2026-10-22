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


# -- Criar o personagem (ou cenario) numa IA de imagem --------------------------------------
# A "folha de personagem" (o mesmo personagem de varios lados e com varias
# expressoes, numa imagem so) e o que faz IAs diferentes desenharem igual: ela
# vira a imagem de referencia do elemento, e a descricao vai em todo pedido.
TRACOS = {
    "2d": {
        "nome": "Animacao 2D (traco simples)",
        "estilo": (
            "ilustracao 2D vetorial plana, traco limpo com contorno fino, cores chapadas com "
            "sombra simples, poucos detalhes, proporcoes levemente estilizadas"
        ),
        "quando": (
            "o mais facil de manter igual de um post para o outro e de animar: ate IA menor "
            "e mais barata (e a placa propria) da conta. Combina com quadrinhos e apresentacao."
        ),
    },
    "3d": {
        "nome": "3D estilo filme de animacao",
        "estilo": (
            "personagem 3D estilo filme de animacao, formas arredondadas, texturas suaves, "
            "iluminacao de estudio suave"
        ),
        "quando": (
            "simpatico e caprichado, mas precisa de IA boa (ChatGPT, Midjourney, Gemini) para "
            "sair igual sempre; animar pede IA de video maior (Sora, Kling) — mais caro."
        ),
    },
    "realista": {
        "nome": "Realista (foto)",
        "estilo": "fotografia realista, luz natural, pessoa comum (nao famosa), nitida",
        "quando": (
            "o mais dificil de manter o mesmo rosto entre imagens e de animar sem estranhar; "
            "so as melhores IAs dao conta, e e o mais caro. Para mascote, evite."
        ),
    },
}
FOLHA = {
    "personagem": (
        "Crie a FOLHA DE PERSONAGEM de {nome}: {descricao}. Estilo: {estilo}. Na mesma "
        "imagem, em fundo branco liso, o MESMO personagem 4 vezes lado a lado — de frente, de "
        "3/4, de perfil e de costas — e, embaixo, 3 expressoes do rosto (neutro, sorrindo, "
        "preocupado). Mesma roupa, mesmas cores e proporcoes em todas. Sem texto, sem "
        "logotipo. Formato horizontal 16:9."
    ),
    "cenario": (
        "Crie a FOLHA DO CENARIO {nome}: {descricao}. Estilo: {estilo}. Na mesma imagem, o "
        "MESMO lugar de 3 angulos (de frente, de lado, de cima), sem pessoas, mesma luz e "
        "mesmas cores. Sem texto, sem logotipo. Formato horizontal 16:9."
    ),
    "objeto": (
        "Crie a FOLHA DO OBJETO {nome}: {descricao}. Estilo: {estilo}. Na mesma imagem, o "
        "MESMO objeto de 4 lados em fundo branco liso, mesmas cores e proporcoes. Sem texto, "
        "sem logotipo. Formato horizontal 16:9."
    ),
}
EXEMPLO_DE_PERSONAGEM = (
    "[descreva sempre igual: quem e, idade, cabelo, roupa e cores, acessorios — ex.: dono de "
    "empresa, uns 55 anos, grisalho, oculos de armacao preta, camisa social azul-clara de "
    "mangas dobradas, calca azul-marinho]"
)


REFERENCIA_LOCAL = (
    "Anexe a folha de referencia de {nome}. Desenhe {nome} ({descricao}) UMA vez so: "
    "corpo inteiro, de frente, em pe, bracos relaxados ao lado do corpo, expressao neutra, "
    "centralizado e ocupando a maior parte da altura, NO MESMO traco, cores e roupa da folha. "
    "Fundo cinza claro liso (#d9d9d9), sem chao, sem sombra, sem outros objetos, sem texto. "
    "Formato quadrado 1:1."
)


def prompt_da_referencia_local(nome: str, descricao: str) -> str:
    """O pedido da referencia para o gerador local (ver ElementoVisual.referencia_local)."""
    return REFERENCIA_LOCAL.format(
        nome=nome or "[nome]", descricao=descricao.strip().rstrip(".") or "[descricao]"
    )


def prompts_de_folha(nome: str = "", descricao: str = "", tipo: str = "personagem") -> list:
    """O pedido da folha de referencia em cada traco: [{"chave", "nome", "quando",
    "prompt"}]. Sem nome e descricao, com o modelo para preencher."""
    modelo = FOLHA.get(tipo, FOLHA["personagem"])
    return [
        {
            "chave": chave,
            "nome": traco["nome"],
            "quando": traco["quando"],
            "prompt": modelo.format(
                nome=nome or "[nome]",
                descricao=descricao.strip().rstrip(".") or EXEMPLO_DE_PERSONAGEM,
                estilo=traco["estilo"],
            ),
        }
        for chave, traco in TRACOS.items()
    ]


def elementos(destino) -> list:
    return list(destino.elementos.filter(ativo=True))


def ficha(destino) -> str:
    """Os personagens, cenarios, objetos e lugares reais da conta, um por linha."""
    return "\n".join(
        f"- {e.nome} ({e.get_tipo_display().lower()}): {e.descricao.strip()}"
        for e in elementos(destino)
    )


def citados(cena: str, lista: list) -> list:
    """Os elementos fixos chamados pelo nome na cena."""
    from apps.social.iscas import normalizar

    alvo = f" {normalizar(cena)} "
    return [e for e in lista if f" {normalizar(e.nome)} " in alvo]


def expandir(cena: str, lista: list) -> str:
    """A cena com a descricao inteira de cada elemento fixo chamado pelo nome
    (para o gerador de imagem, que nao sabe quem e o "Gugu")."""
    return cena + "".join(f"; {e.nome}: {e.descricao.strip()}" for e in citados(cena, lista))


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


# O primeiro quadro de uma cena do video (cenas.py): o gerador de imagem faz o
# quadro e o worker o anima. Diferente da lamina, nao ha texto por cima nem
# espaco para ele, e o quadro e o COMECO do gesto (o movimento parte dele).
PROMPT_DO_QUADRO = {
    "descricao": "Transforma a cena do video no prompt do primeiro quadro e no do movimento.",
    "variaveis": ["estilo", "cena", "cores", "movimento", "estilo_video"],
    "temperatura": 0.3,
    "sistema": (
        "You write prompts for an image generator and for an image-to-video model that "
        "animates the image. Given a video scene described in Portuguese and the visual "
        "style of a brand, write:\n"
        "- 'prompt': ONE English image prompt, 30 to 60 words, for the FIRST FRAME of the "
        "scene: vertical 9:16 full frame, who is there, where, the pose and expression at "
        "the START of the gesture, composition with the main subject large and centered, "
        "lighting, style and palette. When a fixed character or place is described, repeat "
        "its full visual description every time, word for word. If the scene has a window "
        "showing a real place, describe just a plain window (the photo is inserted there).\n"
        "- 'movimento': ONE English prompt for the animation, 15 to 35 words: the single "
        "slow gesture of the scene, the camera (static or a slow push-in), no cuts, no new "
        "characters, the characters keep exactly the same look, mouths closed (the voice "
        "is a narrator, nobody talks on screen).\n"
        "Concrete nouns, no abstract ideas. NEVER ask for text, letters, numbers, logos or "
        "recognizable real people.\n\n"
        'Answer ONLY with JSON: {"prompt": "...", "movimento": "..."}'
    ),
    "usuario": (
        "Style: {estilo}\nBrand colors: {cores}\nScene (Portuguese): {cena}\n"
        "Motion wanted (Portuguese): {movimento}\n"
        "Video guidelines (Portuguese, may be empty): {estilo_video}"
    ),
}


def _cena(item: dict) -> str:
    sobre = " — ".join(p for p in (item.get("titulo", ""), item.get("texto", "")) if p)
    return item.get("visual") or f"uma cena concreta que mostre: {sobre}"


def _pedido_da_cena(post, item: dict) -> dict:
    from apps.social.models import ConfiguracaoSocial

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    estilo = descricao_do_estilo(destino, estilo_do_post(post))
    regras = ConfiguracaoSocial.carregar().regras.strip()
    if regras:
        # As regras do negocio valem tambem para a imagem feita aqui.
        estilo = f"{estilo}. Regras do negocio: {regras}" if estilo else f"Regras: {regras}"
    return {
        "estilo": estilo or "clean modern editorial illustration",
        "cena": expandir(_cena(item), elementos(destino)),
        "cores": f"background {cores['fundo']}, accent {cores['destaque']}",
        "movimento": item.get("movimento", ""),
        "estilo_video": descricao_do_estilo(destino, "", para="video"),
    }


def prompts_da_lamina(post, n: int, *, refazer: bool = False) -> dict:
    """{"imagem", "movimento"} da lamina n, em ingles. Guardados no post; o
    modelo so e chamado quando a cena, o estilo ou o movimento mudaram."""
    itens = _laminas(post)
    if not 1 <= n <= len(itens):
        raise ValueError(f"o post nao tem a lamina {n}")
    return prompts_da_cena(post, itens[n - 1], chave=str(n), refazer=refazer)


def prompts_da_cena(
    post, item: dict, *, chave: str, refazer: bool = False, prompt: str = "social_cena"
) -> dict:
    """{"imagem", "movimento"} em ingles de uma cena (`item`: visual e
    movimento) do post: a lamina `chave`, a "foto" ou uma cena do video
    (`prompt` "social_quadro"). Guardados no post; o modelo so e chamado quando
    a cena, o estilo ou o movimento mudaram."""
    import json
    import re

    from apps.social import fontes

    pedido = _pedido_da_cena(post, item)
    assinatura = json.dumps({**pedido, "_prompt": prompt}, sort_keys=True)
    guardados = ((post.extras or {}).get("prompts") or {}).get(chave) or {}
    if guardados.get("de") == assinatura and not refazer:
        return guardados
    bruto = fontes.executar(prompt, pedido, json_schema=ESQUEMA_DA_CENA)
    dados = json.loads(re.sub(r"^```\w*\n|```$", "", bruto.strip()).strip())
    novos = {
        "imagem": str(dados.get("prompt") or "").strip(),
        "movimento": str(dados.get("movimento") or "").strip(),
        "de": assinatura,
    }
    extras = dict(post.extras or {})
    extras["prompts"] = {**(extras.get("prompts") or {}), chave: novos}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    return novos


def usar_imagem(post, n: int, conteudo: bytes) -> str:
    """Grava a imagem como fundo da lamina n (a 1a vira a foto da capa; a 0 e a
    foto do post nas redes sem laminas)."""
    import io

    caminho = laminas.guardar_imagem_propria(
        post, io.BytesIO(conteudo), "propria" if n <= 1 else f"fundo-{n}"
    )
    extras = dict(post.extras or {})
    if n <= 1:
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

    if apenas == FOTO:
        return gerar_opcoes_da_foto(post, opcoes=opcoes)
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


# -- A foto do post (LinkedIn, Google: uma imagem, sem laminas) ---------------------------
# Tratada como a "lamina 0": as mesmas opcoes, a mesma escolha e o mesmo
# prompt da cena (com a identidade da conta e as regras do negocio).
FOTO = 0
FORMATO_DA_FOTO = {
    "linkedin": ("quadrado 1:1 (1080 x 1080 px)", "1024x1024"),
    "gmn": ("horizontal 4:3 (1200 x 900 px)", "1152x896"),
}


def cena_da_foto(post) -> str:
    sobre = post.gancho or post.artigo_titulo or (post.texto or "")[:200]
    return (post.extras or {}).get("visual_da_foto") or f"uma cena concreta que mostre: {sobre}"


def gerar_opcoes_da_foto(post, *, opcoes: int = OPCOES_POR_LAMINA) -> int:
    """Gera as opcoes da foto do post pelo gerador daqui, para escolher."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from apps.social import fontes

    prompt = prompts_da_cena(post, {"visual": cena_da_foto(post)}, chave="foto")["imagem"]
    _rotulo, tamanho = FORMATO_DA_FOTO.get(post.destino.rede, ("", "1024x1024"))
    caminhos = []
    geradas = fontes.gerar_imagens(prompt, quantidade=max(opcoes, 1), tamanho=tamanho)
    for i, conteudo in enumerate(geradas):
        nome = f"social/{post.pk}/opcao-foto-{i}.png"
        if default_storage.exists(nome):
            default_storage.delete(nome)
        caminhos.append(default_storage.save(nome, ContentFile(conteudo)))
    extras = dict(post.extras or {})
    extras["opcoes"] = {**(extras.get("opcoes") or {}), str(FOTO): caminhos}
    post.extras = extras
    post.save(update_fields=["extras", "atualizado_em"])
    return 1


PEDIDO_DE_FOTO = """\
Gere {quantas} opcoes de UMA imagem para um post do {rede}, no formato {formato}.

Regras:
- NAO escreva nenhum texto, letra, numero ou logotipo na imagem.
- Estilo: {estilo}.
- Cores da marca: fundo {fundo}, destaque {destaque}.
- Nada de pessoas reais reconheciveis, marcas de terceiros ou imagem chocante.
{regras}{elementos}
A cena: {cena}
"""


def pedido_de_foto(post, quantas: int = OPCOES_POR_LAMINA) -> str:
    """O texto para colar numa IA de imagem: a foto do post (redes sem laminas)."""
    from apps.social.models import ConfiguracaoSocial
    from apps.social.redes import rede

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    regras = ConfiguracaoSocial.carregar().regras.strip()
    return PEDIDO_DE_FOTO.format(
        quantas=quantas,
        rede=rede(destino.rede).nome,
        formato=FORMATO_DA_FOTO.get(destino.rede, ("quadrado 1:1", ""))[0],
        estilo=descricao_do_estilo(destino, estilo_do_post(post)) or "sobrio, editorial",
        fundo=cores["fundo"],
        destaque=cores["destaque"],
        regras=f"- Regras do negocio: {regras}\n" if regras else "",
        elementos=_ficha_do_pedido(destino),
        cena=expandir(cena_da_foto(post), elementos(destino)),
    )


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
