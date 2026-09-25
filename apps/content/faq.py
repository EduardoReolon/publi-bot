"""Perguntas frequentes do artigo: gerar, revisar e entregar ao site.

Por que isto existe, e por que e pequeno:

* **Nao e pelo resultado rico.** O Google restringiu o FAQ enriquecido a
  sites de governo e saude em 2023 e o removeu de vez em 2026. Tambem diz que
  nao ha marcacao especial para aparecer nos recursos de IA.
* **E pelo conteudo.** Um bloco curto de perguntas diretas, com resposta na
  primeira frase, cobre as duvidas vizinhas que o leitor procura em seguida —
  e e o formato que buscadores que extraem respostas aproveitam melhor.
* **Vai SEPARADO do corpo**, no campo `faq` do payload (contrato, secao
  "Perguntas frequentes"). Quem decide onde e como exibir — bloco no fim,
  sanfona, JSON-LD — e o site, que conhece o proprio layout. Dentro do
  `html_content` essa decisao ficaria presa aqui, junto com o titulo do bloco
  num idioma escolhido por este lado.

A pessoa que revisa decide o que entra: o modelo gera mais do que o
necessario e ja marca as mais relevantes. Nao ha "refazer esta resposta" pela
fila — editar a mao e mais rapido que esperar outra rodada, e evita uma
estrutura inteira para uma resposta de tres frases.
"""

from __future__ import annotations

import json
import logging

from django.db import transaction

from apps.content.models import Article, ArticleFaq
from apps.content.rendering import PADRAO_MARCADOR, PADRAO_URL_SOLTA

logger = logging.getLogger("publibot.content")

# Quantas o modelo sugere, e quantas ja vem marcadas. Tres e o bastante para
# cobrir as duvidas vizinhas sem virar uma segunda metade do artigo; as outras
# ficam para quem revisa escolher.
SUGERIDAS = 6
MARCADAS = 3

MAXIMO_DA_PERGUNTA = 300


def interpretar(texto: str) -> list[tuple[str, str]]:
    """Le o JSON do modelo e devolve os pares aproveitaveis, na ordem dada.

    Descarta, em vez de recusar o lote inteiro, o par que traga endereco da
    web: aqui nao ha marcador de fonte, entao um link so pode ter sido
    inventado pelo modelo. Marcador solto e removido.
    """
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FAQ: o modelo nao devolveu JSON valido: {texto[:200]}") from exc

    itens = dados.get("perguntas") if isinstance(dados, dict) else dados
    if not isinstance(itens, list):
        raise ValueError("FAQ: esperado um objeto com a lista 'perguntas'.")

    pares = []
    vistas = set()
    for item in itens:
        if not isinstance(item, dict):
            continue
        pergunta = str(item.get("pergunta") or "").strip()
        resposta = PADRAO_MARCADOR.sub("", str(item.get("resposta") or "")).strip()
        if not pergunta or not resposta:
            continue
        if PADRAO_URL_SOLTA.search(pergunta) or PADRAO_URL_SOLTA.search(resposta):
            logger.warning("FAQ: par descartado por trazer endereco da web: %r", pergunta[:120])
            continue
        chave = pergunta.lower()
        if chave in vistas:
            continue
        vistas.add(chave)
        pares.append((pergunta[:MAXIMO_DA_PERGUNTA], resposta))
    return pares[:SUGERIDAS]


def gerar(article: Article, *, site=None, job=None) -> list[ArticleFaq]:
    """Pede as perguntas ao modelo e grava, com as primeiras ja marcadas.

    Nao gera de novo se o artigo ja tem FAQ: o que a pessoa revisou nao pode
    ser trocado por uma rodada automatica.
    """
    from apps.content.inference import executar_prompt
    from apps.content.services import esqueleto_do_artigo, montar_contexto_das_fontes

    if article.faq.exists():
        return list(article.faq.all())

    trechos = [
        citacao.super_chunk
        for citacao in article.citations.select_related("super_chunk").order_by("rank")
        if citacao.super_chunk is not None
    ]

    resultado = executar_prompt(
        key="article_faq",
        variaveis={
            "titulo": article.title,
            "palavra_chave": article.focus_keyword or article.title,
            "esqueleto": esqueleto_do_artigo(article) or "(sem secoes)",
            "fontes": montar_contexto_das_fontes(trechos) or "(sem fontes)",
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        job=job,
    )

    pares = interpretar(resultado.texto)
    with transaction.atomic():
        criadas = [
            ArticleFaq.objects.create(
                article=article,
                order=posicao,
                question=pergunta,
                answer=resposta,
                is_selected=posicao <= MARCADAS,
            )
            for posicao, (pergunta, resposta) in enumerate(pares, start=1)
        ]
    return criadas


@transaction.atomic
def salvar_revisao(article: Article, dados) -> int:
    """Aplica o formulario da tela de revisao. Devolve quantas linhas mudaram.

    `dados` e o `request.POST`. Para cada pergunta existente: `faq_<id>_pergunta`,
    `faq_<id>_resposta`, `faq_<id>_incluir` e `faq_<id>_apagar`. Uma pergunta
    nova vem em `faq_nova_pergunta` e `faq_nova_resposta`, e entra marcada —
    quem escreveu a mao quer que ela saia.
    """
    mudancas = 0

    for item in list(article.faq.all()):
        prefixo = f"faq_{item.pk}_"
        if dados.get(prefixo + "apagar"):
            item.delete()
            mudancas += 1
            continue

        pergunta = (dados.get(prefixo + "pergunta") or "").strip()[:MAXIMO_DA_PERGUNTA]
        resposta = (dados.get(prefixo + "resposta") or "").strip()
        incluir = bool(dados.get(prefixo + "incluir"))

        campos = []
        if pergunta and pergunta != item.question:
            item.question = pergunta
            campos.append("question")
        if resposta and resposta != item.answer:
            item.answer = resposta
            campos.append("answer")
        if incluir != item.is_selected:
            item.is_selected = incluir
            campos.append("is_selected")

        if campos:
            item.save(update_fields=[*campos, "updated_at"])
            mudancas += 1

    pergunta = (dados.get("faq_nova_pergunta") or "").strip()[:MAXIMO_DA_PERGUNTA]
    resposta = (dados.get("faq_nova_resposta") or "").strip()
    if pergunta and resposta:
        ultima = article.faq.order_by("-order").values_list("order", flat=True).first() or 0
        ArticleFaq.objects.create(
            article=article,
            order=ultima + 1,
            question=pergunta,
            answer=resposta,
            is_selected=True,
            origin=ArticleFaq.Origin.HUMAN,
        )
        mudancas += 1

    return mudancas


def itens_para_publicar(article: Article) -> list[dict]:
    """O campo `faq` do payload: so as selecionadas, na ordem da revisao.

    A pergunta vai como texto puro. A resposta vai em HTML, passada pelo
    Markdown e pela mesma sanitizacao do corpo, com TODO link removido — no
    FAQ nao ha citacao, entao nao ha destino legitimo. O site sanitiza de novo
    ao gravar, como faz com o `html_content`.
    """
    from apps.content.rendering import markdown_para_html, sanitizar_html

    return [
        {
            "question": item.question,
            "answer_html": sanitizar_html(
                markdown_para_html(item.answer), dominios_permitidos=set()
            ),
        }
        for item in article.faq.all()
        if item.is_selected
    ]
