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
{regras}
Assunto do post: {assunto}

"""


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
                "fundo": fundo,
            }
        )
    return saida


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
        assunto=post.artigo_titulo or post.gancho or "(post proprio)",
    )
    for x in faltam:
        papel = "capa" if x["n"] == 1 else "ultima" if x["n"] == len(estado) else "meio"
        sobre = " — ".join(p for p in (x["titulo"], x["texto"]) if p)
        texto += f"Lamina {x['n']} ({papel}): uma cena que mostre a ideia de: {sobre}\n"
    padrao = [str(x["n"]) for x in estado if x["fundo"].startswith("padrao")]
    if padrao:
        texto += f"\n(As laminas {', '.join(padrao)} usam o fundo padrao da conta: nao gere.)\n"
    return texto
