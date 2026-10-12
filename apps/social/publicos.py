"""Publico do impulso: os publicos salvos, o resultado de cada um e o pedido
para outra IA sugerir o publico de um post.

A lista de interesses da Meta (o "direcionamento detalhado") so sai pela API de
anuncios com a permissao ads_management, que exige revisao do app pela Meta —
e mexer nela pela API seria criar anuncio, o que o PubliBot nao faz. Por isso:

* o PubliBot guarda os publicos que a pessoa usou (ou que a outra IA sugeriu),
  e cada post impulsionado diz qual usou;
* o resultado soma por publico (gasto, cliques, alcance — do anuncio ligado ao
  post, ou o valor digitado), e o melhor de cada foco volta como sugestao;
* para um publico novo, o pedido leva o negocio, as regioes, o post, o foco e
  os publicos ja usados com o resultado; a resposta colada vira publico salvo.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from apps.social import focos, fontes
from apps.social.models import Destino, Post, PublicoDeImpulso
from core.resposta_ia import itens, ler_blocos

ROTULOS = ["PUBLICOS", "COMENTARIOS"]
MAXIMO_DE_PUBLICOS = 3

PEDIDO = """\
Voce monta publicos de impulso pago no Gerenciador de Anuncios da Meta
(Instagram e Facebook). Quero impulsionar o post abaixo e preciso marcar o
publico: local, idade e interesses do "direcionamento detalhado".

Regras:
- Use so interesses que EXISTEM na lista da Meta, com o nome como aparece la.
  Se voce puder pesquisar na web, confira; se nao tiver certeza, diga.
- Desde 2022 a Meta nao aceita interesses sobre saude, condicao medica,
  religiao, politica e outros temas sensiveis: nao sugira esses. Use estilo
  de vida, comportamentos, profissoes e locais.
- Diga se vale deixar o "Publico Advantage+" (a Meta amplia sozinha) ligado.
- Se um publico ja usado (abaixo) serve, repita o NOME EXATO dele.
- Para o foco "alcance", publico amplo; para "site", mais estreito e perto de
  quem compraria.

Converse antes se faltar algo. Quando estiver pronto, responda SO nos blocos
e termine com FIM:

PUBLICOS:
- nome curto | local | idade | interesses (separados por ;) | por que, em ate 15 palavras
  (ate {maximo}, o melhor primeiro)
COMENTARIOS:
curto: o que mais marcar (genero, posicionamento, Advantage+) e o valor/dias.
FIM
"""


def _por_mil(gasto, alcance) -> Decimal | None:
    return round(Decimal(gasto) * 1000 / alcance, 2) if gasto and alcance else None


def resultado(publico: PublicoDeImpulso) -> dict:
    """O que o publico rendeu nos posts impulsionados com ele."""
    posts = list(publico.posts.filter(impulsionado=True))
    anuncios = publico.posts.filter(impulsionado=True, anuncios__isnull=False).aggregate(
        gasto=Sum("anuncios__gasto"),
        cliques=Sum("anuncios__cliques"),
        alcance=Sum("anuncios__alcance"),
    )
    gasto = anuncios["gasto"] or sum((p.custo_impulso or 0) for p in posts)
    cliques = anuncios["cliques"] or 0
    alcance = anuncios["alcance"] or 0
    return {
        "posts": len(posts),
        "focos": sorted({f for p in posts if (f := focos.do_post(p))}),
        "gasto": gasto or None,
        "cliques": cliques,
        "alcance": alcance,
        "custo_por_clique": round(gasto / cliques, 2) if gasto and cliques else None,
        "custo_por_mil": _por_mil(gasto, alcance),
    }


def quadro(destino: Destino) -> list[dict]:
    return [
        {"publico": p, **resultado(p)} for p in destino.publicos.filter(ativo=True).order_by("nome")
    ]


def melhor(destino: Destino, foco: str = "") -> dict | None:
    """O publico salvo que rendeu mais barato no foco (alcance: custo por mil
    pessoas; os outros: custo por clique). None sem resultado medido."""
    criterio = "custo_por_mil" if foco == "alcance" else "custo_por_clique"
    medidos = [
        linha
        for linha in quadro(destino)
        if linha[criterio] is not None and (not foco or foco in linha["focos"])
    ]
    return min(medidos, key=lambda linha: linha[criterio], default=None)


def _resumo(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def pedido(post: Post) -> str:
    from apps.social.estrategia import FASES, fase

    destino = post.destino
    negocio = fontes.negocio()
    foco = focos.do_post(post)
    partes = [
        PEDIDO.format(maximo=MAXIMO_DE_PUBLICOS),
        "## Negocio",
        f"Tema: {negocio.get('tema') or '(nao informado)'}",
        f"Publico: {negocio.get('publico') or '(nao informado)'}",
        f"Oferta: {negocio.get('oferta') or '(nao informada)'}",
        "Dores do publico: " + ("; ".join(negocio.get("dores") or []) or "(nenhuma)"),
        "Onde atende: " + (", ".join(negocio.get("regioes") or []) or "(nao informado)"),
        "",
        f"## A conta: {destino.nome}",
        f"Publico desta conta: {destino.publico or '(nao informado)'}",
        f"Fase: {FASES[fase(destino)]['nome']}; seguidores: {destino.seguidores or '?'}",
        "",
        "## O post",
    ]
    if foco:
        info = focos.FOCOS[foco]
        partes.append(f"Foco: {info['nome']} — objetivo do impulso: {info['impulso']}")
    partes.append(f"Artigo: {post.artigo_titulo or '(post proprio)'}")
    if post.gancho:
        partes.append(f"Gancho: {post.gancho}")
    partes.append(f"Texto: {_resumo(post.texto, 1200)}")
    for n, lamina in enumerate((post.extras or {}).get("laminas") or [], start=1):
        partes.append(
            f"Lamina {n}: {_resumo(lamina.get('titulo', ''), 120)} — "
            f"{_resumo(lamina.get('texto', ''), 200)}"
        )

    partes += ["", "## Publicos ja usados nesta conta e o resultado"]
    linhas = quadro(destino)
    for linha in linhas:
        p = linha["publico"]
        numeros = (
            f"{linha['posts']} impulso(s)"
            + (f", focos {', '.join(linha['focos'])}" if linha["focos"] else "")
            + (f", R$ {linha['custo_por_clique']} por clique" if linha["custo_por_clique"] else "")
            + (f", R$ {linha['custo_por_mil']} por mil pessoas" if linha["custo_por_mil"] else "")
        )
        partes.append(f"- {p.nome}: {_resumo(p.descricao, 300)} [{numeros}]")
    if not linhas:
        partes.append("(nenhum ainda)")
    return "\n".join(partes).strip() + "\n"


def _partes(linha: str) -> list[str]:
    return [p.strip().strip("`*_[]") for p in linha.split("|")]


def ler(resposta: str) -> tuple[list[dict], str]:
    """([{nome, descricao, por_que}], comentarios) da resposta colada."""
    blocos = ler_blocos(resposta, ROTULOS)
    lidos, vistos = [], set()
    for linha in itens(blocos.get("PUBLICOS", "")):
        partes = _partes(linha)
        if len(partes) < 2:  # continuacao de linha, nao publico
            continue
        nome = partes[0][:80]
        if not nome or nome.lower() in vistos:
            continue
        vistos.add(nome.lower())
        rotulos = ("Local", "Idade", "Interesses")
        descricao = "\n".join(
            f"{rotulo}: {valor}"
            for rotulo, valor in zip(rotulos, partes[1:4], strict=False)
            if valor
        )
        lidos.append(
            {
                "nome": nome,
                "descricao": descricao,
                "por_que": (partes[4] if len(partes) > 4 else "")[:300],
            }
        )
    return lidos[:MAXIMO_DE_PUBLICOS], blocos.get("COMENTARIOS", "").strip()


def salvar(destino: Destino, lidos: list[dict]) -> list[PublicoDeImpulso]:
    """Cria os publicos novos; o que ja existe com o mesmo nome e reaproveitado
    (sem trocar o que a pessoa escreveu)."""
    existentes = {p.nome.lower(): p for p in destino.publicos.all()}
    salvos = []
    for lido in lidos:
        publico = existentes.get(lido["nome"].lower())
        if publico is None:
            publico = PublicoDeImpulso.objects.create(
                destino=destino,
                nome=lido["nome"],
                descricao=lido["descricao"],
                por_que=lido["por_que"],
                origem=PublicoDeImpulso.Origem.OUTRA_IA,
            )
        elif not publico.ativo:
            publico.ativo = True
            publico.save(update_fields=["ativo"])
        salvos.append(publico)
    return salvos
