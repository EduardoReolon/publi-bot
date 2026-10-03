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


def texto_do_guia(
    perfil, *, chave: str, tipo_de_conteudo: str = "", com_convite: bool = True
) -> str:
    """O bloco de guia editorial para o prompt `chave`, ou vazio.

    `com_convite=False` quando o artigo nao leva chamada (tema longe da
    oferta): ai o fecho tambem nao convida.
    """
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

    if chave in PROMPTS_COM_CONVITE and perfil.convite and com_convite:
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
    repeticoes: list[Achado] = field(default_factory=list)

    @property
    def bloqueia(self) -> bool:
        return bool(self.proibidos)

    @property
    def vazia(self) -> bool:
        return not (self.proibidos or self.marcas or self.travessoes or self.repeticoes)


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

    resultado.repeticoes = [
        Achado(expressao=frase, sugestao="", vezes=vezes)
        for frase, vezes in expressoes_repetidas(texto or "")
    ]
    return resultado


# Expressao de 3 palavras ou mais que volta 3 vezes ou mais: o texto escrito
# por secoes tende a reafirmar a mesma formula em cada uma.
TAMANHO_DA_EXPRESSAO = 3
VEZES_PARA_AVISAR = 3
_VAZIAS = set(
    """a o as os um uma uns umas de do da dos das em no na nos nas por para com
    sem que e ou se ao aos como mais menos muito ja nao sim seu sua seus suas
    isso esse essa este esta ele ela eles elas e foi ser sao tem ter pode""".split()
)


def expressoes_repetidas(texto: str, limite: int = 6) -> list[tuple[str, int]]:
    """As expressoes que se repetem pelo texto, sem modelo: sequencias de
    palavras (sem as de ligacao nas pontas) contadas, e as que se sobrepoem
    juntadas na maior. Links, marcadores e titulos ficam de fora."""
    from collections import Counter

    limpo = re.sub(r"\]\([^)]*\)|\[\[[^\]]*\]\]|https?://\S+|^#+ .*$", " ", texto, flags=re.M)
    palavras = re.findall(r"\w+", _normalizar(limpo))
    n = TAMANHO_DA_EXPRESSAO
    contagem = Counter(tuple(palavras[i : i + n]) for i in range(len(palavras) - n + 1))
    repetidas = {g: v for g, v in contagem.items() if v >= VEZES_PARA_AVISAR}
    # Junta as que se encaixam ("interacoes complexas entre pessoas" +
    # "complexas entre pessoas processos" -> uma expressao so).
    frases: list[tuple[list[str], int]] = []
    for grama, vezes in sorted(repetidas.items(), key=lambda kv: -kv[1]):
        for frase in frases:
            if list(grama[:-1]) == frase[0][-(n - 1) :]:
                frase[0].append(grama[-1])
                break
            if list(grama[1:]) == frase[0][: n - 1]:
                frase[0].insert(0, grama[0])
                break
        else:
            frases.append((list(grama), vezes))
    saida = []
    for palavras_da_frase, vezes in frases:
        while palavras_da_frase and palavras_da_frase[0] in _VAZIAS:
            palavras_da_frase.pop(0)
        while palavras_da_frase and palavras_da_frase[-1] in _VAZIAS:
            palavras_da_frase.pop()
        if len([p for p in palavras_da_frase if p not in _VAZIAS]) >= 2:
            saida.append((" ".join(palavras_da_frase), vezes))
    return saida[:limite]
