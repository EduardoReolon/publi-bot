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

from apps.inference.leases import SemCapacidade, reserva
from apps.inference.models import InferenceConnection
from apps.inference.security import decifrar_chave

logger = logging.getLogger("publibot.knowledge")

EXTENSOES_DE_TEXTO = {".txt", ".md", ".markdown"}
EXTENSOES_WEB = (".html", ".htm")


class ExtracaoIndisponivel(RuntimeError):
    """Nao ha como converter este arquivo com o que esta configurado."""


class ConversorOcupado(RuntimeError):
    """A placa esta com outro trabalho — que pode nem ser uma conversao.

    Nao e erro: o worker arbitra texto, imagem e conversao com um lock so, e
    devolve 503 a quem nao pegou a vez. Quem chama deve adiar, nao falhar.

    `retry_after` e o que o worker calculou a partir do que esta rodando.
    Ignora-lo seria voltar cedo (outra recusa) ou tarde (placa parada).
    """

    def __init__(self, mensagem: str, *, retry_after: int | None = None, code: str = ""):
        super().__init__(mensagem)
        self.retry_after = retry_after
        self.code = code


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

    from apps.knowledge.estruturados import EXTENSOES_ESTRUTURADAS

    nome_em_minusculas = (document.nome_do_arquivo or "").lower()
    # Formatos que declaram a propria estrutura, e paginas web, sao lidos aqui
    # mesmo: nao ha layout a adivinhar, e a placa fica para o PDF.
    if nome_em_minusculas.endswith(EXTENSOES_ESTRUTURADAS):
        return _extrair_estruturado(document)
    if nome_em_minusculas.endswith(EXTENSOES_WEB):
        return _extrair_pagina(document)

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
    document.original_file.open("rb")
    try:
        conteudo = document.original_file.read()
    finally:
        document.original_file.close()

    return converter_no_worker(
        conexao,
        nome=document.nome_do_arquivo,
        conteudo=conteudo,
        sha256=document.file_sha256,
        chave_da_reserva=f"conversao:{document.pk}",
        timeout=timeout,
    )


def converter_no_worker(
    conexao,
    *,
    nome: str,
    conteudo: bytes,
    sha256: str,
    chave_da_reserva: str,
    timeout: float,
) -> ResultadoDaExtracao:
    """O POST em si, separado do documento que o originou.

    Existe separado por um motivo so: `manage.py conferir_worker` precisa
    exercitar ESTE caminho — a reserva, os cabecalhos, a leitura de cada
    status — e nao uma copia dele. Uma conferencia que roda um codigo
    parecido confere o codigo parecido.
    """
    segredo = decifrar_chave(conexao) or ""
    if not segredo:
        raise ExtracaoIndisponivel(
            f"a conexao de conversao {conexao.name!r} nao tem segredo cadastrado."
        )

    arquivos = {"file": (nome, conteudo)}

    # A conversao reserva capacidade, como a geracao de texto.
    #
    # O worker ja recusa a segunda chamada com 503, mas isso protege so contra
    # DUAS CONVERSOES. O caso perigoso e outro: o Docling convertendo enquanto
    # o Ollama gera texto, na mesma placa. Sao dois servicos diferentes, cada
    # um so sabe de si, e nenhum dos dois recusaria nada — a VRAM estoura e o
    # processo cai para CPU em silencio, dezenas de vezes mais lento, sem erro.
    #
    # A reserva e por maquina (`leases.vizinhas_de_hardware`), entao ela cobre
    # exatamente essa combinacao.
    try:
        with reserva(conexao, owner_key=chave_da_reserva):
            resposta = httpx.post(
                f"{conexao.base_url.rstrip('/')}/parse/",
                files=arquivos,
                headers={
                    "X-Worker-Secret": segredo,
                    "X-Expected-Sha256": sha256,
                },
                timeout=timeout,
            )
    except SemCapacidade as exc:
        # Nao e falha: a maquina esta ocupada. `ConversorOcupado` ja e tratado
        # como adiamento pelo fluxo, e adiar nao gasta tentativa.
        raise ConversorOcupado(f"a maquina de conversao esta ocupada: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ConversorOcupado(f"worker de conversao inalcancavel: {exc}") from exc

    if resposta.status_code == 503:
        # Nao necessariamente outra conversao: o lock do worker e um so, e o
        # ocupante costuma ser a geracao de texto ou de imagem. Dizer
        # "ja ha uma conversao em curso" mandava procurar no lugar errado.
        from apps.inference.providers.openai_compatible import (
            CODIGOS_SEM_VOLTA,
            _codigo_do_erro,
            _retry_after,
        )

        codigo = _codigo_do_erro(resposta)

        if codigo in CODIGOS_SEM_VOLTA:
            raise ExtracaoIndisponivel(
                "o worker desistiu de converter por tempo. Repetir o mesmo "
                "arquivo daria o mesmo resultado — ele e grande demais para o "
                "orcamento da maquina."
            )

        detalhe = _mensagem_do_erro(resposta) or "a placa esta com outro trabalho"
        raise ConversorOcupado(
            f"worker ocupado: {detalhe}", retry_after=_retry_after(resposta), code=codigo
        )

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


def _ler_arquivo(document) -> bytes:
    document.original_file.open("rb")
    try:
        return document.original_file.read()
    finally:
        document.original_file.close()


def _extrair_estruturado(document) -> ResultadoDaExtracao:
    from apps.knowledge.estruturados import converter

    try:
        markdown = converter(document.nome_do_arquivo, _ler_arquivo(document))
    except Exception as exc:
        raise ExtracaoIndisponivel(f"nao foi possivel ler o arquivo: {exc}") from exc
    if not markdown.strip():
        raise ExtracaoIndisponivel("o arquivo nao tem texto.")
    return ResultadoDaExtracao(markdown=markdown, metodo="estruturado")


def _extrair_pagina(document) -> ResultadoDaExtracao:
    """Pagina web gravada: texto principal, com os metadados que ela declara.

    Os metadados vao nas MESMAS chaves do dicionario de Info do PDF (`/Title`,
    `/Author`), porque e isso que a sugestao de metadados ja sabe ler. A data
    vai em `data`, e o passo de conversao a grava como data de publicacao.
    """
    from apps.knowledge.web import PaginaIndisponivel, extrair_pagina

    try:
        pagina = extrair_pagina(_ler_arquivo(document), url=document.source_url)
    except PaginaIndisponivel as exc:
        raise ExtracaoIndisponivel(str(exc)) from exc

    metadados = {"/Title": pagina.titulo, "/Author": pagina.autor, "site": pagina.site}
    if pagina.data:
        metadados["data"] = pagina.data.isoformat()
    titulo = f"# {pagina.titulo}\n\n" if pagina.titulo else ""
    return ResultadoDaExtracao(
        markdown=f"{titulo}{pagina.markdown}",
        metodo="web",
        metadados={k: v for k, v in metadados.items() if v},
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


def _mensagem_do_erro(resposta) -> str:
    """O `error.message` do corpo, quando ha um. Nunca levanta."""
    try:
        corpo = resposta.json()
    except ValueError:
        return ""
    erro = corpo.get("error") if isinstance(corpo, dict) else None
    mensagem = erro.get("message") if isinstance(erro, dict) else None
    return mensagem if isinstance(mensagem, str) else ""
