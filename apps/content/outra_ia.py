"""O artigo escrito por um modelo grande de fora, com as mesmas travas.

O caminho de sempre (o modelo local, secao a secao) continua. Este e o
segundo: para a pauta que vale mais, a pessoa copia um pedido FECHADO para um
modelo grande e cola a resposta.

Fechado quer dizer: as fontes vao dentro do pedido, numeradas como no caminho
de sempre, e o pedido proibe busca na web e numero inventado. O que volta passa
por `aplicar_rascunho` — a mesma trava do caminho local: endereco escrito pelo
modelo derruba o texto, e os marcadores [[FONTE_n]] viram link a partir das
citacoes gravadas, nunca a partir do que o modelo escreveu.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from core.resposta_ia import _chave, _como_rotulo, ler_blocos

ORIGEM = "outra_ia"

ROTULOS = [
    "TITULO SUGERIDO",
    "META DESCRIPTION",
    "RESUMO CURTO",
    "PERGUNTAS FREQUENTES",
    "PEDIDOS",
    "CHAMADA DO MEIO",
    "CHAMADA DO FIM",
]

# No fluxo da pesquisa, as fontes sao resumos de artigos cientificos.
REGRAS_DA_PESQUISA = """\
As fontes sao RESUMOS de artigos cientificos achados para esta pauta, agrupados
por angulo na orientacao. Afirme so o que o resumo diz; ao citar um estudo, diga
o contexto que o resumo der (setor, pais, amostra). Nenhum numero que nao esteja
escrito no resumo. Se o artigo ficaria melhor com algo que so o texto completo
de um estudo traz (a amostra, o metodo, os numeros, as estrategias comparadas),
escreva assim mesmo com o que os resumos sustentam e PECA no bloco PEDIDOS."""
BLOCO_DE_PEDIDOS = """\
PEDIDOS:
- fonte N: o que precisaria ler no texto completo, e para que parte do artigo
(no maximo 3; escreva "nenhum" se os resumos bastaram)
"""
# O texto das chamadas, escrito pela outra IA junto com o artigo (no caminho
# daqui, o modelo local o escreve depois). Mesmos limites e regras.
BLOCO_DA_CHAMADA = """\
CHAMADA DO MEIO: (so se o corpo tiver [[CHAMADA]]; senao escreva "nenhuma")
title: ...
text: ...
button: ...
CHAMADA DO FIM:
title: ...
text: ...
button: ...
"""
_CAMPO_DA_CHAMADA = re.compile(
    r"^\s*(?:[-*]\s*)?\**\s*(title|text|button|titulo|texto|botao)\s*\**\s*:\**\s*(.*)$",
    re.IGNORECASE,
)
_CAMPOS = {"titulo": "title", "texto": "text", "botao": "button"}
ROTULO_DO_CORPO = "CORPO DO ARTIGO"
FIM_DO_CORPO = "FIM DO ARTIGO"

_TITULO_DE_SECAO = re.compile(r"^(#{2,4})\s+(.+?)\s*#*\s*$")
_PERGUNTA = re.compile(r"^\s*(?:[-*]\s*)?\**(?:P|PERGUNTA)\**\s*[:.)-]\**\s*(.+)$", re.IGNORECASE)
_RESPOSTA = re.compile(r"^\s*(?:[-*]\s*)?\**(?:R|RESPOSTA)\**\s*[:.)-]\**\s*(.+)$", re.IGNORECASE)

# Pauta que pede o modelo grande: muita busca, nota alta ou dinheiro perto.
VOLUME_DE_PESO = 500
NOTA_DE_PESO = 70
COMERCIAL_DE_PESO = 0.6


def motivos_de_peso(pauta) -> list[str]:
    """Por que esta pauta merece o modelo grande; vazio se o local basta."""
    evidencia = pauta.evidence or {}
    motivos = []
    volume = evidencia.get("volume_total") or 0
    if volume >= VOLUME_DE_PESO:
        motivos.append(f"{volume} buscas/mes")
    if (pauta.demand_score or 0) >= NOTA_DE_PESO:
        motivos.append(f"nota {pauta.demand_score:.0f} no radar")
    comercial = (evidencia.get("parcelas") or {}).get("comercial") or 0
    if comercial >= COMERCIAL_DE_PESO:
        motivos.append("valor comercial alto")
    if (getattr(pauta, "debate", None) or {}).get("frentes"):
        # Confrontar versoes com muitas fontes e o texto mais dificil para um
        # modelo pequeno.
        motivos.append("pauta investigada (frentes e debate: muito material)")
    return motivos


def artigo_em_espera(pauta, fluxo: str = ""):
    """O artigo desta pauta (neste fluxo) esperando a resposta de outra IA."""
    from apps.content.models import Article

    for artigo in pauta.articles.filter(status=Article.Status.DRAFTING, fluxo=fluxo):
        if (artigo.thesis_json or {}).get("origem") == ORIGEM:
            return artigo
    return None


def preparar(pauta, fluxo: str = "", *, refaz=None):
    """Busca as fontes no acervo e cria o artigo que espera o texto de fora.

    Sem fonte que sustente a pauta, nao ha artigo: `fontes_da_pauta` levanta
    `SemFontesSuficientes`, exatamente como no caminho de sempre.

    `refaz`: o artigo que este texto refaz, com as MESMAS fontes (nenhuma
    busca nova). Publicado, o novo e a versao seguinte dele; rascunho, o novo o
    substitui quando o texto colado for aceito. Uma geracao local pedida para
    ele (em curso ou esperando) para: e um texto ou o outro.
    """
    from apps.content.services import fontes_da_pauta

    # A mesma regra do caminho de sempre: sem fonte que sustente a pauta, para.
    # Fora da transacao: a pauta marcada "aguardando fontes" tem de ficar.
    trechos = fontes_da_pauta(pauta, fluxo=fluxo)
    with transaction.atomic():
        if refaz is not None:
            _parar_a_geracao_local(pauta, fluxo, refaz)
        return _criar_artigo(pauta, trechos, fluxo, refaz=refaz)


def _parar_a_geracao_local(pauta, fluxo: str, refaz) -> None:
    from apps.content import fluxos
    from apps.knowledge.referencias import registrar

    trabalho = fluxos.em_andamento(pauta, fluxo or fluxos.A)
    payload = (trabalho.step_payloads or {}) if trabalho is not None else {}
    if str(refaz.pk) in (payload.get("substitui"), payload.get("versao_de")):
        fluxos.desistir(trabalho, "Trocada pelo texto da outra IA.")
    dados = (pauta.busca_de_fontes or {}).get("pesquisa") or {}
    if str(refaz.pk) in (dados.get("substitui"), dados.get("versao_de")):
        # O texto do B esperado ao fim da pesquisa nao sai mais sozinho.
        registrar(pauta, "pesquisa", gerar_depois=False, substitui="", versao_de="")


def _criar_artigo(pauta, trechos, fluxo: str = "", *, refaz=None):
    from apps.content.chamada import aplicar_decisao
    from apps.content.flows import versao_seguinte
    from apps.content.models import Article, Author, Topic
    from apps.content.services import registrar_citacoes

    padrao = Author.do_site()
    tese = {"origem": ORIGEM, "situacao_da_pauta": pauta.status}
    publicado = refaz is not None and refaz.status == Article.Status.PUBLISHED
    anterior, de_versao = versao_seguinte(refaz.pk if publicado else None)
    if refaz is not None and not publicado:
        tese["substitui"] = str(refaz.pk)
    artigo = Article.objects.create(
        **de_versao,
        topic=pauta,
        title=pauta.title,
        slug=(anterior.slug if anterior else slugify(pauta.title))[:300],
        focus_keyword=pauta.target_keyword,
        content_type=pauta.content_type,
        fluxo=fluxo,
        thesis_json=tese,
        single_source=len(trechos) == 1,
        author=padrao,
        author_name=getattr(padrao, "name", "") or "",
        author_credentials=getattr(padrao, "credentials", "") or "",
        status=Article.Status.DRAFTING,
    )
    registrar_citacoes(artigo, trechos)
    # Sem secoes ainda: a conta decide entre "no fim" e "sem chamada"; o texto
    # colado diz depois se ela vai para o meio (a marca [[CHAMADA]]).
    aplicar_decisao(artigo)
    pauta.status = Topic.Status.USED
    pauta.save(update_fields=["status"])
    return artigo


def para_colar_de_novo(pauta, pk):
    """O artigo da pauta, ja escrito pela outra IA e ainda nao publicado, em
    que a resposta pode ser colada de novo (as citacoes ficam as mesmas)."""
    from apps.content import fluxos
    from apps.content.models import Article

    if not pk:
        return None
    try:
        artigo = pauta.articles.filter(pk=pk).first()
    except (ValueError, ValidationError):
        return None
    if artigo is None or (artigo.thesis_json or {}).get("origem") != ORIGEM:
        return None
    return artigo if artigo.status in (*fluxos.RASCUNHOS, Article.Status.DRAFTING) else None


@transaction.atomic
def desistir(artigo) -> None:
    """Apaga o artigo que esperava o texto, e a pauta volta a como estava."""
    pauta = artigo.topic
    anterior = (artigo.thesis_json or {}).get("situacao_da_pauta")
    artigo.delete()
    if pauta is not None and anterior:
        pauta.status = anterior
        pauta.save(update_fields=["status"])


def _fontes(artigo) -> str:
    from apps.content.services import _uso_da_fonte

    partes = []
    for citacao in artigo.citations.select_related("super_chunk").order_by("rank"):
        chunk = citacao.super_chunk
        if chunk is None:
            continue
        partes.append(
            f'<fonte numero="{citacao.rank}" autores="{chunk.source_authors}" '
            f'ano="{chunk.source_year or ""}"{_uso_da_fonte(chunk)}>\n'
            f"{chunk.content}\n</fonte>"
        )
    return "\n\n".join(partes)


# A resposta inteira num bloco de codigo: copiada da tela, ela leva os ## dos
# titulos (fora do bloco, a selecao copia o texto ja formatado, sem eles).
PEDIDO_EM_BLOCO = (
    "Responda EXATAMENTE neste formato, sem nada antes, TUDO DENTRO DE UM UNICO\n"
    "BLOCO DE CODIGO (```): quem copiar leva os ## dos titulos junto."
)


def pedido(artigo) -> str:
    from apps.content.chamada import texto_da_oferta
    from apps.content.flows import _publico_padrao, dores_do_artigo
    from apps.content.prompts_iniciais import (
        AVISO_DE_DELIMITADOR,
        REGRA_DO_EMBASAMENTO,
        REGRA_DOS_LINKS,
        REGRAS_DA_CHAMADA,
    )
    from apps.editorial.services import perfil_atual, texto_do_guia

    pauta = artigo.topic
    perfil = perfil_atual()
    com_chamada = artigo.call_to_action != "none"
    guia = texto_do_guia(
        perfil,
        chave="article_outline",
        tipo_de_conteudo=artigo.content_type or getattr(perfil, "tipo_padrao", ""),
        com_convite=com_chamada,
    )
    if com_chamada:
        chamada = (
            "Chamada para a oferta do site: se UMA secao do meio tratar do problema "
            "que a oferta resolve, escreva [[CHAMADA]] sozinho numa linha logo "
            "depois dela (uma vez so, nunca na primeira secao). Se nenhuma tratar, "
            "nao escreva a marca: a chamada vai ao fim sozinha. No corpo, so a "
            "marca: o texto da chamada vai nos blocos CHAMADA DO MEIO e CHAMADA DO "
            "FIM (o site o mostra num bloco com o botao para a oferta). A do meio "
            "parte do problema da secao logo acima da marca; a do fim fecha o "
            "artigo inteiro. Chamada generica parece anuncio: ela continua a "
            f"conversa do texto.\n{REGRAS_DA_CHAMADA}\nOferta: {texto_da_oferta()}"
        )
    else:
        chamada = "Este tema esta longe da oferta do site: nao convide para ela."
    da_pesquisa = artigo.fluxo == "pesquisa"
    orientacao = (pauta.briefing if pauta else "") or ""
    if da_pesquisa and pauta is not None:
        from apps.knowledge.pesquisa import orientacao_dos_angulos

        orientacao = "\n\n".join(p for p in (orientacao, orientacao_dos_angulos(pauta)) if p)
    regras_da_pesquisa = f"\n{REGRAS_DA_PESQUISA}\n" if da_pesquisa else ""
    from apps.content.debate import bloco as bloco_do_debate

    debate = bloco_do_debate(pauta)
    debate = f"\n{debate}\n" if debate else ""
    bloco_de_pedidos = (BLOCO_DE_PEDIDOS if da_pesquisa else "") + (
        BLOCO_DA_CHAMADA if com_chamada else ""
    )
    return f"""\
Voce vai escrever um artigo de blog completo, SO com as fontes abaixo.

REGRAS FECHADAS — o texto que as quebrar sera recusado automaticamente:
- NAO pesquise na web e nao use nada fora das fontes para dados, numeros,
  estudos, datas ou nomes. Conhecimento geral so para contexto e definicao.
- {REGRA_DOS_LINKS}
- {AVISO_DE_DELIMITADOR}

{REGRA_DO_EMBASAMENTO}
{regras_da_pesquisa}
A pauta:
- Titulo de trabalho: {pauta.title if pauta else artigo.title}
- Palavra-chave: {artigo.focus_keyword or artigo.title}
- Publico: {artigo.audience or _publico_padrao(None)}
- Orientacao: {orientacao or "(sem orientacao)"}
- Dores do publico ligadas ao tema (a dor — falas reais, entre aspas):
{dores_do_artigo(artigo)}
{debate}
{guia}

{chamada}

Forma:
- Abertura de 1 a 2 paragrafos sem titulo: comece pela dor de quem le, com o
  jeito de dizer das falas acima (sem copiar nome nem detalhe de ninguem, sem
  alarmismo), e entregue a resposta cedo.
- Titulos das secoes como a pessoa pensa o problema, nao como indice.
- Titulo de cada secao numa linha propria, comecando com "SECAO: " (ex.:
  "SECAO: Onde a automacao paga a conta"). Subtitulo so se a secao tiver duas
  partes, com "SUBSECAO: ". Nada de #, nem titulo para o artigo inteiro.
- Cada paragrafo numa linha so, com uma linha em branco entre eles.
- Frases curtas. Sem "vale ressaltar", "no cenario atual", "jornada".
- Um fecho curto, sem titulo "Conclusao".
- Nao inclua perguntas frequentes nem referencias dentro do corpo: elas tem
  bloco proprio abaixo, e as referencias o sistema monta.

Fontes (numeradas; o numero e o do marcador [[FONTE_N]]):

{_fontes(artigo)}

{PEDIDO_EM_BLOCO}

TITULO SUGERIDO: 55 a 60 caracteres, com a palavra-chave
META DESCRIPTION: ate 155 caracteres
RESUMO CURTO: 2 frases
PERGUNTAS FREQUENTES:
P: pergunta que quem busca faria
R: resposta curta, sem marcador de fonte
(3 a 6 pares)
{bloco_de_pedidos}CORPO DO ARTIGO:
(o artigo em Markdown)
FIM DO ARTIGO
"""


def _corpo(resposta: str) -> tuple[str, str]:
    """(corpo, resto da resposta). O corpo vai da linha CORPO ate FIM.

    Separado dos outros blocos de proposito: um "## Perguntas frequentes" ou
    um "Nota:" dentro do artigo nao podem cortar o texto.
    """
    linhas = (resposta or "").splitlines()
    conhecido = {_chave(ROTULO_DO_CORPO): ROTULO_DO_CORPO}
    inicio = next(
        (i for i, linha in enumerate(linhas) if _como_rotulo(linha, conhecido)),
        None,
    )
    if inicio is None:
        return "", resposta or ""
    fim = len(linhas)
    for i in range(len(linhas) - 1, inicio, -1):
        if _chave(re.sub(r"[*_#`]", "", linhas[i])) == FIM_DO_CORPO:
            fim = i
            break
    primeira = _como_rotulo(linhas[inicio], conhecido)[1]
    corpo = [primeira] if primeira else []
    corpo += [linha for linha in linhas[inicio + 1 : fim] if not linha.strip().startswith("```")]
    resto = "\n".join(linhas[:inicio] + linhas[fim + 1 :])
    return "\n".join(corpo).strip(), resto


def _perguntas(bloco: str) -> list[tuple[str, str]]:
    from apps.content.faq import MAXIMO_DA_PERGUNTA, SUGERIDAS
    from apps.content.rendering import PADRAO_MARCADOR, PADRAO_URL_SOLTA

    pares, pergunta = [], None
    for linha in (bloco or "").splitlines():
        if achado := _PERGUNTA.match(linha):
            pergunta = achado.group(1).strip().strip("*").strip()
        elif (achado := _RESPOSTA.match(linha)) and pergunta:
            resposta = PADRAO_MARCADOR.sub("", achado.group(1)).strip().strip("*").strip()
            if not (PADRAO_URL_SOLTA.search(pergunta) or PADRAO_URL_SOLTA.search(resposta)):
                pares.append((pergunta[:MAXIMO_DA_PERGUNTA], resposta))
            pergunta = None
    return pares[:SUGERIDAS]


# Os titulos vem como rotulos de texto puro, que sobrevivem a qualquer jeito
# de copiar (o ## some quando se seleciona o texto ja desenhado na tela do
# chat). O ## continua aceito, para quem copia pelo botao.
_ROTULO_DE_TITULO = re.compile(
    r"^\s*[*_]*\s*(SUB)?SE[CÇ][AÃ]O\s*[*_]*\s*:\s*[*_]*\s*(.+?)\s*[*_]*\s*$",
    flags=re.IGNORECASE,
)
_ITEM_DE_LISTA = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def com_titulos_marcados(corpo: str) -> str:
    """SECAO: vira ## e SUBSECAO: vira ###; cada linha de texto e um paragrafo
    (o pedido manda um paragrafo por linha), e os itens de lista seguidos
    ficam juntos."""
    blocos: list[str] = []
    for linha in (corpo or "").splitlines():
        if not linha.strip():
            continue
        if achado := _ROTULO_DE_TITULO.match(linha):
            nivel = "###" if achado.group(1) else "##"
            blocos.append(f"{nivel} {achado.group(2).strip()}")
        elif (
            _ITEM_DE_LISTA.match(linha)
            and blocos
            and _ITEM_DE_LISTA.match(blocos[-1].splitlines()[-1])
        ):
            blocos[-1] += "\n" + linha.strip()
        else:
            blocos.append(linha.strip())
    return "\n\n".join(blocos)


def ler(resposta: str) -> dict:
    corpo, resto = _corpo(resposta)
    corpo = com_titulos_marcados(corpo)
    blocos = ler_blocos(resto, ROTULOS)
    return {
        "titulo": " ".join(blocos.get("TITULO SUGERIDO", "").split()).strip('"')[:300],
        "meta": " ".join(blocos.get("META DESCRIPTION", "").split())[:160],
        "resumo": " ".join(blocos.get("RESUMO CURTO", "").split()),
        "perguntas": _perguntas(blocos.get("PERGUNTAS FREQUENTES", "")),
        "pedidos": _pedidos(blocos.get("PEDIDOS", "")),
        "chamada": {
            "inline": _chamada(blocos.get("CHAMADA DO MEIO", "")),
            "end": _chamada(blocos.get("CHAMADA DO FIM", "")),
        },
        "corpo": corpo,
    }


def _chamada(bloco: str) -> dict:
    """{"title", "text", "button"} de um bloco de chamada (vazio: "nenhuma")."""
    campos = {}
    for linha in (bloco or "").splitlines():
        if achado := _CAMPO_DA_CHAMADA.match(linha):
            nome = achado.group(1).lower()
            campos[_CAMPOS.get(nome, nome)] = achado.group(2).strip().strip("*").strip()
    return campos


def _guardar_a_chamada(artigo, escrita: dict) -> bool:
    """O texto das chamadas escrito pela outra IA, so o que o modo usa (o meio
    so com a marca no texto), pelos mesmos limites do caminho daqui. Devolve se
    falta alguma parte (para o modelo daqui escrever)."""
    from apps.content.chamada import _limpar_texto, texto_da_oferta

    if artigo.call_to_action == "none" or not texto_da_oferta():
        artigo.call_to_action_copy = {}
        return False
    usadas = ["inline", "end"] if artigo.call_to_action == "inline" else ["end"]
    texto = {onde: _limpar_texto(escrita.get(onde)) for onde in usadas}
    artigo.call_to_action_copy = {onde: partes for onde, partes in texto.items() if partes}
    return len(artigo.call_to_action_copy) < len(usadas)


def _pedidos(bloco: str) -> list[str]:
    """As linhas do bloco PEDIDOS, sem "nenhum"."""
    from core.resposta_ia import itens

    return [item[:300] for item in itens(bloco) if item.strip().lower().rstrip(".") != "nenhum"][:5]


def _secoes(corpo: str) -> tuple[str, list[tuple[int, str, str]]]:
    """(abertura, [(nivel, titulo, texto)]) a partir dos titulos ## a ####."""
    abertura, secoes = [], []
    for linha in corpo.splitlines():
        achado = _TITULO_DE_SECAO.match(linha)
        if achado and len(achado.group(1)) == 2:
            secoes.append([2, achado.group(2).strip()[:200], []])
        elif secoes:
            secoes[-1][2].append(linha)
        else:
            abertura.append(linha)
    return "\n".join(abertura).strip(), [(n, t, "\n".join(c).strip()) for n, t, c in secoes]


@transaction.atomic
def aplicar(artigo, resposta: str):
    """Grava o texto colado. Levanta ValueError (ou LinkAlucinado) se nao serve."""
    from apps.content.chamada import reconciliar_com_o_texto, tirar_marcas
    from apps.content.faq import MARCADAS
    from apps.content.models import ArticleFaq, ArticleSection
    from apps.content.rendering import validar_saida_do_modelo
    from apps.content.services import aplicar_rascunho

    dados = ler(resposta)
    corpo = re.sub(r"^#\s+.+\n+", "", dados["corpo"])  # titulo de nivel 1 sai
    if len(corpo.split()) < 150:
        raise ValueError(
            "nao achei o CORPO DO ARTIGO na resposta (ou ele tem menos de 150 palavras)."
        )
    # A mesma trava do caminho local, antes de gravar qualquer coisa.
    validar_saida_do_modelo(corpo, max_marcadores=None)
    marcadores = {int(n) for n in re.findall(r"\[\[FONTE_(\d+)\]\]", corpo)}
    fontes = set(artigo.citations.values_list("rank", flat=True))
    if marcadores - fontes:
        raise ValueError(
            f"o texto cita fonte que nao existe no pedido: {sorted(marcadores - fontes)}."
        )

    abertura, secoes = _secoes(tirar_marcas(corpo))
    if not secoes:
        # Gravar assim daria um artigo sem titulo nenhum e sem lugar para a
        # chamada do meio: melhor recusar e dizer o que fazer.
        raise ValueError(
            "a resposta veio sem os titulos das secoes (linhas 'SECAO:'). Copie de novo "
            "pelo botao de copiar da IA, ou peca a ela para reenviar no formato pedido."
        )
    artigo.sections.all().delete()
    for ordem, (nivel, titulo, texto) in enumerate(secoes, start=1):
        ArticleSection.objects.create(
            article=artigo,
            order=ordem,
            level=nivel,
            heading=titulo,
            body_markdown=texto,
            status=ArticleSection.Status.WRITTEN,
            carries_central_idea=bool(re.search(r"\[\[FONTE_\d+\]\]", texto)),
        )

    if artigo.call_to_action == "none":
        corpo = tirar_marcas(corpo)
    else:
        corpo = reconciliar_com_o_texto(artigo, corpo)

    tese = dict(artigo.thesis_json or {})
    tese["moldura"] = {"abertura": abertura, "fecho": ""}
    if dados["titulo"]:
        tese["titulos_sugeridos"] = [dados["titulo"]]
    if dados["pedidos"]:
        # O que a outra IA pediu do texto completo: aparece na revisao.
        tese["pedidos"] = dados["pedidos"]
    artigo.thesis_json = tese
    artigo.meta_description = dados["meta"]
    artigo.excerpt = dados["resumo"]
    falta_chamada = _guardar_a_chamada(artigo, dados["chamada"])
    artigo.save()
    if falta_chamada:
        # A outra IA nao escreveu (ou passou do limite): o modelo daqui escreve.
        from apps.content.tasks import escrever_chamada

        pk = str(artigo.pk)
        transaction.on_commit(lambda: escrever_chamada.delay(pk))

    if not artigo.faq.exists():
        for posicao, (pergunta, texto) in enumerate(dados["perguntas"], start=1):
            ArticleFaq.objects.create(
                article=artigo,
                order=posicao,
                question=pergunta,
                answer=texto,
                is_selected=posicao <= MARCADAS,
            )
    resultado = aplicar_rascunho(artigo, corpo)
    from apps.content.flows import arquivar_substituido

    # Refeito por aqui: o rascunho antigo sai de cena agora que o texto chegou.
    arquivar_substituido((artigo.thesis_json or {}).get("substitui"), artigo)
    return resultado
