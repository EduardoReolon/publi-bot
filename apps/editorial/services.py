"""O guia editorial em uso: no prompt, e na conferencia antes de aprovar."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from apps.editorial.presets import MARCAS_DE_MAQUINA, PRESETS

# Prompts que ESCREVEM texto publicado. So eles recebem o guia: o filtro de
# consenso e a extracao de metadados leem fontes, e um guia de tom ali so
# gastaria contexto e poderia torcer a leitura.
PROMPTS_COM_GUIA = frozenset(
    {
        "article_outline",
        "section_draft",
        "article_framing",
        "seo_metadata",
        "article_faq",
        "seo_draft",
        "qa_answer",
    }
)

# So o planejamento recebe a estrutura-modelo: e ele que decide as secoes.
PROMPTS_COM_ESTRUTURA = frozenset({"article_outline"})

# So o fecho recebe o convite: convite no meio do texto e o que o leitor sente
# como anuncio.
PROMPTS_COM_CONVITE = frozenset({"article_framing", "seo_draft"})

_ROTULOS_DE_TOM = {
    "tom_humor": ("serio", "engracado"),
    "tom_formalidade": ("formal", "casual"),
    "tom_respeito": ("respeitoso", "irreverente"),
    "tom_entusiasmo": ("objetivo", "entusiasmado"),
}


def aplicar_modo(perfil, modo: str) -> None:
    """Preenche o perfil com o ponto de partida do modo. Nao salva."""
    preset = PRESETS.get(modo)
    if preset is None:
        raise ValueError(f"modo desconhecido: {modo!r}")
    perfil.mode = modo
    for campo, valor in preset.items():
        setattr(perfil, campo, valor if not isinstance(valor, list) else [dict(v) for v in valor])


def _descrever_tom(perfil) -> str:
    partes = []
    for campo, (baixo, alto) in _ROTULOS_DE_TOM.items():
        valor = getattr(perfil, campo)
        if valor <= 2:
            partes.append(baixo if valor == 1 else f"mais {baixo} que {alto}")
        elif valor >= 4:
            partes.append(alto if valor == 5 else f"mais {alto} que {baixo}")
        else:
            partes.append(f"equilibrado entre {baixo} e {alto}")
    return "; ".join(partes)


def texto_do_guia(perfil, *, chave: str, tipo_de_conteudo: str = "") -> str:
    """O bloco de guia editorial para o prompt `chave`, ou vazio."""
    if perfil is None or chave not in PROMPTS_COM_GUIA:
        return ""

    linhas = ["GUIA EDITORIAL DO SITE — siga no texto que escrever:"]
    linhas.append(f"- Tom: {_descrever_tom(perfil)}.")
    if perfil.pessoa:
        linhas.append(f"- Trate o leitor por: {perfil.pessoa}.")
    if perfil.regra_de_ouro:
        linhas.append(f"- Regra de ouro: {perfil.regra_de_ouro}")

    pares = [p for p in (perfil.somos or []) if isinstance(p, dict) and p.get("somos")]
    if pares:
        linhas.append("- Somos / nao somos:")
        for par in pares:
            nao = f", nao {par['nao_somos']}" if par.get("nao_somos") else ""
            linhas.append(f"  - {par['somos']}{nao}")

    termos = [t for t in (perfil.termos or []) if isinstance(t, dict) and t.get("termo")]
    if termos:
        linhas.append("- NUNCA use estes termos:")
        for termo in termos:
            troca = f' (use "{termo["troca"]}")' if termo.get("troca") else ""
            linhas.append(f'  - "{termo["termo"]}"{troca}')

    linhas.append(
        "- Evite frases de texto de maquina: 'vale ressaltar', 'e importante "
        "destacar', 'no cenario atual', 'desempenha um papel crucial', "
        "'proporcionar', 'mergulhar', 'jornada', travessao decorativo."
    )

    if chave in PROMPTS_COM_ESTRUTURA and tipo_de_conteudo:
        estrutura = perfil.estrutura_de(tipo_de_conteudo)
        if estrutura:
            linhas.append(
                "- Estrutura-modelo deste tipo de conteudo (objetivo de cada "
                "secao, na ordem; adapte os titulos ao tema, e so omita uma "
                "secao se as fontes nao a sustentarem):"
            )
            linhas.extend(f"  {n}. {item}" for n, item in enumerate(estrutura, start=1))

    if chave in PROMPTS_COM_CONVITE and perfil.convite:
        linhas.append(f"- Convite final (so no fecho, uma vez): {perfil.convite}")

    if perfil.exemplos:
        linhas.append("- Paragrafos de exemplo do tom desejado (imite o tom, NAO o conteudo):")
        linhas.append(perfil.exemplos.strip())

    return "\n".join(linhas)


def perfil_atual():
    """O perfil do tenant em uso, ou None fora de um tenant."""
    from django.db import connection

    from apps.editorial.models import EditorialProfile

    if getattr(connection, "schema_name", "public") == "public":
        return None
    return EditorialProfile.carregar()


# ---------------------------------------------------------------------------
# Conferencia do texto
# ---------------------------------------------------------------------------
def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return sem_acento.casefold()


def _padrao(expressao: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(_normalizar(expressao))}(?!\w)")


@dataclass(frozen=True)
class Achado:
    expressao: str
    sugestao: str
    vezes: int
    motivo: str = ""


@dataclass
class Conferencia:
    proibidos: list[Achado] = field(default_factory=list)
    marcas: list[Achado] = field(default_factory=list)
    travessoes: int = 0

    @property
    def bloqueia(self) -> bool:
        return bool(self.proibidos)

    @property
    def vazia(self) -> bool:
        return not (self.proibidos or self.marcas or self.travessoes)


# Acima disto, o travessao deixa de ser pontuacao e vira tique.
TRAVESSOES_TOLERADOS = 3


def conferir_texto(texto: str, perfil) -> Conferencia:
    """Termos proibidos (bloqueiam) e marcas de maquina (avisam).

    Casa palavra inteira, sem caixa e sem acento: "Trata" e "trata" sao o
    mesmo termo, e "tratamento" nao e "trata".
    """
    normalizado = _normalizar(texto or "")
    resultado = Conferencia()

    for termo in (perfil.termos if perfil else []) or []:
        if not isinstance(termo, dict) or not termo.get("termo"):
            continue
        vezes = len(_padrao(termo["termo"]).findall(normalizado))
        if vezes:
            resultado.proibidos.append(
                Achado(
                    expressao=termo["termo"],
                    sugestao=termo.get("troca", ""),
                    vezes=vezes,
                    motivo=termo.get("motivo", ""),
                )
            )

    for expressao, sugestao in MARCAS_DE_MAQUINA:
        vezes = len(_padrao(expressao).findall(normalizado))
        if vezes:
            resultado.marcas.append(Achado(expressao=expressao, sugestao=sugestao, vezes=vezes))

    travessoes = (texto or "").count("—")
    if travessoes > TRAVESSOES_TOLERADOS:
        resultado.travessoes = travessoes

    return resultado
