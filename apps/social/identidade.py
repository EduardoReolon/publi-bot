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
    "misto": "foto realista nos cenarios indicados abaixo; ilustracao (desenho) no resto",
}

PEDIDO = """\
Gere {quantas} imagem(ns) para um carrossel do Instagram: uma por lamina, na
ordem, no formato vertical 4:5 (1080 x 1350 px).

Regras para TODAS:
- NAO escreva nenhum texto, letra, numero ou logotipo na imagem: o texto entra
  depois, por cima. {espaco}
- Estilo da conta (o mesmo em todas, para o perfil ter uma linha): {estilo}.
{descricao}{cenarios}- Cores da marca: fundo {fundo}, destaque {destaque}. Use como base da paleta.
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
        saida.append(
            {
                "n": n,
                "titulo": item.get("titulo", ""),
                "texto": item.get("texto", ""),
                "visual": item.get("visual", ""),
                "fundo": fundo,
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
    estilo = ESTILOS.get(destino.estilo_das_imagens, "o que combinar com o assunto, sobrio")
    regras = ConfiguracaoSocial.carregar().regras.strip()
    texto = PEDIDO.format(
        quantas=len(faltam),
        espaco=(
            "Deixe a metade de baixo da capa e o centro das outras com pouco detalhe, "
            "para o texto ser lido."
        ),
        estilo=estilo,
        descricao=(
            f"- Como sao as imagens desta conta: {destino.descricao_do_estilo.strip()}\n"
            if destino.descricao_do_estilo.strip()
            else ""
        ),
        cenarios=(
            f"- Foto realista so quando: {destino.cenarios_de_foto.strip()}\n"
            if destino.estilo_das_imagens == "misto" and destino.cenarios_de_foto.strip()
            else ""
        ),
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


# -- Gerar aqui, com o gerador de imagem do PubliBot -------------------------------------
ESQUEMA_DA_CENA = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
}

PROMPT_DA_CENA = {
    "descricao": "Transforma a cena de uma lamina num prompt para o gerador de imagem.",
    "variaveis": ["estilo", "cena", "cores"],
    "temperatura": 0.3,
    "sistema": (
        "You write prompts for an image generator (Stable Diffusion family). Given a "
        "scene described in Portuguese and the visual style of a brand, write ONE English "
        "prompt, 30 to 60 words: subject, action, setting, composition (vertical 4:5, calm "
        "empty area in the {lugar} for overlaid text), lighting, style and palette. Concrete "
        "nouns, no abstract ideas. NEVER ask for text, letters, numbers, logos or "
        "recognizable real people.\n\n"
        'Answer ONLY with JSON: {"prompt": "..."}'
    ).replace("{lugar}", "lower half or center"),
    "usuario": "Style: {estilo}\nBrand colors: {cores}\nScene (Portuguese): {cena}",
}


def _cena(item: dict) -> str:
    sobre = " — ".join(p for p in (item.get("titulo", ""), item.get("texto", "")) if p)
    return item.get("visual") or f"uma cena concreta que mostre: {sobre}"


def gerar_imagens(post) -> int:
    """Gera, com o gerador do PubliBot, a imagem de cada lamina que ainda nao
    tem imagem enviada nem fundo padrao; grava como imagem da lamina e refaz o
    carrossel. Devolve quantas gerou. Gerador ocupado: levanta (a tarefa tenta
    mais tarde) e o que ja saiu fica."""
    import io
    import json
    import re

    from apps.social import fontes

    destino = post.destino
    cores = {**laminas.CORES, **{k: v for k, v in (destino.cores or {}).items() if v}}
    estilo = ESTILOS.get(destino.estilo_das_imagens, "clean modern editorial illustration")
    detalhe = destino.descricao_do_estilo.strip()
    itens = _laminas(post)
    fixos = elementos(destino)
    feitas = 0
    for x in situacao_das_laminas(post):
        if x["fundo"]:
            continue
        bruto = fontes.executar(
            "social_cena",
            {
                "estilo": f"{estilo}. {detalhe}".strip(". "),
                "cena": expandir(_cena(itens[x["n"] - 1]), fixos),
                "cores": f"background {cores['fundo']}, accent {cores['destaque']}",
            },
            json_schema=ESQUEMA_DA_CENA,
        )
        prompt = json.loads(re.sub(r"^```\w*\n|```$", "", bruto.strip()).strip())["prompt"]
        conteudo = fontes.gerar_imagem(prompt)
        caminho = laminas.guardar_imagem_propria(
            post, io.BytesIO(conteudo), "propria" if x["n"] == 1 else f"fundo-{x['n']}"
        )
        extras = dict(post.extras or {})
        if x["n"] == 1:
            extras["imagem_propria"] = caminho
            extras.pop("ajuste_da_capa", None)
        else:
            extras["fundos"] = {**(extras.get("fundos") or {}), str(x["n"]): caminho}
        post.extras = extras
        post.save(update_fields=["extras", "atualizado_em"])
        feitas += 1
    return feitas
