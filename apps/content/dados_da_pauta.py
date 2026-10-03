"""Dados publicos no artigo: da escolha na pauta a conferencia do numero.

1. Na pauta, `sugestoes` (embedding com o catalogo) e a escolha (`DadoDaPauta`).
2. Na geracao, `fatos_do_artigo` congela os valores no artigo e
   `bloco_para_o_prompt` os entrega ao modelo, citaveis como [[DADO_N]].
3. Na montagem, `trocar_marcadores` vira "(IBGE, 2019)" no texto e o dado
   entra na lista de referencias com o link da instituicao.
4. Na revisao, `pendencias` bloqueia a aprovacao se o texto cita o dado e o
   numero nao saiu igual.
"""

from __future__ import annotations

import re
from decimal import Decimal

PADRAO_DADO = re.compile(r"\[\[DADO_(\d+)\]\]")


def sugestoes(pauta, limite: int = 5) -> list:
    """Series aprovadas proximas da pauta que ainda nao foram escolhidas."""
    from apps.dados.catalogo import sugerir

    texto = ". ".join(p for p in [pauta.title, pauta.target_keyword, pauta.briefing] if p)
    escolhidas = pauta.dados.values_list("serie_id", flat=True)
    return sugerir(texto, limite=limite, excluir=escolhidas)


def escolhidos(pauta) -> list:
    """Os dados da pauta, com o valor atual de cada um (ou None)."""
    from apps.dados.catalogo import fato

    itens = []
    for dado in pauta.dados.select_related("serie__instituicao"):
        itens.append({"dado": dado, "fato": fato(dado.serie, dado.local)})
    return itens


def fatos_do_artigo(article) -> list[dict]:
    """Os fatos do artigo, numerados. Congela na primeira vez: a revisao
    confere contra o numero que o modelo recebeu, mesmo que saia um novo."""
    if article.dados_usados or article.topic_id is None:
        return article.dados_usados
    from apps.dados.catalogo import fato

    fatos = []
    for dado in article.topic.dados.select_related("serie__instituicao"):
        item = fato(dado.serie, dado.local)
        if item is not None:
            fatos.append({"n": len(fatos) + 1, **item, "citado": False})
    if fatos:
        article.dados_usados = fatos
        article.save(update_fields=["dados_usados"])
    return fatos


def bloco_para_o_prompt(fatos: list[dict]) -> str:
    """Os fatos para o modelo: texto nosso (nao de terceiros), fora das fontes."""
    if not fatos:
        return ""
    linhas = [
        "Dados oficiais disponiveis. Se usar um, escreva o numero EXATAMENTE como "
        "esta (sem arredondar) e cite logo depois com o marcador indicado:"
    ]
    for f in fatos:
        unidade = f" {f['unidade']}" if f.get("unidade") else ""
        linhas.append(
            f"- [[DADO_{f['n']}]] {f['titulo']} — {f['local']}, {f['periodo']}: "
            f"{f['valor']}{unidade} (fonte: {f['instituicao']})"
        )
    return "\n".join(linhas)


def com_dados(fontes: str, article) -> str:
    """O bloco de fontes do prompt, com os dados no fim (se houver)."""
    bloco = bloco_para_o_prompt(fatos_do_artigo(article))
    return f"{fontes}\n\n{bloco}" if bloco else fontes


def trocar_marcadores(texto: str, article) -> tuple[str, list]:
    """[[DADO_N]] vira "(IBGE, 2019)"; devolve tambem as referencias (Fonte).
    Marcador de dado que nao existe some (o modelo inventou um numero)."""
    from apps.content.rendering import Fonte

    fatos = {f["n"]: f for f in (article.dados_usados or [])}
    citados: list[int] = []

    def trocar(achado: re.Match) -> str:
        n = int(achado.group(1))
        fato = fatos.get(n)
        if fato is None:
            return ""
        if n not in citados:
            citados.append(n)
        return f"({fato['instituicao']}, {fato['periodo']})"

    texto = PADRAO_DADO.sub(trocar, texto)
    texto = re.sub(r"[ \t]+([.,;:!?)])", r"\1", texto)
    if citados:
        for f in article.dados_usados:
            f["citado"] = f["n"] in citados
    referencias = [
        Fonte(
            url=fatos[n]["url"],
            anchor=f"{fatos[n]['instituicao']} — {fatos[n]['titulo']} ({fatos[n]['local']}, "
            f"{fatos[n]['periodo']})",
        )
        for n in citados
    ]
    return texto, referencias


def pendencias(article) -> list[str]:
    """Dado citado cujo numero nao aparece igual no texto."""
    from apps.dados.catalogo import formas_do_numero

    texto = article.body_markdown or ""
    faltam = []
    for f in article.dados_usados or []:
        if not f.get("citado"):
            continue
        if not any(forma in texto for forma in formas_do_numero(Decimal(f["valor_bruto"]))):
            faltam.append(
                f"o dado {f['instituicao']} ({f['titulo']}) e citado, mas o numero "
                f"{f['valor']} nao aparece igual no texto: corrija o numero ou tire a citacao"
            )
    return faltam
