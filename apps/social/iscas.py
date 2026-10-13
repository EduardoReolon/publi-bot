"""Isca: a oferta para quem comentar ("comente QUERO e receba o guia").

Engajamento com troca: o post termina convidando a comentar uma palavra; quem
comenta recebe, na resposta ao comentario, o que foi prometido (com o link
rastreado do PubliBot). O negocio cadastra o que pode oferecer (guia,
checklist, avaliacao gratis, aula) e o PubliBot usa nos posts de engajamento,
numa parte deles (parametro `iscas_por_cento`), revezando as iscas.

Reconhecer o pedido e por algoritmo, no modo que a isca escolher:

* **todos** — qualquer comentario pede (o post diz "comente aqui");
* **palavra** — a palavra aparece, sem importar maiuscula, acento, pontuacao
  ou emoji ("Quero!!", "quero 🙏");
* **aproximado** — tambem a palavra com erro de digitacao ("qeuro") e frases
  claras de interesse ("me manda", "tenho interesse"); o duvidoso ("sim",
  "eu", so emoji) fica como "talvez" no painel, para um clique decidir.

Responde na rede (resposta publica ao comentario). Mensagem no direct pede a
permissao de mensagens da Meta, que passa por revisao: fica para depois.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.social.models import Comentario, Isca, Post

logger = logging.getLogger("publibot.social")

SIM, TALVEZ, NAO = "sim", "talvez", "nao"
# Frases que pedem com clareza, e as que podem ser pedido ou nao.
INTERESSE = (
    "quero",
    "eu quero",
    "me manda",
    "manda pra mim",
    "manda para mim",
    "me envia",
    "pode mandar",
    "tenho interesse",
    "gostaria",
    "quero receber",
    "queria",
)
TALVEZ_FRASES = ("sim", "eu", "eu tambem", "eu tb", "eu tbm", "interesse", "aqui", "oi")
PARECIDA = 0.8
# Quanto tempo depois do post os comentarios ainda sao lidos de 5 em 5 minutos.
DIAS_DE_ATENCAO = 10


def normalizar(texto: str) -> str:
    """Minusculo, sem acento, sem pontuacao nem emoji, espacos simples."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9@ ]+", " ", sem_acento.lower()).split())


def _parecida(a: str, b: str) -> bool:
    if len(b) < 4 or abs(len(a) - len(b)) > 2:
        return False
    return SequenceMatcher(None, a, b).ratio() >= PARECIDA


def reconhecer(isca: Isca, texto: str) -> str:
    """ "sim", "talvez" ou "nao": o comentario pede a isca?"""
    if isca.modo == Isca.Modo.TODOS:
        return SIM
    limpo = normalizar(texto)
    sem_marcacao = " ".join(p for p in limpo.split() if not p.startswith("@"))
    palavra = normalizar(isca.palavra)
    if palavra and re.search(rf"(^| ){re.escape(palavra)}s?( |$)", sem_marcacao):
        return SIM
    if not palavra:
        return SIM  # sem palavra, comentar ja e pedir
    if isca.modo == Isca.Modo.PALAVRA:
        return NAO
    palavras = sem_marcacao.split()
    if " " not in palavra and any(_parecida(p, palavra) for p in palavras):
        return SIM
    if any(re.search(rf"(^| ){f}( |$)", sem_marcacao) for f in INTERESSE):
        return SIM
    if not palavras:  # so emoji (pode ser a mao levantada) ou so marcacao de amigo
        return TALVEZ if (texto or "").strip() and "@" not in texto else NAO
    return TALVEZ if sem_marcacao in TALVEZ_FRASES else NAO


def link(isca: Isca) -> str:
    """O link rastreado (conta os cliques e segue para a entrega)."""
    from apps.social import fontes

    if not isca.link:
        return ""
    return fontes.endereco_publico(reverse("social:isca", args=[isca.chave_publica]))


def texto_da_resposta(isca: Isca, comentario: Comentario) -> str:
    texto = isca.resposta or "{nome}, aqui esta: {link}"
    for chave, valor in (("{nome}", comentario.autor or ""), ("{link}", link(isca))):
        texto = texto.replace(chave, valor)
    # Sem nome: tira a virgula/espaco que sobra no comeco.
    return re.sub(r"^[\s,!.]+", "", texto).strip()


def chamada(isca: Isca) -> str:
    """A frase do fim do post, quando quem escreveu nao a pos."""
    if isca.modo == Isca.Modo.TODOS or not isca.palavra:
        return f"Comente aqui que eu te mando: {isca.nome}."
    return f"Comente {isca.palavra.upper()} que eu te mando: {isca.nome}."


def instrucao(post: Post) -> str:
    isca = post.isca if post.isca_id else None
    if isca is None:
        return ""
    pedir = (
        "a comentar qualquer coisa"
        if isca.modo == Isca.Modo.TODOS or not isca.palavra
        else f"a comentar a palavra {isca.palavra.upper()}"
    )
    return (
        f"OFERTA PARA QUEM COMENTAR: termine convidando {pedir} para receber: {isca.nome}. "
        f"{isca.descricao.strip()} Uma frase, no fim, sem prometer resultado de saude "
        "nem pressionar.\n"
    )


def garantir_chamada(post: Post, texto: str) -> str:
    """O post com isca precisa dizer a palavra: se o modelo esqueceu, o
    PubliBot poe a chamada no fim (algoritmo, sem outra volta no modelo)."""
    isca = post.isca if post.isca_id else None
    if isca is None or not texto:
        return texto
    palavra = normalizar(isca.palavra)
    if palavra and palavra in normalizar(texto):
        return texto
    if not palavra and "coment" in normalizar(texto):
        return texto
    return f"{texto.rstrip()}\n\n{chamada(isca)}"


# -- Escolher a isca de um post novo ------------------------------------------------------
def escolher(destino, foco: str) -> Isca | None:
    """Em parte dos posts de engajamento (parametro), a isca menos usada nas
    ultimas semanas nesta conta — revezar evita cansar o publico."""
    from apps.social import focos, parametros

    if foco != "engajamento":
        return None
    iscas = [i for i in Isca.objects.filter(ativa=True) if not i.redes or destino.rede in i.redes]
    if not iscas:
        return None
    por_cento = parametros.valor("iscas_por_cento")
    recentes = destino.posts.filter(
        criado_em__gte=timezone.now() - focos.JANELA, extras__foco="engajamento"
    )
    total = recentes.count() + 1
    com_isca = recentes.exclude(isca=None).count()
    if com_isca / total >= por_cento / 100:  # ja tem a parte dele (contando o novo)
        return None
    usos = dict(
        recentes.exclude(isca=None)
        .values_list("isca")
        .annotate(n=Count("id"))
        .values_list("isca", "n")
    )
    return min(iscas, key=lambda i: (usos.get(i.pk, 0), i.criado_em))


# -- Comentarios ---------------------------------------------------------------------------
def avaliar(comentario: Comentario) -> None:
    """Marca o pedido no comentario novo (o envio e em `responder`)."""
    post = comentario.post
    if not post.isca_id or comentario.respondido_em:
        return
    comentario.pedido = {SIM: "sim", TALVEZ: "talvez"}.get(
        reconhecer(post.isca, comentario.texto), ""
    )
    if comentario.pedido:
        comentario.save(update_fields=["pedido"])


def enviar(comentario: Comentario, *, http=None) -> bool:
    """Responde o comentario com a isca, na rede."""
    from apps.social.redes import rede
    from apps.social.redes.base import ErroDaRede

    post = comentario.post
    isca = post.isca
    if isca is None or comentario.respondido_em:
        return False
    texto = texto_da_resposta(isca, comentario)
    try:
        id_remoto = (
            rede(post.destino.rede)
            .publicador(post.destino, http=http)
            .responder(post, comentario.id_remoto, texto)
        )
    except ErroDaRede as exc:
        comentario.erro = str(exc)[:2000]
        comentario.save(update_fields=["erro"])
        return False
    comentario.pedido = SIM
    comentario.resposta = texto
    comentario.respondido_em = timezone.now()
    comentario.id_da_resposta = (id_remoto or "")[:300]
    comentario.erro = ""
    comentario.save(update_fields=["pedido", "resposta", "respondido_em", "id_da_resposta", "erro"])
    return True


def responder(*, http=None) -> int:
    """Le os comentarios dos posts recentes com isca e responde os pedidos
    (das iscas que respondem sozinhas). Roda de 5 em 5 minutos."""
    from apps.social import comentarios

    desde = timezone.now() - timedelta(days=DIAS_DE_ATENCAO)
    posts = Post.objects.filter(
        situacao=Post.Situacao.PUBLICADO, publicado_em__gte=desde, isca__ativa=True
    ).select_related("destino", "isca")
    for post in posts:
        try:
            comentarios.ler(post, http=http)
        except Exception:  # uma conta com problema nao para as outras
            logger.exception("Comentarios do post %s nao lidos.", post.pk)
    pendentes = Comentario.objects.filter(
        post__in=posts,
        post__isca__responder_sozinho=True,
        pedido=SIM,
        respondido_em__isnull=True,
        erro="",
    ).select_related("post__destino", "post__isca")
    return sum(enviar(c, http=http) for c in pendentes)


# -- Painel --------------------------------------------------------------------------------
def painel(conta=None) -> list[dict]:
    """Cada isca: posts, comentarios, pedidos, respondidos, cliques, e o
    comentario por post contra os de engajamento sem isca (da conta, se dada;
    os cliques no link sao da isca, de todas as contas)."""
    publicados = Post.objects.filter(situacao=Post.Situacao.PUBLICADO)
    if conta is not None:
        publicados = publicados.filter(destino=conta)
    sem_isca = publicados.filter(isca=None, extras__foco="engajamento").annotate(
        n=Count("comentarios")
    )
    base = [p.n for p in sem_isca]
    media_sem = round(sum(base) / len(base), 1) if base else None
    linhas = []
    filtro = Q(posts__situacao=Post.Situacao.PUBLICADO)
    if conta is not None:
        filtro &= Q(posts__destino=conta)
    for isca in Isca.objects.annotate(n_posts=Count("posts", filter=filtro, distinct=True)):
        if conta is not None and isca.redes and conta.rede not in isca.redes:
            continue
        comentarios = Comentario.objects.filter(post__isca=isca)
        if conta is not None:
            comentarios = comentarios.filter(post__destino=conta)
        total = comentarios.count()
        linhas.append(
            {
                "isca": isca,
                "posts": isca.n_posts,
                "comentarios": total,
                "por_post": round(total / isca.n_posts, 1) if isca.n_posts else None,
                "media_sem_isca": media_sem,
                "pedidos": comentarios.filter(pedido=SIM).count(),
                "talvez": comentarios.filter(pedido=TALVEZ, respondido_em=None).count(),
                "respondidos": comentarios.filter(pedido=SIM).exclude(respondido_em=None).count(),
                "cliques": isca.cliques,
            }
        )
    return linhas


def a_decidir(conta=None):
    """Comentarios que esperam a pessoa: "talvez", pedidos de isca que nao
    responde sozinha, e envios que deram erro (da conta, se dada)."""
    base = Comentario.objects.filter(post__isca__isnull=False, respondido_em=None)
    if conta is not None:
        base = base.filter(post__destino=conta)
    return (
        base.filter(
            Q(pedido=TALVEZ)
            | Q(pedido=SIM, post__isca__responder_sozinho=False)
            | (Q(pedido=SIM) & ~Q(erro=""))
        )
        .select_related("post__destino", "post__isca")
        .order_by("-escrito_em")[:100]
    )


# -- Outra IA: o que o negocio pode oferecer -------------------------------------------------
ROTULOS = ["ISCAS", "COMENTARIOS"]
MAXIMO_DE_IDEIAS = 5

PEDIDO = """\
Voce ajuda um negocio a pensar o que oferecer a quem comenta nos posts dele
(a "isca": "comente QUERO e receba ..."). O objetivo e conversa e contato,
sem parecer propaganda. Exemplos de formato: guia ou checklist curto,
avaliacao ou conversa gratis de 15-20 minutos, aula ao vivo, lista de
perguntas para levar a consulta, calculadora/planilha simples.

Regras:
- So o que este negocio consegue entregar de verdade, sem custo alto por pessoa.
- Respeite as regras da conta e as do conselho profissional da area (algumas
  proibem brinde, sorteio ou consulta gratis como chamariz): se for o caso,
  diga e proponha outra coisa.
- Nada que prometa resultado de saude ou peca dado sensivel no comentario.
- Palavra curta, facil de digitar no celular, sem acento.
- Parta do que o publico quer (abaixo): a isca boa resolve uma pergunta ou dor
  que aparece nas buscas e nos comentarios.

Converse antes, se precisar. Quando eu pedir, responda SO nos blocos e
termine com FIM:

ISCAS:
- o que a pessoa recebe | PALAVRA | como funciona, em 2 frases | resposta ao
  comentario (use {{nome}} e {{link}})
  (ate {maximo}, a melhor primeiro)
COMENTARIOS:
curto: o que precisa ser preparado e o que voce testaria primeiro.
FIM
"""


def pedido() -> str:
    from apps.social import desejos, fontes
    from apps.social.models import ConfiguracaoSocial

    negocio = fontes.negocio()
    config = ConfiguracaoSocial.carregar()
    partes = [
        PEDIDO.format(maximo=MAXIMO_DE_IDEIAS),
        "## Negocio",
        f"Tema: {negocio.get('tema') or '(nao informado)'}",
        f"Publico: {negocio.get('publico') or '(nao informado)'}",
        f"Oferta: {negocio.get('oferta') or '(nao informada)'}",
        "Dores do publico: " + ("; ".join(negocio.get("dores") or []) or "(nenhuma)"),
        "Onde atende: " + (", ".join(negocio.get("regioes") or []) or "(nao informado)"),
        f"Regras que nunca se quebram: {config.regras or '(nenhuma)'}",
        *desejos.secao(),
        "",
        "## Iscas que ja existem e o resultado",
    ]
    linhas = painel()
    for linha in linhas:
        i = linha["isca"]
        partes.append(
            f"- {i.nome} (palavra {i.palavra or '-'}; {'ativa' if i.ativa else 'parada'}): "
            f"{linha['posts']} posts, {linha['pedidos']} pedidos, {linha['cliques']} cliques"
        )
    if not linhas:
        partes.append("(nenhuma ainda)")
    return "\n".join(partes).strip() + "\n"


def ler_ideias(resposta: str) -> tuple[list[dict], str]:
    from core.resposta_ia import itens, ler_blocos

    blocos = ler_blocos(resposta, ROTULOS)
    ideias, vistos = [], set()
    for linha in itens(blocos.get("ISCAS", "")):
        partes = [p.strip().strip("`*_[]") for p in linha.split("|")]
        if len(partes) < 2 or not partes[0] or partes[0].lower() in vistos:
            continue
        vistos.add(partes[0].lower())
        ideias.append(
            {
                "nome": partes[0][:120],
                "palavra": re.sub(r"[^A-Za-z0-9]", "", partes[1])[:40].upper(),
                "descricao": partes[2] if len(partes) > 2 else "",
                "resposta": " | ".join(partes[3:]) if len(partes) > 3 else "",
            }
        )
    return ideias[:MAXIMO_DE_IDEIAS], blocos.get("COMENTARIOS", "").strip()


def salvar_ideias(ideias: list[dict]) -> list[Isca]:
    """As ideias viram iscas PARADAS: a pessoa poe o link, ajusta e liga."""
    salvas = []
    for ideia in ideias:
        isca, criada = Isca.objects.get_or_create(
            nome=ideia["nome"],
            defaults={
                "palavra": ideia["palavra"],
                "descricao": ideia["descricao"],
                "resposta": ideia["resposta"] or "{nome}, aqui esta: {link}",
                "ativa": False,
                "origem": Isca.Origem.OUTRA_IA,
            },
        )
        if criada:
            salvas.append(isca)
    return salvas
