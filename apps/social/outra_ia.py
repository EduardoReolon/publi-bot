"""Segunda opiniao de uma IA grande: o pedido (copiar) e a volta (colar).

O modelo local escreve bem um post, mas JULGAR — qual tema postar agora, que
jeito de falar falta, o que esta acontecendo no mundo que conversa com os
artigos — ganha com um modelo grande, que entende mais e pode pesquisar na
web. Nada e enviado pelo PubliBot: a pessoa copia o pedido, conversa com a IA
que quiser e cola a resposta.

A volta e lida por algoritmo (`core.resposta_ia`), com previa:

* PROPOSTAS — temas ou artigos (pelo codigo) que viram post nesta conta, com o
  gancho sugerido como IDEIA para quem escreve (nao como texto pronto);
* ABORDAGENS — jeitos novos de falar, que entram no sorteio e competem com os
  outros pelo placar;
* EVENTOS — o que a IA achou na web (data, link) e que conversa com um tema ou
  artigo: vira post daquele material, com o evento como gancho;
* COMENTARIOS — so leitura.

O que veio da outra IA fica marcado (motivo do post, origem da abordagem), e
a pagina Estrategia compara: o que ela sugeriu funciona mais ou menos que o
que o PubliBot escolheu sozinho? Assim ela ganha (ou perde) credito com dado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.utils import timezone

from apps.social import experimentos, fontes
from apps.social.models import Abordagem, ConfiguracaoSocial, Destino, Post, Tema
from apps.social.redes import rede
from core.resposta_ia import itens, ler_blocos

ROTULOS = ["PROPOSTAS", "ABORDAGENS", "EVENTOS", "COMENTARIOS"]
TEMAS_NO_PEDIDO = 15
ARTIGOS_NO_PEDIDO = 25
POSTS_NO_PEDIDO = 15
MAXIMO_DE_PROPOSTAS = 5
MAXIMO_DE_ABORDAGENS = 3
MAXIMO_DE_EVENTOS = 5
TAMANHO = 6

PEDIDO = """\
Voce e estrategista de redes sociais de um negocio que publica artigos com
fontes (um blog). Os posts levam as pessoas aos artigos: eles NAO trazem
conteudo novo, so escolhem o que mostrar e o jeito de chamar a atencao.
Hoje e {hoje}.

Abaixo: o negocio, a conta ({rede}), o que ja se sabe dos posts dela, o que
engaja no nicho e os CANDIDATOS a post — temas (que atravessam varios
artigos) e artigos — cada um com um codigo.

COMO TRABALHAR COMIGO

1. Converse antes. Em poucas linhas: o que voce ve (o que funciona, o que
   falta, o publico certo?) e o que faria diferente. Espere eu responder e so
   gere os blocos quando eu pedir ("pode gerar"); se nao houver o que
   discutir, diga e ja gere.
2. Se voce puder PESQUISAR NA WEB, procure o que acontece agora e conversa com
   os candidatos: campanhas de saude do mes (ex.: Outubro Rosa), datas da
   area, regra nova de orgao publico, estudo que virou noticia, assunto em
   alta. So dos ultimos 30 dias ou dos proximos 60, e SO o que voce confirmou,
   com o link. Sem pesquisa na web, deixe EVENTOS vazio: nao invente.
3. O post so pode afirmar o que esta no artigo: nao proponha numero, caso ou
   promessa que nao esteja nos candidatos. Respeite as regras da conta.

Quando eu pedir os blocos, responda SO neles, nesta ordem, e termine com FIM
(a resposta volta para o sistema, que a le sozinho):

PROPOSTAS:
- codigo | gancho sugerido (ate 15 palavras) | por que, em ate 15 palavras
  (ate {max_propostas}, na ordem: a primeira e a que eu deveria postar primeiro)
ABORDAGENS:
- Nome curto: como escrever com ela, em 2 a 4 frases (e uma instrucao para
  quem escreve o post). Ate {max_abordagens}: jeitos que faltam, ou a versao
  melhor de uma que existe (mesmo nome + " v2"). Nenhuma, se as atuais bastam.
EVENTOS:
- AAAA-MM-DD | o evento | link da fonte | codigo do candidato | a ideia do post
  em uma frase (ate {max_eventos})
COMENTARIOS:
curto: o que voce acha da estrategia e perguntas para mim.
FIM

Regras dos blocos:
- Use os codigos exatamente como estao (m- ou a- e seis caracteres).
- Nao reescreva os candidatos: escolha e de o angulo.
"""


def codigo_do_tema(tema: Tema) -> str:
    return f"m-{tema.pk.hex[:TAMANHO]}"


def codigo_do_artigo(artigo_id) -> str:
    return f"a-{str(artigo_id).replace('-', '')[:TAMANHO]}"


# -- O pedido ------------------------------------------------------------------------
def _taxa(post: Post) -> str:
    v = experimentos.valor(post, ConfiguracaoSocial.Metrica.TAXA)
    if v is None or not isinstance((post.metricas or {}).get("alcance"), int | float):
        return f"{post.cliques} cliques"
    return f"taxa {100 * v:.1f}% (alcance {post.metricas['alcance']}), {post.cliques} cliques"


def _resultado(post: Post) -> str:
    if post.sucesso is None:
        return "cedo para julgar"
    return "acima do normal da conta" if post.sucesso else "abaixo do normal da conta"


def pedido(destino: Destino) -> str:
    from apps.social.estrategia import FASES, fase

    config = ConfiguracaoSocial.carregar()
    negocio = fontes.negocio()
    r = rede(destino.rede)
    partes = [
        PEDIDO.format(
            hoje=timezone.localdate().strftime("%d/%m/%Y"),
            rede=r.nome,
            max_propostas=MAXIMO_DE_PROPOSTAS,
            max_abordagens=MAXIMO_DE_ABORDAGENS,
            max_eventos=MAXIMO_DE_EVENTOS,
        ),
        "## Negocio",
        f"Tema: {negocio.get('tema') or '(nao informado)'}",
        f"Publico: {negocio.get('publico') or '(nao informado)'}",
        f"Oferta: {negocio.get('oferta') or '(nao informada)'}",
        "Dores do publico: " + ("; ".join(negocio.get("dores") or []) or "(nenhuma)"),
        "Onde atende: " + (", ".join(negocio.get("regioes") or []) or "(nao informado)"),
        "",
        f"## A conta: {destino.nome} ({r.nome})",
        f"Publico desta conta: {destino.publico or r.publico_padrao}",
        f"Tom: {destino.tom or r.tom_padrao}",
        f"Fase: {FASES[fase(destino, config)]['nome']}; seguidores: {destino.seguidores or '?'};"
        f" ate {destino.teto_semanal} posts por semana.",
        f"Regras que nunca se quebram: {config.regras or '(nenhuma)'}",
    ]
    if config.instrucoes.strip() or destino.instrucoes.strip():
        partes.append(
            "Instrucoes do negocio: "
            + " ".join(x for x in [config.instrucoes, destino.instrucoes] if x.strip())
        )

    partes += ["", "## Abordagens (jeitos de falar) e o placar delas"]
    for linha in experimentos.quadro(destino):
        a = linha["abordagem"]
        if not a.ativa:
            continue
        conta = f"{linha['taxa']}% de {linha['medidos']}" if linha["taxa"] is not None else "-"
        coletivo = (
            f"{linha['coletivo']}% de {linha['coletivo_medidos']}"
            if linha["coletivo"] is not None
            else "-"
        )
        partes.append(
            f"- {a.nome}: {_resumo(a.instrucao, 220)} [funcionou nesta conta: {conta}; "
            f"em outras contas: {coletivo}]"
        )

    recentes = list(
        destino.posts.filter(situacao=Post.Situacao.PUBLICADO)
        .select_related("abordagem")
        .order_by("-publicado_em")[:POSTS_NO_PEDIDO]
    )
    partes += ["", "## Posts recentes desta conta"]
    for post in recentes:
        quando = f"{timezone.localtime(post.publicado_em):%d/%m}" if post.publicado_em else ""
        partes.append(
            f"- {quando} | {post.abordagem or 'sem abordagem'} | "
            f'"{_resumo(post.gancho or post.texto, 120)}" | {_taxa(post)} | {_resultado(post)}'
            + (" | impulsionado" if post.impulsionado else "")
        )
    if not recentes:
        partes.append("(nenhum ainda)")

    referencias = list(destino.referencias.order_by("-curtidas")[:8])
    if referencias:
        partes += ["", "## O que mais engaja no nicho (posts de outras contas)"]
        for ref in referencias:
            partes.append(
                f"- #{ref.hashtag} | {ref.formato} | {ref.curtidas} curtidas, "
                f'{ref.comentarios} comentarios | "{_resumo(ref.legenda, 160)}"'
            )

    partes += ["", "## Candidatos: temas (atravessam varios artigos)"]
    temas = list(Tema.objects.filter(ativo=True).order_by("-nota")[:TEMAS_NO_PEDIDO])
    for tema in temas:
        partes.append(
            f"- {codigo_do_tema(tema)}: {_resumo(tema.titulo, 200)} "
            f"(nota {tema.nota:.2f}; em {len(tema.artigos)} artigos)"
        )
    if not temas:
        partes.append("(nenhum ainda)")

    partes += ["", "## Candidatos: artigos publicados"]
    for artigo_id in fontes.artigos_no_ar()[:ARTIGOS_NO_PEDIDO]:
        artigo = fontes.artigo(artigo_id)
        if artigo is None or not artigo.url:
            continue
        ultimo = destino.posts.filter(artigo_id=artigo_id).order_by("-criado_em").first()
        ja = (
            f" (ja foi para esta conta em {timezone.localtime(ultimo.criado_em):%d/%m})"
            if ultimo
            else ""
        )
        partes.append(
            f"- {codigo_do_artigo(artigo_id)}: {artigo.titulo} — {_resumo(artigo.resumo, 180)}{ja}"
        )
    return "\n".join(partes).strip() + "\n"


def _resumo(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rsplit(" ", 1)[0] + "…"


# -- A volta ---------------------------------------------------------------------------
@dataclass
class Alvo:
    """O candidato de um codigo: um tema ou um artigo."""

    codigo: str
    titulo: str
    tema: Tema | None = None
    artigo_id: str = ""


@dataclass
class Proposta:
    alvo: Alvo
    gancho: str
    por_que: str


@dataclass
class AbordagemNova:
    nome: str
    instrucao: str
    existe: bool = False


@dataclass
class Evento:
    data: str
    evento: str
    link: str
    ideia: str
    alvo: Alvo | None


@dataclass
class Leitura:
    propostas: list[Proposta] = field(default_factory=list)
    abordagens: list[AbordagemNova] = field(default_factory=list)
    eventos: list[Evento] = field(default_factory=list)
    comentarios: str = ""
    nao_achados: list[str] = field(default_factory=list)

    @property
    def vazia(self) -> bool:
        return not (self.propostas or self.abordagens or self.eventos)


_CODIGO = re.compile(r"\b([ma])-([0-9a-f]{4,32})\b", re.I)
_CODIGO_SOLTO = re.compile(r"\b[ma]-\w+", re.I)
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINK = re.compile(r"https?://\S+")


def _partes(linha: str) -> list[str]:
    return [p.strip().strip("`*_[]") for p in linha.split("|")]


class _Alvos:
    """Acha o tema ou artigo de um codigo (prefixo do id), uma consulta so."""

    def __init__(self):
        self._temas = {t.pk.hex: t for t in Tema.objects.filter(ativo=True)}
        self._artigos = {i.replace("-", ""): i for i in fontes.artigos_no_ar()}

    def achar(self, texto: str) -> Alvo | None:
        achado = _CODIGO.search(texto or "")
        if achado is None:
            return None
        tipo, prefixo = achado.group(1).lower(), achado.group(2).lower()
        codigo = f"{tipo}-{prefixo}"
        if tipo == "m":
            temas = [t for h, t in self._temas.items() if h.startswith(prefixo)]
            return Alvo(codigo, temas[0].titulo, tema=temas[0]) if len(temas) == 1 else None
        ids = [i for h, i in self._artigos.items() if h.startswith(prefixo)]
        if len(ids) != 1:
            return None
        artigo = fontes.artigo(ids[0])
        return Alvo(codigo, artigo.titulo, artigo_id=ids[0]) if artigo else None


def ler(resposta: str) -> Leitura:
    blocos = ler_blocos(resposta, ROTULOS)
    alvos = _Alvos()
    leitura = Leitura(comentarios=blocos.get("COMENTARIOS", "").strip())

    vistos = set()
    for linha in itens(blocos.get("PROPOSTAS", "")):
        partes = _partes(linha)
        alvo = alvos.achar(partes[0])
        if alvo is None:
            if _CODIGO_SOLTO.search(linha):  # continuacao de linha nao e codigo errado
                leitura.nao_achados.append(linha[:120])
            continue
        if alvo.codigo in vistos:
            continue
        vistos.add(alvo.codigo)
        leitura.propostas.append(
            Proposta(
                alvo=alvo,
                gancho=(partes[1] if len(partes) > 1 else "")[:200],
                por_que=(partes[2] if len(partes) > 2 else "")[:300],
            )
        )
    leitura.propostas = leitura.propostas[:MAXIMO_DE_PROPOSTAS]

    existentes = {a.nome.lower() for a in Abordagem.objects.all()}
    for linha in itens(blocos.get("ABORDAGENS", "")):
        nome, sep, instrucao = linha.partition(":")
        nome = nome.strip().strip("*_\"'").strip()
        if not sep or not nome or len(instrucao.strip()) < 20 or len(nome) > 80:
            continue
        leitura.abordagens.append(
            AbordagemNova(
                nome=nome, instrucao=instrucao.strip()[:2000], existe=nome.lower() in existentes
            )
        )
    leitura.abordagens = leitura.abordagens[:MAXIMO_DE_ABORDAGENS]

    for linha in itens(blocos.get("EVENTOS", "")):
        partes = _partes(linha)
        data = _DATA.search(linha)
        link = _LINK.search(linha)
        if data is None or link is None:  # sem data ou sem fonte: nao confirmado
            continue
        resto = [p for p in partes if p and not _DATA.fullmatch(p) and not _LINK.fullmatch(p)]
        resto = [p for p in resto if not _CODIGO.fullmatch(p)]
        leitura.eventos.append(
            Evento(
                data=data.group(0),
                evento=(resto[0] if resto else "")[:200],
                link=link.group(0).rstrip(").,;")[:500],
                ideia=(resto[1] if len(resto) > 1 else "")[:300],
                alvo=alvos.achar(linha),
            )
        )
    leitura.eventos = leitura.eventos[:MAXIMO_DE_EVENTOS]
    return leitura


# -- Aplicar ---------------------------------------------------------------------------
def _criar_post(destino: Destino, alvo: Alvo, por_que: str, ideia: str) -> list[Post]:
    from apps.social import escolha

    if alvo.tema is not None:
        return escolha.sugerir_tema(
            destino, alvo.tema, motivo=Post.Motivo.OUTRA_IA, por_que=por_que, ideia=ideia
        )
    artigo = fontes.artigo(alvo.artigo_id)
    if artigo is None:
        return []
    return escolha.sugerir(destino, artigo, Post.Motivo.OUTRA_IA, por_que, ideia=ideia)


def _nome_livre(nome: str) -> str:
    candidato, n = nome[:80], 2
    while Abordagem.objects.filter(nome__iexact=candidato).exists():
        sufixo = f" ({n})"
        candidato, n = nome[: 80 - len(sufixo)] + sufixo, n + 1
    return candidato


def aplicar(
    leitura: Leitura,
    destino: Destino,
    *,
    propostas: set[int],
    abordagens: set[int],
    eventos: set[int],
) -> dict:
    """Aplica o que a pessoa marcou na previa (pelos indices). Devolve as contagens."""
    from apps.social.abordagens import NAO_INVENTE

    feito = {"posts": 0, "abordagens": 0}
    for i, proposta in enumerate(leitura.propostas):
        if i not in propostas:
            continue
        por_que = "Sugerido pela outra IA" + (f": {proposta.por_que}" if proposta.por_que else ".")
        ideia = f"gancho sugerido: {proposta.gancho}" if proposta.gancho else ""
        feito["posts"] += len(_criar_post(destino, proposta.alvo, por_que, ideia))
    for i, evento in enumerate(leitura.eventos):
        if i not in eventos or evento.alvo is None:
            continue
        por_que = f"Evento ({evento.data}): {evento.evento} — {evento.link}"
        ideia = (
            f"use como gancho o evento '{evento.evento}' ({evento.data}), so pelo nome; "
            f"{evento.ideia}. Numeros e afirmacoes, so do material."
        )
        feito["posts"] += len(_criar_post(destino, evento.alvo, por_que, ideia))
    for i, nova in enumerate(leitura.abordagens):
        if i not in abordagens:
            continue
        instrucao = nova.instrucao
        if "invente" not in instrucao.lower():
            instrucao += NAO_INVENTE
        Abordagem.objects.create(
            nome=_nome_livre(nova.nome),
            instrucao=instrucao,
            redes=[destino.rede],
            origem=Abordagem.Origem.OUTRA_IA,
        )
        feito["abordagens"] += 1
    return feito


# -- O comparativo ---------------------------------------------------------------------
def _taxa_de_acerto(consulta) -> dict:
    julgados = list(consulta.filter(sucesso__isnull=False).values_list("sucesso", flat=True))
    n = len(julgados)
    return {"n": n, "taxa": round(100 * sum(julgados) / n) if n else None}


def comparativo(destino: Destino | None = None) -> dict:
    """O que veio da outra IA funciona mais ou menos que o resto? Por post
    (propostas e eventos) e por abordagem (as que ela criou)."""
    posts = Post.objects.filter(situacao=Post.Situacao.PUBLICADO)
    if destino is not None:
        posts = posts.filter(destino=destino)
    da_ia = posts.filter(motivo=Post.Motivo.OUTRA_IA)
    do_publibot = posts.exclude(
        motivo__in=[Post.Motivo.OUTRA_IA, Post.Motivo.PEDIDO, Post.Motivo.HISTORICO]
    )
    com_abordagem_da_ia = posts.filter(abordagem__origem=Abordagem.Origem.OUTRA_IA)
    com_outras = posts.exclude(abordagem__origem=Abordagem.Origem.OUTRA_IA).filter(
        abordagem__isnull=False
    )
    saida = {
        "posts_ia": _taxa_de_acerto(da_ia),
        "posts_publibot": _taxa_de_acerto(do_publibot),
        "abordagens_ia": _taxa_de_acerto(com_abordagem_da_ia),
        "abordagens_outras": _taxa_de_acerto(com_outras),
        "publicados_ia": da_ia.count(),
        "abordagens_criadas": Abordagem.objects.filter(origem=Abordagem.Origem.OUTRA_IA).count(),
    }
    saida["tem_algo"] = bool(saida["publicados_ia"] or saida["abordagens_criadas"])
    return saida
