"""Precisa de backlinks? A resposta sai da posicao dos seus artigos no Google.

E o metodo usual sem ferramenta paga de links: o Search Console diz onde cada
artigo aparece, e a posicao separa os casos.

* **Cedo** — publicado ha menos de CEDO_EM_DIAS: o Google ainda esta decidindo.
* **Sem impressoes** — maduro e quase nunca mostrado: o problema e o tema
  (pouca busca, intencao errada) ou a indexacao, nao link.
* **Funcionando** — posicao media ate 5.
* **Falta autoridade** — mostrado, mas parado entre 6 e 30: o Google aceita o
  conteudo e poe outros na frente. E aqui que link faz diferenca.
* **Longe** — alem de 30: o texto nao e o que a busca quer; reescrever vale
  mais que link.

Quantos links: pela dificuldade das buscas do artigo (quem domina a primeira
pagina), ver `radar.dificuldade`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

CEDO_EM_DIAS = 60
MINIMO_DE_IMPRESSOES = 30
FUNCIONA_ATE = 5
AUTORIDADE_ATE = 30
MINIMO_DE_MADUROS = 3

ROTULOS = {
    "cedo": "cedo para julgar",
    "sem_impressoes": "quase nao aparece",
    "funcionando": "funcionando",
    "falta_autoridade": "falta autoridade",
    "longe": "longe do que a busca quer",
}


@dataclass
class Diagnostico:
    veredito: str
    explicacao: str
    links: str = ""
    artigos: list[dict] = field(default_factory=list)
    contagem: dict[str, int] = field(default_factory=dict)
    sem_console: bool = False


def classificar(linha: dict, agora=None) -> str:
    agora = agora or timezone.now()
    publicado = linha["artigo"].published_at
    if publicado is None or (agora - publicado).days < CEDO_EM_DIAS:
        return "cedo"
    if (linha["impressoes"] or 0) < MINIMO_DE_IMPRESSOES or linha["posicao"] is None:
        return "sem_impressoes"
    if linha["posicao"] <= FUNCIONA_ATE:
        return "funcionando"
    if linha["posicao"] <= AUTORIDADE_ATE:
        return "falta_autoridade"
    return "longe"


def _dificuldade_do_artigo(linha: dict, dificuldades):
    from apps.radar.dificuldade import de_textos

    textos = [c["consulta"] for c in linha["consultas"]]
    artigo = linha["artigo"]
    textos += [artigo.focus_keyword, artigo.title]
    if artigo.topic_id:
        textos.append(artigo.topic.target_keyword)
    return de_textos([t for t in textos if t], dificuldades)


def diagnosticar() -> Diagnostico:
    from apps.radar.dificuldade import mapa
    from apps.radar.models import ColetaDoConsole
    from apps.radar.search_console import desempenho_dos_artigos

    if not ColetaDoConsole.objects.exists():
        return Diagnostico(
            veredito="sem dados",
            explicacao=(
                "Sem Search Console nao da para saber. Ligue-o (docs/CONTAS_EXTERNAS.md): "
                "e a posicao de cada artigo no Google que diz se falta autoridade."
            ),
            sem_console=True,
        )

    dificuldades = mapa()
    linhas = desempenho_dos_artigos(limite=200)
    contagem = dict.fromkeys(ROTULOS, 0)
    artigos = []
    for linha in linhas:
        situacao = classificar(linha)
        contagem[situacao] += 1
        artigos.append(
            {
                **linha,
                "situacao": situacao,
                "rotulo": ROTULOS[situacao],
                "dificuldade": _dificuldade_do_artigo(linha, dificuldades),
            }
        )
    ordem = {"falta_autoridade": 0, "longe": 1, "sem_impressoes": 2, "funcionando": 3, "cedo": 4}
    artigos.sort(key=lambda a: (ordem[a["situacao"]], a["posicao"] or 99))

    maduros = len(linhas) - contagem["cedo"]
    if maduros < MINIMO_DE_MADUROS:
        return Diagnostico(
            veredito="cedo para dizer",
            explicacao=(
                f"So {maduros} artigo(s) com mais de {CEDO_EM_DIAS} dias no ar. Antes disso "
                "a posicao ainda oscila; continue publicando e volte a olhar."
            ),
            artigos=artigos,
            contagem=contagem,
        )

    falta = contagem["falta_autoridade"]
    if falta / maduros >= 0.4:
        dificeis = [
            a
            for a in artigos
            if a["situacao"] == "falta_autoridade"
            and a["dificuldade"] is not None
            and a["dificuldade"].rotulo == "dificil"
        ]
        if len(dificeis) * 2 > falta:
            links = "muitos: dezenas de links, de sites fortes do setor"
        else:
            links = "poucos: 5 a 15 bons links, apontando para estes artigos"
        return Diagnostico(
            veredito="links ajudariam",
            explicacao=(
                f"{falta} de {maduros} artigos maduros aparecem, mas param entre a 6a e a "
                "30a posicao: o Google aceita o conteudo e poe sites com mais autoridade na "
                "frente. Links para ESTES artigos (e nao para a home) sao o que falta."
            ),
            links=links,
            artigos=artigos,
            contagem=contagem,
        )
    if contagem["funcionando"] >= falta and contagem["funcionando"] >= maduros / 3:
        return Diagnostico(
            veredito="links nao sao prioridade",
            explicacao=(
                "A maioria dos artigos maduros ja chega ao topo. Continue escolhendo temas "
                "com brecha; link vira prioridade quando muitos travarem entre a 6a e a 30a."
            ),
            artigos=artigos,
            contagem=contagem,
        )
    return Diagnostico(
        veredito="o gargalo nao e link",
        explicacao=(
            "Os artigos quase nao aparecem, ou aparecem longe (alem da 30a posicao). Link "
            "nao resolve isso: revise o tema (tem busca?) e a intencao (o texto responde o "
            "que a pessoa procura?), e confira a indexacao no Search Console."
        ),
        artigos=artigos,
        contagem=contagem,
    )


def resumo_dos_temas(grupos) -> dict[str, int]:
    """Quantos temas da lista estao em brecha, medio ou dificil."""
    contagem = {"brecha": 0, "medio": 0, "dificil": 0}
    for grupo in grupos:
        dificuldade = getattr(grupo, "dificuldade", None)
        if dificuldade is not None:
            contagem[dificuldade.rotulo] += 1
    return contagem
