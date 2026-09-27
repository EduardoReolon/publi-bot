"""Chamada para a oferta do site: se cabe, e onde.

O padrao de mercado para conteudo de marca: uma chamada NO CONTEXTO, logo
depois do trecho em que o problema de quem le encontra o que o site vende,
converte bem mais que so o rodape. Mas chamada em todo artigo — ou forcada num
texto que so explica um conceito — o leitor sente como anuncio. Entao:

* **none** — o tema esta longe da oferta; nem no fim;
* **end** — so no fim (o convite do Guia editorial no fecho, e o bloco do site);
* **inline** — tambem no meio, depois da secao mais proxima da oferta.

A decisao e por embedding, sem modelo de linguagem: a distancia entre a oferta
(Guia editorial) e o tema, e entre a oferta e cada secao planejada. A pessoa ve
o motivo na revisao e pode mudar.

O bloco em si e do SITE. O artigo leva so a marca `[[CHAMADA]]`, que vira
`<aside data-publibot="chamada"></aside>` no HTML; o site troca pelo
componente dele (botao, WhatsApp, rastreio). Trocar a oferta muda um lugar, nao
duzentos artigos. Site que nao conhece a marca mostra um elemento vazio.
"""

from __future__ import annotations

import logging
import re

import numpy as np

logger = logging.getLogger("publibot.content")

MARCA = "[[CHAMADA]]"
ELEMENTO = '<aside data-publibot="chamada"></aside>'
PADRAO_MARCA = re.compile(r"\[\[CHAMADA\]\]")
_PARAGRAFO_DA_MARCA = re.compile(r"<p>\s*\[\[CHAMADA\]\]\s*</p>")
_TITULO = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.MULTILINE)

# Mesma escala da aderencia do radar: distancia de cosseno ate 0,12 e "o mesmo
# assunto" (1,0); a partir de 0,30, "outro assunto" (0,0).
PERTO, LONGE = 0.12, 0.30
# Secao com aderencia a partir disto recebe a chamada no meio.
MINIMO_NO_MEIO = 0.5
# Tema com aderencia abaixo disto nao leva chamada nenhuma.
MINIMO_NO_FIM = 0.2


def _escala(distancia: float) -> float:
    return float(min(1.0, max(0.0, (LONGE - distancia) / (LONGE - PERTO))))


def _vetor(texto: str) -> np.ndarray:
    from apps.knowledge.embeddings import get_embedding_client

    return np.asarray(get_embedding_client().embed_query(texto), dtype=np.float32)


def _distancia(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if not na or not nb:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


def texto_da_oferta() -> str:
    from apps.editorial.services import perfil_atual

    perfil = perfil_atual()
    if perfil is None:
        return ""
    return (perfil.oferta or perfil.convite or "").strip()


def decidir(article) -> dict:
    """Modo, secao e o porque, a partir do plano ja gravado.

    Pauta com escolha da pessoa vale sobre a conta. Sem oferta no Guia
    editorial nao ha com o que comparar: fica "so no fim", o comportamento
    de sempre.
    """
    forcado = getattr(article.topic, "call_to_action", "") if article.topic_id else ""
    oferta = texto_da_oferta()
    secoes = list(article.sections.all())
    decisao = {"modo": "end", "secao": None, "aderencia": None, "motivo": ""}

    if not oferta:
        decisao["motivo"] = "sem oferta no Guia editorial"
        if forcado:
            decisao["modo"] = forcado
    else:
        try:
            aderencia, melhor = _medir(article, secoes, oferta)
        except Exception as exc:
            # Sem o modelo de embedding a chamada nao pode derrubar a geracao
            # do artigo: fica o comportamento de sempre, e a revisao diz por que.
            logger.exception("Falha ao medir a proximidade com a oferta.")
            decisao["motivo"] = f"nao foi possivel medir: {str(exc)[:120]}"
            decisao["modo"] = forcado or "end"
            aderencia, melhor = None, (0.0, None)
        decisao["aderencia"] = round(aderencia, 3) if aderencia is not None else None

        if aderencia is None:
            # Ja decidido acima, pela falha.
            melhor = (0.0, None)
        elif forcado:
            decisao["modo"] = forcado
            decisao["motivo"] = "escolhida na pauta"
        elif melhor[0] >= MINIMO_NO_MEIO:
            decisao["modo"] = "inline"
            decisao["motivo"] = "uma secao trata do que a oferta resolve"
        elif aderencia >= MINIMO_NO_FIM:
            decisao["modo"] = "end"
            decisao["motivo"] = "tema perto da oferta, sem secao que peca a chamada no meio"
        else:
            decisao["modo"] = "none"
            decisao["motivo"] = "tema longe da oferta"
        # Secao nenhuma perto da oferta (so quando a pauta forcou o meio): a
        # regra de baixo poe a chamada no meio do artigo.
        if decisao["modo"] == "inline" and melhor[0] > 0:
            decisao["secao"] = melhor[1]
            decisao["aderencia_da_secao"] = round(melhor[0], 3)

    if decisao["modo"] == "inline" and decisao["secao"] is None:
        # Forcada no meio sem secao escolhida: depois da do meio do artigo.
        decisao["secao"] = secoes[len(secoes) // 2].order if len(secoes) > 1 else None
        if decisao["secao"] is None:
            decisao["modo"] = "end"
    return decisao


def _medir(article, secoes, oferta: str) -> tuple[float, tuple[float, int | None]]:
    """Aderencia do tema a oferta, e a melhor secao (aderencia, ordem)."""
    vetor_da_oferta = _vetor(oferta)
    tema = ". ".join(p for p in [article.title, article.focus_keyword] if p)
    aderencia = _escala(_distancia(_vetor(tema), vetor_da_oferta))
    # A primeira secao fica de fora: chamada antes de o texto entregar alguma
    # coisa e o que faz o leitor desconfiar do resto.
    candidatas = []
    for secao in secoes[1:]:
        texto = ". ".join(p for p in [secao.heading, secao.intent] if p)
        candidatas.append((_escala(_distancia(_vetor(texto), vetor_da_oferta)), secao.order))
    return aderencia, max(candidatas, default=(0.0, None))


def aplicar_decisao(article) -> dict:
    decisao = decidir(article)
    article.call_to_action = decisao["modo"]
    article.call_to_action_after = decisao["secao"]
    tese = dict(article.thesis_json or {})
    tese["chamada"] = decisao
    article.thesis_json = tese
    article.save(update_fields=["call_to_action", "call_to_action_after", "thesis_json"])
    return decisao


def tirar_marcas(markdown: str) -> str:
    sem = PADRAO_MARCA.sub("", markdown or "")
    return re.sub(r"\n{3,}", "\n\n", sem).strip()


def inserir_marca(markdown: str, secoes_ate_a_marca: int) -> str:
    """A marca num paragrafo proprio, antes do titulo da secao seguinte.

    `secoes_ate_a_marca` conta os titulos de secao (## a ####) do Markdown: a
    marca vai antes do titulo de numero seguinte. Sem titulo seguinte, vai ao
    fim do texto.
    """
    texto = tirar_marcas(markdown)
    titulos = list(_TITULO.finditer(texto))
    if secoes_ate_a_marca < len(titulos):
        corte = titulos[secoes_ate_a_marca].start()
        return f"{texto[:corte].rstrip()}\n\n{MARCA}\n\n{texto[corte:]}"
    return f"{texto}\n\n{MARCA}"


def _titulos(markdown: str) -> list[str]:
    return [achado.group(2).strip() for achado in _TITULO.finditer(markdown or "")]


def posicao_da_marca(markdown: str) -> int | None:
    """Quantos titulos de secao ha antes da primeira marca; None sem marca."""
    achado = PADRAO_MARCA.search(markdown or "")
    if achado is None:
        return None
    return len(list(_TITULO.finditer(markdown[: achado.start()])))


def reconciliar_com_o_texto(article, markdown: str) -> str:
    """Depois de uma edicao a mao, o texto manda: a marca diz o modo e a secao.

    Marca apagada: a chamada volta a ficar so no fim. Marca acrescentada ou
    movida: vale onde a pessoa pos. Mais de uma: fica a primeira.
    """
    posicao = posicao_da_marca(markdown)
    if posicao is None:
        if article.call_to_action == "inline":
            article.call_to_action = "end"
            article.call_to_action_after = None
        return markdown
    secoes = list(article.sections.all())
    article.call_to_action = "inline"
    # Pelo titulo da secao logo acima da marca: secao sem texto nao aparece no
    # corpo, e a contagem de titulos sozinha apontaria a secao errada.
    acima = _titulos(markdown[: PADRAO_MARCA.search(markdown).start()])
    por_titulo = {s.heading.strip(): s.order for s in secoes}
    if acima and acima[-1] in por_titulo:
        article.call_to_action_after = por_titulo[acima[-1]]
    else:
        ordens = [s.order for s in secoes]
        article.call_to_action_after = ordens[posicao - 1] if 0 < posicao <= len(ordens) else None
    primeira = PADRAO_MARCA.search(markdown)
    resto = PADRAO_MARCA.sub("", markdown[primeira.end() :])
    return re.sub(r"\n{3,}", "\n\n", markdown[: primeira.end()] + resto)


def marca_no_html(html: str) -> str:
    """A marca vira o elemento que o site troca pelo componente dele.

    Chamada DEPOIS da sanitizacao: `aside` nao esta na lista de permissao, e
    assim o unico `aside` que chega ao site e este, sem atributo nenhum alem do
    marcador.
    """
    html = _PARAGRAFO_DA_MARCA.sub(ELEMENTO, html, count=1)
    # Marca solta no meio de um paragrafo, ou repetida: sai do texto.
    html = _PARAGRAFO_DA_MARCA.sub("", html)
    return PADRAO_MARCA.sub("", html)


def sem_marca_no_html(html: str) -> str:
    return marca_no_html(html).replace(ELEMENTO, "")


def mudar(article, modo: str, secao: int | None, *, editor) -> None:
    """Troca o modo na revisao, e leva o texto junto."""
    from apps.content.services import aplicar_edicao_humana

    secoes = {s.order: s for s in article.sections.all()}
    ordens = list(secoes)
    if modo == "inline":
        if secao not in secoes:
            secao = article.call_to_action_after if article.call_to_action_after in secoes else None
        if secao is None:
            secao = ordens[len(ordens) // 2] if len(ordens) > 1 else (ordens[0] if ordens else None)
        titulos = _titulos(article.body_markdown)
        if secao is None:
            antes = 0
        elif secoes[secao].heading.strip() in titulos:
            antes = titulos.index(secoes[secao].heading.strip()) + 1
        else:
            antes = ordens.index(secao) + 1
        markdown = inserir_marca(article.body_markdown, antes)
    else:
        markdown = tirar_marcas(article.body_markdown)
    article.call_to_action = modo
    article.call_to_action_after = secao if modo == "inline" else None
    article.save(update_fields=["call_to_action", "call_to_action_after"])
    aplicar_edicao_humana(article, markdown, editor=editor)
