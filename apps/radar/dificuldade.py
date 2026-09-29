"""Dificuldade de cada busca: quem ocupa a primeira pagina do Google.

Sem pagar por metrica de autoridade, o radar ja tem o que importa: as paginas
de resultado das buscas que ele fez (guardadas por 90 dias). Duas contas:

* **Sites dominantes**: os que aparecem em muitas buscas DIFERENTES do seu
  tema. E a autoridade medida no seu nicho, e nao uma nota generica: se um
  portal esta em quase toda busca, ele e forte ali.
* **Brechas**: forum, video, rede social, Q&A — plataforma aberta onde
  qualquer um publica. Quando o Google poe isso no topo e porque nao achou
  pagina melhor: um artigo bom entra sem precisar de muita autoridade.

A nota (0 a 100) pesa mais o topo da pagina. E o que decide, na tela, se o
tema e "brecha", "medio" ou "dificil" — e quantos links um artigo ali tende
a precisar para chegar a primeira pagina.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.utils import timezone

JANELA_EM_DIAS = 90
# Um dominio e "dominante" quando aparece em pelo menos esta fracao das buscas
# distintas (e em pelo menos MINIMO_DE_BUSCAS delas).
FRACAO_DOMINANTE = 0.25
MINIMO_DE_BUSCAS = 3
# Abaixo disto de buscas guardadas, a comparacao entre elas nao diz nada.
BUSCAS_PARA_MEDIR = 6

BRECHA_ATE = 30
DIFICIL_DESDE = 60

_CAMINHO_DE_FORUM = re.compile(
    r"/(forum|foruns|comunidade|community|perguntas|questions|topic|topico|thread)s?/",
    re.IGNORECASE,
)


@dataclass
class Dificuldade:
    nota: int
    dominantes: list[str] = field(default_factory=list)
    brechas: list[str] = field(default_factory=list)

    @property
    def rotulo(self) -> str:
        if self.nota < BRECHA_ATE:
            return "brecha"
        if self.nota < DIFICIL_DESDE:
            return "medio"
        return "dificil"

    @property
    def links(self) -> str:
        """Quantos links um artigo tende a precisar para a primeira pagina."""
        return {
            "brecha": "poucos ou nenhum",
            "medio": "alguns (5 a 15 bons links de sites do setor)",
            "dificil": "muitos (dezenas, e de sites fortes)",
        }[self.rotulo]


def _dominio(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _e_brecha(url: str, dominio: str) -> bool:
    from apps.knowledge.fontes_web import _plataforma

    return _plataforma(dominio) is not None or bool(_CAMINHO_DE_FORUM.search(url))


def _chave(texto: str) -> str:
    return " ".join((texto or "").lower().split())


def mapa() -> dict[str, Dificuldade]:
    """{consulta: Dificuldade} para as buscas guardadas. Vazio sem dados."""
    from apps.radar.concorrentes import _dominio_proprio
    from apps.radar.models import ResultadoOrganico

    limite = timezone.now() - timezone.timedelta(days=JANELA_EM_DIAS)
    linhas = list(
        ResultadoOrganico.objects.filter(criado_em__gte=limite)
        .order_by("consulta", "-criado_em", "posicao")
        .values("consulta", "url", "posicao", "criado_em")
    )
    # Da busca repetida, vale a pagina mais recente.
    paginas: dict[str, list[dict]] = {}
    for linha in linhas:
        chave = _chave(linha["consulta"])
        atual = paginas.setdefault(chave, [])
        if atual and atual[0]["criado_em"].date() != linha["criado_em"].date():
            continue
        atual.append(linha)
    if len(paginas) < BUSCAS_PARA_MEDIR:
        return {}

    proprio = _dominio_proprio()
    presenca: dict[str, int] = {}
    for resultados in paginas.values():
        for dominio in {_dominio(r["url"]) for r in resultados}:
            presenca[dominio] = presenca.get(dominio, 0) + 1
    corte = max(MINIMO_DE_BUSCAS, FRACAO_DOMINANTE * len(paginas))
    dominantes = {d for d, n in presenca.items() if n >= corte and d != proprio}

    saida = {}
    for chave, resultados in paginas.items():
        total = forte = fraco = 0
        nomes_fortes, nomes_fracos = [], []
        for r in sorted(resultados, key=lambda r: r["posicao"])[:10]:
            peso = 11 - min(r["posicao"], 10)
            total += peso
            dominio = _dominio(r["url"])
            if dominio in dominantes:
                forte += peso
                nomes_fortes.append(dominio)
            elif _e_brecha(r["url"], dominio):
                fraco += peso
                nomes_fracos.append(dominio)
        if not total:
            continue
        nota = round(100 * (forte - 0.5 * fraco) / total)
        saida[chave] = Dificuldade(
            nota=max(0, min(100, nota)),
            dominantes=list(dict.fromkeys(nomes_fortes))[:5],
            brechas=list(dict.fromkeys(nomes_fracos))[:5],
        )
    return saida


def do_grupo(grupo, dificuldades: dict[str, Dificuldade]) -> Dificuldade | None:
    """A do rotulo do tema, ou a mais facil entre os sinais dele que foram buscados."""
    if not dificuldades:
        return None
    if (achada := dificuldades.get(_chave(grupo.rotulo))) is not None:
        return achada
    candidatas = [
        dificuldades[_chave(texto)]
        for texto in grupo.sinais.values_list("texto", flat=True)[:50]
        if _chave(texto) in dificuldades
    ]
    return min(candidatas, key=lambda d: d.nota, default=None)


def de_textos(textos, dificuldades: dict[str, Dificuldade]) -> Dificuldade | None:
    """A mais facil entre estes textos (as consultas de um artigo, de uma pauta)."""
    candidatas = [dificuldades[_chave(t)] for t in textos if _chave(t) in dificuldades]
    return min(candidatas, key=lambda d: d.nota, default=None)


# Quanto a dificuldade tira da nota quando a estrategia e "brechas primeiro".
# Nao zera o dificil: um tema dificil muito bom ainda passa um medio fraco.
FATOR_DA_DIFICULDADE = {"brecha": 1.0, "medio": 0.8, "dificil": 0.55}
# Sem pagina de resultado guardada, a dificuldade e desconhecida: um pouco
# abaixo da brecha, para o que foi medido como facil vir antes.
FATOR_SEM_MEDIDA = 0.9


def prioridade(nota: float, dificuldade: Dificuldade | None, estrategia: str) -> float:
    """A nota, ou a nota pesada pela dificuldade em "brechas primeiro"."""
    if estrategia != "brechas":
        return nota
    fator = FATOR_SEM_MEDIDA if dificuldade is None else FATOR_DA_DIFICULDADE[dificuldade.rotulo]
    return nota * fator


def estrategia_efetiva(config, diagnostico=None) -> tuple[str, str]:
    """("brechas" | "equilibrada", por que) — a escolhida, ou a do momento do site.

    Na automatica, o que decide e o diagnostico de autoridade (Search
    Console): so quando os artigos maduros ja chegam ao topo o site deixa de
    precisar de brechas.
    """
    escolhida = config.estrategia_de_temas
    if escolhida == "brechas":
        return "brechas", "escolhida na configuracao"
    if escolhida == "equilibrada":
        return "equilibrada", "escolhida na configuracao"
    if diagnostico is None:
        from apps.radar.autoridade import diagnosticar

        diagnostico = diagnosticar()
    if diagnostico.veredito == "links nao sao prioridade":
        return "equilibrada", "os artigos maduros ja chegam ao topo do Google"
    return "brechas", {
        "sem dados": "sem Search Console, o site e tratado como comecando",
        "cedo para dizer": "poucos artigos com mais de 60 dias no ar",
        "links ajudariam": "os artigos param entre a 6a e a 30a posicao: falta autoridade",
    }.get(diagnostico.veredito, "o site ainda nao chega ao topo")
