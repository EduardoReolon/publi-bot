"""Cuidados de linguagem que valem para qualquer cliente, em artigos e redes.

Tudo por algoritmo (listas e palavras-chave), e tudo AVISO na revisao, nunca
bloqueio: em saude, varios termos aparecem em contexto clinico correto, e
quem decide e a pessoa que revisa. Tres partes:

* **linguagem que estigmatiza** — a pessoa antes da condicao ("pessoa com
  deficiencia", e nao "deficiente"), diagnostico que nao vira xingamento;
* **temas sensiveis** — suicidio e automutilacao (recomendacoes da OMS para
  a imprensa, com o CVV 188) e transtornos alimentares: a instrucao vai para
  quem escreve, e a conferencia confere o que da para conferir;
* **regras dos conselhos de saude** (CFM, Resolucao 2.336/2023; CFO, codigo
  de etica): superlativo, garantia, preco e promocao, "antes e depois". Entram
  como termos proibidos do ponto de partida de saude (editaveis), porque la
  sao regra, e nao gosto.

As listas sao genericas de proposito: o negocio de cada cliente entra pelo
guia editorial dele, nao aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# termo -> (troca sugerida, motivo). Casamento por palavra inteira, sem caixa e
# sem acento (o mesmo da conferencia do guia).
ESTIGMATIZANTES: list[dict] = [
    {"termo": "portador de", "troca": "pessoa com", "motivo": "a pessoa antes da condicao"},
    {"termo": "portadora de", "troca": "pessoa com", "motivo": "a pessoa antes da condicao"},
    {"termo": "portadores de", "troca": "pessoas com", "motivo": "a pessoa antes da condicao"},
    {"termo": "sofre de", "troca": "tem / vive com", "motivo": "nao presuma sofrimento"},
    {"termo": "sofrem de", "troca": "tem / vivem com", "motivo": "nao presuma sofrimento"},
    {"termo": "vitima de", "troca": "pessoa que teve", "motivo": "doenca nao e crime"},
    {"termo": "vitimas de", "troca": "pessoas que tiveram", "motivo": "doenca nao e crime"},
    {
        "termo": "deficiente",
        "troca": "pessoa com deficiencia",
        "motivo": "termo da Convencao da ONU e da Lei Brasileira de Inclusao",
    },
    {
        "termo": "deficientes",
        "troca": "pessoas com deficiencia",
        "motivo": "termo da Convencao da ONU e da Lei Brasileira de Inclusao",
    },
    {
        "termo": "portador de necessidades especiais",
        "troca": "pessoa com deficiencia",
        "motivo": "termo em desuso",
    },
    {"termo": "aleijado", "troca": "pessoa com deficiencia fisica", "motivo": "pejorativo"},
    {"termo": "retardado", "troca": "pessoa com deficiencia intelectual", "motivo": "pejorativo"},
    {"termo": "mongoloide", "troca": "pessoa com sindrome de Down", "motivo": "pejorativo"},
    {"termo": "surdo-mudo", "troca": "pessoa surda", "motivo": "a maioria das pessoas surdas fala"},
    {"termo": "doente mental", "troca": "pessoa com transtorno mental", "motivo": "estigma"},
    {"termo": "doentes mentais", "troca": "pessoas com transtorno mental", "motivo": "estigma"},
    {"termo": "louco", "troca": "", "motivo": "estigma de transtorno mental"},
    {"termo": "esquizofrenico", "troca": "pessoa com esquizofrenia", "motivo": "a pessoa antes"},
    {"termo": "diabetico", "troca": "pessoa com diabetes", "motivo": "a pessoa antes da condicao"},
    {"termo": "diabeticos", "troca": "pessoas com diabetes", "motivo": "a pessoa antes"},
    {"termo": "epileptico", "troca": "pessoa com epilepsia", "motivo": "a pessoa antes"},
    {"termo": "aidetico", "troca": "pessoa vivendo com HIV", "motivo": "pejorativo"},
    {"termo": "viciado", "troca": "pessoa com dependencia", "motivo": "estigma"},
    {"termo": "drogado", "troca": "pessoa que usa drogas", "motivo": "estigma"},
    {"termo": "obesos", "troca": "pessoas com obesidade", "motivo": "a pessoa antes da condicao"},
    {"termo": "cometer suicidio", "troca": "morrer por suicidio", "motivo": "suicidio nao e crime"},
    {"termo": "cometeu suicidio", "troca": "morreu por suicidio", "motivo": "suicidio nao e crime"},
    {"termo": "denegrir", "troca": "difamar", "motivo": "expressao de origem racista"},
    {"termo": "mulato", "troca": "pardo / negro", "motivo": "termo de origem racista"},
    {"termo": "a coisa ta preta", "troca": "a situacao esta dificil", "motivo": "racismo"},
    {"termo": "lista negra", "troca": "lista de bloqueio", "motivo": "associacao racista"},
    {"termo": "mercado negro", "troca": "mercado ilegal", "motivo": "associacao racista"},
    {"termo": "homossexualismo", "troca": "homossexualidade", "motivo": "o sufixo indica doenca"},
    {"termo": "opcao sexual", "troca": "orientacao sexual", "motivo": "nao e escolha"},
    {"termo": "velhinho", "troca": "pessoa idosa", "motivo": "infantiliza"},
    {"termo": "velhinhos", "troca": "pessoas idosas", "motivo": "infantiliza"},
]

# CFM 2.336/2023 e codigo de etica do CFO. Proibidos (o revisor confirma para
# manter): no ponto de partida de saude e no botao "adicionar ao meu guia".
CONSELHOS_DE_SAUDE: list[dict] = [
    # Especificos o bastante para nao pegar "o melhor horario para tomar".
    {"termo": "a melhor clinica", "troca": "", "motivo": "superlativo (CFM/CFO)"},
    {"termo": "o melhor medico", "troca": "", "motivo": "superlativo (CFM/CFO)"},
    {"termo": "o melhor dentista", "troca": "", "motivo": "superlativo (CFM/CFO)"},
    {"termo": "o melhor tratamento", "troca": "", "motivo": "superlativo (CFM/CFO)"},
    {"termo": "unica clinica", "troca": "", "motivo": "superlativo (CFM/CFO)"},
    {"termo": "referencia em", "troca": "", "motivo": "autopromocao (CFM/CFO)"},
    {"termo": "resultado garantido", "troca": "", "motivo": "garantia de resultado (CFM/CFO)"},
    {"termo": "resultados garantidos", "troca": "", "motivo": "garantia de resultado (CFM/CFO)"},
    {"termo": "promocao", "troca": "", "motivo": "preco e promocao (CFM/CFO)"},
    {"termo": "desconto", "troca": "", "motivo": "preco e promocao (CFM/CFO)"},
    {"termo": "consulta gratuita", "troca": "", "motivo": "preco e promocao (CFM/CFO)"},
    {"termo": "avaliacao gratuita", "troca": "", "motivo": "preco e promocao (CFM/CFO)"},
    {"termo": "antes e depois", "troca": "", "motivo": "so dentro das regras da resolucao (CFM)"},
]


@dataclass(frozen=True)
class TemaSensivel:
    nome: str
    palavras: re.Pattern
    instrucao: str
    exige: re.Pattern | None = None  # se o tema aparece, o texto precisa ter isto
    exige_aviso: str = ""
    evita: re.Pattern | None = None  # se o tema aparece, o texto nao deve ter isto
    evita_aviso: str = ""


TEMAS_SENSIVEIS = [
    TemaSensivel(
        nome="suicidio e automutilacao",
        palavras=re.compile(
            r"suic[ií]d|tirar a pr[oó]pria vida|se matar|automutila|autoles[aã]o|"
            r"auto-?les[aã]o|ideia[s]? de morte",
            re.I,
        ),
        instrucao=(
            "SUICIDIO/AUTOMUTILACAO (recomendacoes da OMS): nao descreva metodo, meio "
            "nem local; nao apresente como solucao, como inevitavel nem como reacao "
            "compreensivel a um problema; nao use 'cometer suicidio' (use 'morrer por "
            "suicidio', 'tentativa de suicidio'); mostre que ha tratamento e ajuda; "
            "inclua: CVV, ligue 188 (gratuito, 24 horas) ou cvv.org.br."
        ),
        exige=re.compile(r"\b188\b|cvv", re.I),
        exige_aviso="fala de suicidio ou automutilacao sem o CVV (ligue 188 ou cvv.org.br)",
        evita=re.compile(
            r"enforc|overdose de|pulou d[eo]|se jogou|cortou os pulsos|veneno|arma de fogo",
            re.I,
        ),
        evita_aviso="fala de suicidio e cita meio ou metodo (a OMS recomenda nao citar)",
    ),
    TemaSensivel(
        nome="transtornos alimentares",
        palavras=re.compile(
            r"anorexi|bulimi|compuls[aã]o alimentar|transtorno[s]? alimenta|ortorexi", re.I
        ),
        instrucao=(
            "TRANSTORNOS ALIMENTARES: nao cite peso, IMC, numero de calorias nem medidas "
            "como exemplo ou meta; nao descreva comportamentos compensatorios em detalhe; "
            "nao elogie emagrecimento; diga que ha tratamento e que procurar ajuda "
            "profissional funciona."
        ),
        evita=re.compile(r"\b\d+[.,]?\d*\s*(kg|quilos|kcal|calorias)\b", re.I),
        evita_aviso="fala de transtorno alimentar e cita peso ou calorias",
    ),
]

# Para o guia editorial (vai em todo prompt que escreve texto publicado).
INSTRUCAO_GERAL = (
    "- Linguagem que respeita: a pessoa antes da condicao ('pessoa com diabetes', "
    "'pessoa com deficiencia'), sem termos pejorativos, sem estereotipo de genero, "
    "raca, idade, corpo, religiao ou orientacao sexual, e sem diagnostico como "
    "adjetivo ou xingamento."
)


def _normalizar(texto: str) -> str:
    from apps.editorial.services import _normalizar as normalizar

    return normalizar(texto)


def instrucoes_sensiveis(texto: str) -> str:
    """As instrucoes dos temas sensiveis que aparecem no texto (material, pauta)."""
    return "\n".join(t.instrucao for t in TEMAS_SENSIVEIS if t.palavras.search(texto or ""))


def todos_os_temas_sensiveis() -> str:
    """Para o guia editorial: as instrucoes, valendo SE o tema aparecer."""
    return "\n".join(f"- Se o texto tocar no tema: {t.instrucao}" for t in TEMAS_SENSIVEIS)


def cuidados(texto: str) -> list:
    """Os avisos de linguagem do texto: termos que estigmatizam (com a troca) e
    temas sensiveis sem o cuidado que da para conferir."""
    from apps.editorial.services import Achado, _padrao

    normalizado = _normalizar(texto or "")
    achados = []
    for item in ESTIGMATIZANTES:
        vezes = len(_padrao(item["termo"]).findall(normalizado))
        if vezes:
            achados.append(
                Achado(
                    expressao=item["termo"],
                    sugestao=item["troca"],
                    vezes=vezes,
                    motivo=item["motivo"],
                )
            )
    for tema in TEMAS_SENSIVEIS:
        if not tema.palavras.search(texto or ""):
            continue
        if tema.exige is not None and not tema.exige.search(texto or ""):
            achados.append(
                Achado(expressao=tema.nome, sugestao="", vezes=1, motivo=tema.exige_aviso)
            )
        if tema.evita is not None and tema.evita.search(texto or ""):
            achados.append(
                Achado(expressao=tema.nome, sugestao="", vezes=1, motivo=tema.evita_aviso)
            )
    return achados


def termos_que_faltam(perfil) -> list[dict]:
    """Os termos dos conselhos de saude que o guia ainda nao tem."""
    tem = {_normalizar(t.get("termo", "")) for t in (perfil.termos or []) if isinstance(t, dict)}
    return [dict(t) for t in CONSELHOS_DE_SAUDE if _normalizar(t["termo"]) not in tem]
