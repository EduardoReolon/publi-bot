"""Fluxo de ingestao de documento, registrado no orquestrador.

O documento chega, e convertido em Markdown e para em `pending_curation`. A
parada e deliberada e e o coracao do ADR sobre curadoria: o sistema nao joga
Markdown convertido direto no indice vetorial. Um humano confirma titulo,
autores, ano e URL de origem — que sustentam a citacao publicada — e escolhe
qual trecho representa o documento.

Extrair metadados automaticamente aqui serve para **pre-preencher** a tela de
curadoria, nunca para dispensar a conferencia. Cabecalho de PDF e desalinhado
com frequencia, e um ano errado vira uma citacao errada no site do cliente.
"""

from __future__ import annotations

import logging
import re

from apps.knowledge.extraction import ConversorOcupado, extrair_markdown
from apps.knowledge.models import Document
from apps.knowledge.services import extrair_doi, formatar_autores
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import Fluxo, Passo, PassoAdiado, registrar_fluxo

logger = logging.getLogger("publibot.knowledge")

# Um ano de publicacao plausivel. Sem a faixa, "Figura 1988x2" vira ano.
PADRAO_DE_ANO = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# Digito de filiacao colado ao nome: "Mikhail V . Chester2", "Yeowon Kim1".
PADRAO_DE_FILIACAO = re.compile(r"\s*\d+\s*$")

# O mesmo marcador visto no meio da linha, que denuncia uma lista de autores:
# "Yeowon Kim1 & Daniel A. Eisenberg 2 &".
PADRAO_DE_AUTOR_COM_FILIACAO = re.compile(r"[a-zA-Z]\s?\d\b")

# Inicial abreviada de nome proprio: "Jason K. Levy".
PADRAO_DE_INICIAL = re.compile(r"\b[A-Z]\.")

# O "and"/"e" que antecede o ultimo autor da lista.
PADRAO_DE_CONJUNCAO = re.compile(r",\s+(and|e)\s+\S", re.IGNORECASE)

# Palavra de verdade, para distinguir titulo de codigo de producao.
PADRAO_DE_PALAVRA = re.compile(r"[A-Za-z\u00c0-\u024f]{3,}")
MINIMO_DE_PALAVRAS_NO_TITULO = 4

# Marcador de nota de rodape colado no fim do titulo: "...URBAN WATERSHEDS1".
PADRAO_DE_NOTA_NO_TITULO = re.compile(r"(?<=[a-z\u00e0-\u00ff])\d{1,2}$|(?<=[A-Z])\d{1,2}$")


def passo_converter(job: GenerationJob) -> dict:
    """Converte o arquivo em Markdown e pre-preenche o que der.

    O conversor ocupado vira `PassoAdiado`, nao falha: a maquina de conversao
    processa um documento por vez de proposito, e esperar a vez e o
    comportamento correto.
    """
    document = Document.objects.filter(pk=job.target_object_id).first()
    if document is None:
        raise ValueError(f"documento {job.target_object_id} nao existe")

    Document.objects.filter(pk=document.pk).update(status=Document.Status.PARSING)

    try:
        resultado = extrair_markdown(document)
    except ConversorOcupado as exc:
        # Adiar devolve o documento a fila; deixa-lo em PARSING faria a lista
        # mostrar "convertendo" para algo que nao esta sendo convertido.
        Document.objects.filter(pk=document.pk).update(status=Document.Status.QUEUED)
        raise PassoAdiado(str(exc), tentar_em_segundos=180) from exc
    except Exception as exc:
        # O motivo vai para o proprio documento: e la que quem enviou o arquivo
        # vai procurar, e nao no registro do trabalho.
        Document.objects.filter(pk=document.pk).update(
            status=Document.Status.FAILED, failure_reason=str(exc)[:2000]
        )
        raise

    sugestoes = sugerir_metadados(
        resultado.markdown,
        metadados_do_arquivo=resultado.metadados,
        e_markdown=resultado.metodo != Document.ExtractionMethod.PYPDF,
    )

    document.markdown_full = resultado.markdown
    document.extraction_method = resultado.metodo
    document.status = Document.Status.PENDING_CURATION
    document.failure_reason = ""

    # O que uma PESSOA confirmou e intocavel. O que a extracao anterior sugeriu,
    # nao: quem reconverte esta justamente dizendo que o resultado anterior
    # estava errado, e proteger a sugestao velha deixaria no lugar o valor que
    # motivou a reconversao. Foi o que aconteceu com um artigo real cujos
    # "autores" eram a primeira linha do resumo — reconverter nao consertava.
    conferido = document.metadata_confidence == Document.MetadataConfidence.MANUAL

    def _manter(valor) -> bool:
        return bool(valor) and conferido

    if not _manter(document.title):
        document.title = sugestoes["title"] or document.title
    if not _manter(document.authors):
        document.authors = sugestoes["authors"] or document.authors
    if not _manter(document.year):
        document.year = sugestoes["year"] or document.year
    if not _manter(document.doi) and sugestoes["doi"]:
        # O DOI e unico no tenant; um segundo documento com o mesmo DOI
        # quebraria a gravacao inteira por causa de uma sugestao.
        if not Document.objects.filter(doi=sugestoes["doi"]).exclude(pk=document.pk).exists():
            document.doi = sugestoes["doi"]

    # AUTO e a procedencia, nao um julgamento de qualidade: vira MANUAL quando a
    # curadoria confirmar os campos. Um documento ja conferido continua
    # conferido — o texto foi reconvertido, a decisao humana sobre a
    # identificacao da obra nao.
    if not conferido:
        document.metadata_confidence = Document.MetadataConfidence.AUTO
    document.save()

    logger.info(
        "Documento %s convertido por %s (%s caracteres).",
        document.pk,
        resultado.metodo,
        len(resultado.markdown),
    )
    return {
        "metodo": resultado.metodo,
        "caracteres": len(resultado.markdown),
        "duracao_ms": resultado.duracao_ms,
        "sugestoes": sugestoes,
    }


def _titulo_do_arquivo(metadados: dict) -> str:
    """O titulo que o proprio PDF declara, quando serve.

    Frequentemente nao serve: o campo guarda o nome do arquivo ou o codigo de
    producao da grafica. Num artigo do JAWRA veio `jawr_027 346..358` — revista,
    artigo e faixa de paginas — e o campo tem tamanho plausivel e nao termina em
    `.pdf`, entao passava por qualquer checagem de forma.

    O que separa os dois e o conteudo: titulo de artigo e feito de palavras.
    """
    bruto = (metadados.get("/Title") or "").strip()
    if not 10 <= len(bruto) <= 500:
        return ""
    if bruto.lower().endswith((".pdf", ".doc", ".docx", ".tex", ".indd")):
        return ""
    if len(PADRAO_DE_PALAVRA.findall(bruto)) < MINIMO_DE_PALAVRAS_NO_TITULO:
        return ""
    return bruto


def _e_caixa_alta(linha: str) -> bool:
    """Linha sem nenhuma letra minuscula, ignorando digitos e pontuacao."""
    letras = [c for c in linha if c.isalpha()]
    return bool(letras) and all(c.isupper() for c in letras)


def _parece_linha_de_autores(linha: str) -> bool:
    """Onde o titulo termina.

    A checagem de caixa alta vem primeiro e nao e detalhe. Titulo impresso em
    versal carrega o marcador de nota de rodape colado na ultima palavra
    (`...URBAN WATERSHEDS1`), que tem exatamente a forma do marcador de
    filiacao de autor (`Chester2`). Sem distinguir os dois, o titulo era cortado
    na primeira linha. Nome de pessoa vem em caixa mista; titulo em versal, nao.
    """
    if linha.startswith(("*", "†", "‡")):
        return True
    if _e_caixa_alta(linha):
        return False
    if "&" in linha:
        return True
    if PADRAO_DE_AUTOR_COM_FILIACAO.search(linha):
        return True
    # Virgula sozinha nao basta: titulo tambem tem virgula. O que denuncia a
    # lista de autores e a inicial abreviada ("Jason K. Levy") ou o "and"/"e"
    # antes do ultimo nome.
    if "," in linha:
        return bool(PADRAO_DE_INICIAL.search(linha)) or bool(PADRAO_DE_CONJUNCAO.search(linha))
    return False


def _titulo_do_texto(cabecalho: list[str]) -> str:
    """As primeiras linhas ate onde comecam os autores.

    Pegar so a primeira linha devolvia titulo cortado ao meio — "Fail-safe and
    safe-to-fail adaptation: decision-making", sem "for urban flooding under
    climate change". Um titulo truncado vira o texto do link publicado e o
    prefixo de contexto de todo trecho vetorizado do documento.
    """
    partes: list[str] = []
    for linha in cabecalho[:8]:
        if _parece_linha_de_autores(linha) or linha.startswith("#"):
            break
        if not 3 <= len(linha) <= 300:
            break
        # Linha que termina em ponto e frase, nao continuacao de titulo. Parar
        # ANTES de junta-la: sem isso, num documento em que o titulo nao e
        # seguido de autores, o primeiro paragrafo entrava no titulo.
        if partes and linha.endswith((".", "?", "!")):
            break
        partes.append(linha)
        if linha.endswith((".", "?", "!")) or len(" ".join(partes)) > 300:
            break

    titulo = PADRAO_DE_NOTA_NO_TITULO.sub("", " ".join(partes).strip()).strip()
    return titulo if len(titulo) >= 10 else ""


def _autores_do_texto(cabecalho: list[str], titulo: str) -> str:
    """A linha de autores logo abaixo do titulo.

    Separa por `&` alem de virgula porque e assim que varias revistas listam
    (`Yeowon Kim1 & Daniel A. Eisenberg 2 &`), e o digito de filiacao colado ao
    sobrenome sai fora — ele iria para o texto-ancora do link publicado.
    """
    if not titulo:
        return ""
    try:
        inicio = next(i for i, linha in enumerate(cabecalho) if titulo[:40] in linha)
    except StopIteration:
        inicio = 0

    janela = cabecalho[inicio + 1 : inicio + 8]
    for posicao, linha in enumerate(janela):
        if linha.startswith("#") or len(linha) >= 300:
            continue
        separador = "&" if "&" in linha else ("," if "," in linha else "")
        if not separador:
            continue

        # A lista de autores quebra em varias linhas, e cada uma termina no
        # proprio separador ("... Eisenberg 2 &"). Ler so a primeira linha dava
        # dois nomes de um artigo com seis — e "A e B" no lugar de "A et al.",
        # que e uma atribuicao de autoria errada no site do cliente.
        completa = linha
        for continuacao in janela[posicao + 1 :]:
            if not completa.rstrip().endswith(separador):
                break
            completa = f"{completa} {continuacao}"

        nomes = [PADRAO_DE_FILIACAO.sub("", p).strip() for p in completa.split(separador)]
        nomes = [n for n in nomes if len(n) > 2]
        if nomes:
            return formatar_autores(nomes)
    return ""


def sugerir_metadados(
    markdown: str, *, metadados_do_arquivo: dict | None = None, e_markdown: bool = True
) -> dict:
    """Propoe titulo, autores, ano e DOI para a tela de curadoria conferir.

    Duas fontes, nesta ordem. Primeiro o que o **arquivo declara** — o
    dicionario de Info do PDF, gravado pelo editor. Depois a heuristica sobre o
    texto, que e adivinhacao e esta assumida como tal.

    `e_markdown` importa: so o Docling exporta cabecalho `#`. Procurar `#` em
    texto puro elegeu, num artigo real, a linha de copyright como titulo da
    obra — o simbolo (c) tinha sido decodificado como `#`.
    """
    metadados_do_arquivo = metadados_do_arquivo or {}
    linhas = [linha.strip() for linha in markdown.splitlines()]
    cabecalho = [linha for linha in linhas[:60] if linha]

    titulo = _titulo_do_arquivo(metadados_do_arquivo)
    if not titulo and e_markdown:
        for linha in cabecalho:
            if linha.startswith("#"):
                titulo = linha.lstrip("#").strip()
                break
    if not titulo:
        titulo = _titulo_do_texto(cabecalho)

    autores = (metadados_do_arquivo.get("/Author") or "").strip()
    do_texto = _autores_do_texto(cabecalho, titulo)
    # O `/Author` do PDF costuma trazer so o primeiro nome da lista. Quando o
    # texto rende mais nomes, ele ganha: e a lista completa que sustenta o
    # "et al." da citacao.
    if do_texto and (not autores or "et al." in do_texto or " e " in do_texto):
        autores = do_texto

    ano = None
    achados = PADRAO_DE_ANO.findall("\n".join(cabecalho))
    if achados:
        # O mais recente do cabecalho: um artigo cita anos antigos, mas o seu
        # proprio ano tende a ser o maior ali.
        ano = max(int(a) for a in achados)

    doi = extrair_doi(markdown[:5000]) or ""
    if not doi:
        # Varias editoras gravam o DOI no /Subject ("Climatic Change,
        # doi:10.1007/s10584-017-2090-1").
        doi = extrair_doi(" ".join(metadados_do_arquivo.values())) or ""

    return {
        "title": titulo[:500],
        "authors": autores[:300],
        "year": ano,
        "doi": doi,
        # Quantos dos quatro campos a heuristica achou. A tela de curadoria usa
        # isto para insistir mais quando achou pouco — nao para dispensar a
        # conferencia, que e obrigatoria em qualquer caso.
        "campos_encontrados": sum(1 for v in (titulo, autores, ano, doi) if v),
    }


registrar_fluxo(
    Fluxo(
        kind=GenerationJob.Kind.PDF_INGESTION,
        passos=[Passo(numero=0, nome="converter em markdown", executar=passo_converter)],
    )
)
