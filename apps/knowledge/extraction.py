"""Converte o arquivo enviado em Markdown.

Dois caminhos, e a diferenca entre eles importa.

O caminho **bom** e o Docling, rodando na maquina com GPU (ADR-0007). Ele faz
analise de layout: entende coluna dupla, tabela, cabecalho e nota de rodape, e
devolve Markdown estruturado. E o que um artigo cientifico de verdade exige.

O caminho **de emergencia** roda aqui mesmo, sem GPU, e serve para o sistema
ser testavel antes de a maquina de inferencia existir. Para `.txt` e `.md` nao
ha o que converter. Para PDF ele usa o `pypdf`, que extrai a camada de texto na
ordem em que ela esta no arquivo — em PDF de coluna dupla isso embaralha as
colunas, e num PDF digitalizado nao ha camada de texto nenhuma. O resultado vai
para curadoria humana marcado com o metodo usado, para ninguem confundir um
com o outro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from apps.inference.models import InferenceConnection
from apps.inference.security import decifrar_chave

logger = logging.getLogger("publibot.knowledge")

EXTENSOES_DE_TEXTO = {".txt", ".md", ".markdown"}


class ExtracaoIndisponivel(RuntimeError):
    """Nao ha como converter este arquivo com o que esta configurado."""


class ConversorOcupado(RuntimeError):
    """O worker de conversao ja esta convertendo outra coisa.

    Nao e erro: a maquina processa um documento por vez de proposito (uma placa
    de 8 GB nao comporta duas). Quem chama deve adiar, nao falhar.
    """


@dataclass(frozen=True)
class ResultadoDaExtracao:
    markdown: str
    metodo: str
    duracao_ms: int = 0


def conexao_de_conversao() -> InferenceConnection | None:
    return (
        InferenceConnection.objects.filter(
            is_active=True,
            kind=InferenceConnection.Kind.DOCLING,
        )
        .order_by("created_at")
        .first()
    )


def extrair_markdown(document, *, timeout: float = 600.0) -> ResultadoDaExtracao:
    """Converte o arquivo do documento em Markdown."""
    conexao = conexao_de_conversao()
    if conexao is not None:
        return _extrair_com_docling(document, conexao, timeout=timeout)
    return _extrair_localmente(document)


def _extrair_com_docling(document, conexao, *, timeout: float) -> ResultadoDaExtracao:
    """Envia o arquivo ao worker de conversao.

    O `X-Expected-Sha256` nao e redundante com o TLS: ele garante que o worker
    converteu o arquivo que este documento representa, e nao outro. Sem isso,
    um erro de troca de arquivo produziria Markdown de um documento que ninguem
    pediu, e o texto seguiria para o acervo como se fosse do artigo certo.
    """
    segredo = decifrar_chave(conexao) or ""
    if not segredo:
        raise ExtracaoIndisponivel(
            f"a conexao de conversao {conexao.name!r} nao tem segredo cadastrado."
        )

    document.original_file.open("rb")
    try:
        arquivos = {"file": (document.nome_do_arquivo, document.original_file.read())}
    finally:
        document.original_file.close()

    try:
        resposta = httpx.post(
            f"{conexao.base_url.rstrip('/')}/parse/",
            files=arquivos,
            headers={
                "X-Worker-Secret": segredo,
                "X-Expected-Sha256": document.file_sha256,
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ConversorOcupado(f"worker de conversao inalcancavel: {exc}") from exc

    if resposta.status_code == 503:
        raise ConversorOcupado("ja ha uma conversao em curso no worker.")

    if resposta.status_code == 422:
        # O worker conferiu o sha256 e nao bateu. Repetir nao adianta.
        raise ExtracaoIndisponivel(
            "o arquivo recebido pelo worker nao confere com o registrado. Reenvie o documento."
        )

    if resposta.status_code >= 400:
        raise ExtracaoIndisponivel(
            f"worker de conversao respondeu {resposta.status_code}: {resposta.text[:300]}"
        )

    dados = resposta.json()
    return ResultadoDaExtracao(
        markdown=dados.get("markdown", ""),
        metodo="docling",
        duracao_ms=int(dados.get("duration_ms", 0)),
    )


def _extrair_localmente(document) -> ResultadoDaExtracao:
    nome = (document.nome_do_arquivo or "").lower()

    document.original_file.open("rb")
    try:
        bruto = document.original_file.read()
    finally:
        document.original_file.close()

    if any(nome.endswith(ext) for ext in EXTENSOES_DE_TEXTO):
        return ResultadoDaExtracao(markdown=bruto.decode("utf-8", errors="replace"), metodo="texto")

    if nome.endswith(".pdf"):
        return ResultadoDaExtracao(markdown=_pdf_para_texto(bruto), metodo="pypdf")

    raise ExtracaoIndisponivel(
        f"nao sei converter {nome!r} sem o worker de conversao. "
        f"Envie .txt ou .md, ou cadastre uma conexao do tipo Docling."
    )


def _pdf_para_texto(bruto: bytes) -> str:
    import io

    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(bruto))
    paginas = [(pagina.extract_text() or "").strip() for pagina in leitor.pages]
    texto = "\n\n".join(p for p in paginas if p)

    if not texto.strip():
        # Digitalizacao sem camada de texto. Dizer isso e melhor que devolver
        # vazio e deixar a curadoria com uma tela em branco sem explicacao.
        raise ExtracaoIndisponivel(
            "o PDF nao tem camada de texto (provavelmente digitalizado). "
            "A conversao exige o worker com Docling, que faz OCR."
        )
    return texto
