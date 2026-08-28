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

    sugestoes = sugerir_metadados(resultado.markdown)

    document.markdown_full = resultado.markdown
    document.status = Document.Status.PENDING_CURATION
    document.failure_reason = ""
    # So preenche o que esta vazio: se alguem ja corrigiu a mao, a sugestao
    # automatica nao pode sobrescrever.
    if not document.title:
        document.title = sugestoes["title"]
    if not document.authors:
        document.authors = sugestoes["authors"]
    if not document.year:
        document.year = sugestoes["year"]
    if not document.doi and sugestoes["doi"]:
        # O DOI e unico no tenant; um segundo documento com o mesmo DOI
        # quebraria a gravacao inteira por causa de uma sugestao.
        if not Document.objects.filter(doi=sugestoes["doi"]).exclude(pk=document.pk).exists():
            document.doi = sugestoes["doi"]
    # AUTO e a procedencia, nao um julgamento de qualidade: vira MANUAL
    # quando a curadoria confirmar os campos.
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


def sugerir_metadados(markdown: str) -> dict:
    """Le o cabecalho do Markdown e propoe titulo, autores, ano e DOI.

    Heuristica simples e assumida como tal: o primeiro cabecalho `#` ou a
    primeira linha longa e o titulo; a linha seguinte com virgulas e a lista de
    autores. `campos_encontrados` diz a tela de curadoria o quanto insistir na
    conferencia.
    """
    linhas = [linha.strip() for linha in markdown.splitlines()]
    cabecalho = [linha for linha in linhas[:60] if linha]

    titulo = ""
    for linha in cabecalho:
        if linha.startswith("#"):
            titulo = linha.lstrip("#").strip()
            break
    if not titulo:
        for linha in cabecalho:
            if 20 <= len(linha) <= 300:
                titulo = linha
                break

    autores = ""
    if titulo:
        # A linha de autores costuma ser a primeira com virgulas logo abaixo do
        # titulo. Procurar no documento inteiro pegaria qualquer enumeracao.
        try:
            inicio = next(i for i, linha in enumerate(cabecalho) if titulo in linha)
        except StopIteration:
            inicio = 0
        for linha in cabecalho[inicio + 1 : inicio + 6]:
            if "," in linha and len(linha) < 300 and not linha.startswith("#"):
                autores = formatar_autores([p.strip() for p in linha.split(",") if p.strip()])
                break

    ano = None
    achados = PADRAO_DE_ANO.findall("\n".join(cabecalho))
    if achados:
        # O mais recente do cabecalho: um artigo cita anos antigos, mas o seu
        # proprio ano tende a ser o maior ali.
        ano = max(int(a) for a in achados)

    doi = extrair_doi(markdown[:5000]) or ""

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
