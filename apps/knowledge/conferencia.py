"""Conferencia da extracao: o que ela propos contra o que era certo.

Existe porque as heuristicas de `blocos.py` e `flows.py` **nao aprendem
sozinhas**. Cada regra delas saiu de alguem olhar um PDF real e achar o que
separa o titulo verdadeiro do impostor. Isso funciona, mas tem um custo: mexer
numa regra para consertar um artigo pode quebrar outro que ja funcionava, e sem
um ponto de comparacao ninguem descobre isso ate o proximo documento sair torto.

Duas fontes de caso, e as duas sao de graca:

**O acervo.** A curadoria ja e um gabarito. Se a extracao propos um titulo e a
pessoa gravou outro, aquele documento e um caso de falha rotulado — e ninguem
precisou reportar nada.

**Uma pasta de PDFs.** Para iterar sem mexer no banco: roda a extracao, compara
com o esperado gravado ao lado, e diz o que mudou. E o teste de regressao das
heuristicas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CAMPOS_CONFERIDOS = ("title", "authors", "year", "doi")


@dataclass
class Divergencia:
    campo: str
    sugerido: object
    corrigido: object


@dataclass
class CasoDoAcervo:
    """Um documento onde a curadoria discordou da extracao."""

    documento_id: str
    titulo: str
    metodo: str
    divergencias: list[Divergencia] = field(default_factory=list)


def comparar_com_a_curadoria(document) -> list[Divergencia]:
    """Campos que uma pessoa mudou em relacao ao que a extracao propos.

    So faz sentido depois da conferencia humana: antes dela os campos SAO a
    sugestao, e comparar devolveria sempre igual.
    """
    from apps.knowledge.models import Document

    sugerido = document.metadata_suggested or {}
    if not sugerido:
        return []
    if document.metadata_confidence != Document.MetadataConfidence.MANUAL:
        return []

    divergencias = []
    for campo in CAMPOS_CONFERIDOS:
        atual = getattr(document, campo, None)
        proposto = sugerido.get(campo)
        # Normaliza vazio: "" e None sao a mesma ausencia para quem confere.
        if (atual or None) != (proposto or None):
            divergencias.append(Divergencia(campo=campo, sugerido=proposto, corrigido=atual))
    return divergencias


def casos_do_acervo(limite: int = 100) -> list[CasoDoAcervo]:
    """Documentos ja curados em que a extracao errou algum campo."""
    from apps.knowledge.models import Document

    casos = []
    consulta = Document.objects.filter(
        metadata_confidence=Document.MetadataConfidence.MANUAL
    ).exclude(metadata_suggested={})

    for documento in consulta.order_by("-reviewed_at")[:limite]:
        divergencias = comparar_com_a_curadoria(documento)
        if divergencias:
            casos.append(
                CasoDoAcervo(
                    documento_id=str(documento.pk),
                    titulo=documento.title or documento.nome_do_arquivo,
                    metodo=documento.extraction_method,
                    divergencias=divergencias,
                )
            )
    return casos


def taxa_de_acerto() -> dict:
    """Quantos campos a extracao acertou entre os documentos ja conferidos.

    Numero para acompanhar, nao para comemorar: ele so cobre documentos que
    passaram pela curadoria, e a curadoria e obrigatoria justamente porque a
    extracao nao e confiavel sozinha.
    """
    from apps.knowledge.models import Document

    conferidos = list(
        Document.objects.filter(metadata_confidence=Document.MetadataConfidence.MANUAL).exclude(
            metadata_suggested={}
        )
    )
    if not conferidos:
        return {"documentos": 0, "campos": 0, "acertos": 0, "percentual": None}

    campos = acertos = 0
    for documento in conferidos:
        divergentes = {d.campo for d in comparar_com_a_curadoria(documento)}
        campos += len(CAMPOS_CONFERIDOS)
        acertos += len(CAMPOS_CONFERIDOS) - len(divergentes)

    return {
        "documentos": len(conferidos),
        "campos": campos,
        "acertos": acertos,
        "percentual": round(acertos / campos * 100) if campos else None,
    }


# ---------------------------------------------------------------------------
# Conferencia contra uma pasta de PDFs
# ---------------------------------------------------------------------------
def extrair_de_arquivo(caminho: Path) -> dict:
    """Roda a extracao local sobre um PDF do disco, sem tocar no banco.

    Reproduz o caminho de emergencia e nada mais: e ele que tem heuristica para
    calibrar. O Docling nao precisa disso — ele le o layout.
    """
    from apps.knowledge.blocos import dividir_em_blocos
    from apps.knowledge.flows import sugerir_metadados

    bruto = caminho.read_bytes()
    texto, metadados = _ler_pdf(bruto)
    sugestoes = sugerir_metadados(texto, metadados_do_arquivo=metadados, e_markdown=False)
    blocos = dividir_em_blocos(texto, e_markdown=False)

    return {
        "title": sugestoes["title"],
        "authors": sugestoes["authors"],
        "year": sugestoes["year"],
        "doi": sugestoes["doi"],
        "blocos": [b.titulo for b in blocos],
    }


def _ler_pdf(bruto: bytes) -> tuple[str, dict]:
    import io

    from pypdf import PdfReader

    leitor = PdfReader(io.BytesIO(bruto))
    paginas = [(pagina.extract_text() or "").strip() for pagina in leitor.pages]
    texto = "\n\n".join(p for p in paginas if p)
    try:
        info = dict(getattr(leitor, "metadata", None) or {})
    except Exception:
        info = {}
    return texto, {str(k): str(v) for k, v in info.items() if v}


def caminho_do_esperado(pdf: Path, pasta_esperados: Path) -> Path:
    return pasta_esperados / f"{pdf.stem}.json"


def carregar_esperado(caminho: Path) -> dict | None:
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def gravar_esperado(caminho: Path, resultado: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def comparar_com_o_esperado(obtido: dict, esperado: dict) -> list[str]:
    """As diferencas, em texto legivel. Lista vazia significa igual."""
    diferencas = []
    for campo in (*CAMPOS_CONFERIDOS, "blocos"):
        if obtido.get(campo) != esperado.get(campo):
            diferencas.append(
                f"{campo}:\n  esperado: {esperado.get(campo)!r}\n  obtido:   {obtido.get(campo)!r}"
            )
    return diferencas
