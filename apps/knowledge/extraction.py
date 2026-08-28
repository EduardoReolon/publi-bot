"""Converte o arquivo enviado em Markdown.

Dois caminhos, e a diferenca entre eles importa.

O caminho **bom** e o Docling, rodando na maquina com GPU (ADR-0007). Ele faz
analise de layout: entende coluna dupla, tabela, cabecalho e nota de rodape, e
devolve Markdown estruturado. E o que um artigo cientifico de verdade exige.

O caminho **de emergencia** roda aqui mesmo, sem GPU, e serve para o sistema
ser testavel antes de a maquina de inferencia existir. Para `.txt` e `.md` nao
ha o que converter. Para PDF ele usa o `pypdf`, que devolve a camada de texto
**sem interpretar a estrutura da pagina**: nao distingue coluna, cabecalho,
rodape, legenda nem tabela, e a ordem de leitura nao e garantida. Num PDF
digitalizado nao ha camada de texto nenhuma.

O metodo usado fica gravado no proprio documento e a tela de curadoria avisa em
destaque quando foi o caminho fraco — sem isso a degradacao seria silenciosa, e
e essa a parte perigosa: o texto parece correto sem estar.

O caminho de emergencia devolve **texto puro, nunca Markdown**. A distincao
parece formal e nao e: num artigo real o simbolo (c) foi decodificado como `#`,
e interpretar aquilo como Markdown fez a linha de copyright virar titulo de
secao e titulo da obra. Quem consome isto olha `Document.texto_e_markdown`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    # Metadados que o proprio arquivo declara (o dicionario de Info do PDF).
    # Sao mais confiaveis que qualquer heuristica sobre o texto extraido:
    # foram gravados pelo editor, e nao adivinhados a partir do layout.
    metadados: dict = field(default_factory=dict)


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
    """Converte o arquivo do documento em Markdown.

    Prefere sempre o Docling. So cai no caminho local quando nao ha conexao de
    conversao cadastrada E `PERMITIR_EXTRACAO_LOCAL` esta ligado — em producao
    ele vem desligado, para ninguem indexar por engano texto lido sem analise
    de layout.
    """
    from django.conf import settings

    conexao = conexao_de_conversao()
    if conexao is not None:
        return _extrair_com_docling(document, conexao, timeout=timeout)

    nome = (document.nome_do_arquivo or "").lower()

    if nome.endswith(".pdf") and not settings.PERMITIR_EXTRACAO_LOCAL:
        raise ExtracaoIndisponivel(
            "nenhuma conexao de conversao (Docling) esta cadastrada, e a "
            "extracao local de PDF esta desligada. Cadastre a conexao em "
            "Inferencia, ou ligue PERMITIR_EXTRACAO_LOCAL para aceitar texto "
            "lido sem analise de layout."
        )

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
        texto, metadados = _pdf_para_texto(bruto)
        return ResultadoDaExtracao(markdown=texto, metodo="pypdf", metadados=metadados)

    raise ExtracaoIndisponivel(
        f"nao sei converter {nome!r} sem o worker de conversao. "
        f"Envie .txt ou .md, ou cadastre uma conexao do tipo Docling."
    )


def _pdf_para_texto(bruto: bytes) -> tuple[str, dict]:
    import io

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        # Dependencia acrescentada depois da primeira instalacao. O
        # `ModuleNotFoundError` cru nao diz o que fazer.
        raise ExtracaoIndisponivel(
            "o pypdf nao esta instalado neste ambiente. Rode "
            "`pip install -r requirements.txt` e reinicie o worker — ele carrega "
            "as bibliotecas na hora em que sobe, entao instalar sem reiniciar "
            "nao muda nada."
        ) from exc

    leitor = PdfReader(io.BytesIO(bruto))
    paginas = [(pagina.extract_text() or "").strip() for pagina in leitor.pages]
    texto = "\n\n".join(p for p in paginas if p)
    # `getattr` e nao acesso direto: um PDF pode nao trazer dicionario de Info,
    # e o caminho de emergencia nao pode quebrar por causa de um campo opcional
    # que so serve para pre-preencher a tela.
    try:
        info = dict(getattr(leitor, "metadata", None) or {})
    except Exception:
        info = {}
    metadados = {str(chave): str(valor) for chave, valor in info.items() if valor}

    if not texto.strip():
        # Digitalizacao sem camada de texto. Dizer isso e melhor que devolver
        # vazio e deixar a curadoria com uma tela em branco sem explicacao.
        raise ExtracaoIndisponivel(
            "o PDF nao tem camada de texto (provavelmente digitalizado). "
            "A conversao exige o worker com Docling, que faz OCR."
        )
    return texto, metadados
