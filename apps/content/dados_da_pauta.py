"""Dados publicos no artigo: da pauta a conferencia do numero.

1. Na pauta, as series muito proximas (embedding) entram sozinhas, ate 3;
   as proximas ficam sugeridas para "Usar". Tirar fica gravado: nao volta.
2. Os valores saem no recorte do site: o estado (se as regioes do Radar estao
   num estado so) e o Brasil, para comparar.
3. Na geracao, `fatos_do_artigo` congela os valores; cada secao recebe so os
   fatos mais proximos dela (no maximo MAXIMO_POR_TRECHO), como OPCAO.
4. Na montagem, [[DADO_N]] vira "(IBGE, 2019)" e entra nas referencias.
5. Na revisao, numero diferente do recebido bloqueia a aprovacao.
"""

from __future__ import annotations

import re
from decimal import Decimal

import numpy as np

PADRAO_DADO = re.compile(r"\[\[DADO_(\d+)\]\]")
# Teto do artigo inteiro (series x recortes), e o que cada chamada recebe.
MAXIMO_DE_FATOS = 8
MAXIMO_POR_TRECHO = 4


def _texto_da_pauta(pauta) -> str:
    return ". ".join(p for p in [pauta.title, pauta.target_keyword, pauta.briefing] if p)


def garantir_automaticos(pauta) -> None:
    """As series muito proximas da pauta entram sozinhas (sem repetir as tiradas)."""
    from apps.content.models import DadoDaPauta
    from apps.dados.catalogo import DISTANCIA_AUTOMATICA, MAXIMO_AUTOMATICOS, sugerir

    ja = set(pauta.dados.values_list("serie_id", flat=True))
    vagas = MAXIMO_AUTOMATICOS - pauta.dados.filter(automatico=True, tirado=False).count()
    if vagas <= 0:
        return
    for serie in sugerir(_texto_da_pauta(pauta), limite=vagas, excluir=ja):
        if serie.distancia <= DISTANCIA_AUTOMATICA:
            DadoDaPauta.objects.get_or_create(
                topic=pauta, serie=serie, defaults={"automatico": True}
            )


def sugestoes(pauta, limite: int = 5) -> list:
    """Series proximas que nao entraram (nem foram tiradas)."""
    from apps.dados.catalogo import sugerir

    ja = pauta.dados.values_list("serie_id", flat=True)
    return sugerir(_texto_da_pauta(pauta), limite=limite, excluir=ja)


def escolhidos(pauta) -> list:
    """Os dados que vao para o artigo, com os valores em cada recorte."""
    from apps.dados.catalogo import fato
    from apps.dados.locais import recortes_do_site

    garantir_automaticos(pauta)
    recortes = recortes_do_site()
    itens = []
    for dado in pauta.dados.filter(tirado=False).select_related("serie__instituicao"):
        valores = [f for f in (fato(dado.serie, local) for local in recortes) if f]
        itens.append({"dado": dado, "valores": valores})
    return itens


def fatos_do_artigo(article) -> list[dict]:
    """Os fatos do artigo, numerados. Congela na primeira vez: a revisao
    confere contra o numero que o modelo recebeu, mesmo que saia um novo."""
    if article.dados_usados or article.topic_id is None:
        return article.dados_usados
    fatos = []
    for item in escolhidos(article.topic):
        for valor in item["valores"]:
            if len(fatos) >= MAXIMO_DE_FATOS:
                break
            fatos.append(
                {
                    "n": len(fatos) + 1,
                    **valor,
                    "citado": False,
                    "automatico": item["dado"].automatico,
                }
            )
    if fatos:
        article.dados_usados = fatos
        article.save(update_fields=["dados_usados"])
    return fatos


def fatos_para_o_trecho(fatos: list[dict], foco: str, maximo: int = MAXIMO_POR_TRECHO) -> list:
    """Os fatos mais proximos do trecho (titulo e objetivo da secao). Poucos:
    vao todos. Muitos: a serie mais proxima primeiro, com os recortes dela."""
    if len(fatos) <= maximo or not foco:
        return fatos[:maximo]
    from apps.dados.models import Serie
    from apps.knowledge.embeddings import get_embedding_client

    try:
        alvo = np.asarray(get_embedding_client().embed_query(foco), dtype=np.float32)
    except Exception:
        return fatos[:maximo]
    vetores = {
        str(pk): np.asarray(v, dtype=np.float32)
        for pk, v in Serie.objects.filter(pk__in={f["serie_id"] for f in fatos})
        .exclude(vetor=None)
        .values_list("pk", "vetor")
    }

    def distancia(f) -> float:
        v = vetores.get(f["serie_id"])
        if v is None or not np.linalg.norm(v) or not np.linalg.norm(alvo):
            return 1.0
        return float(1 - np.dot(v, alvo) / (np.linalg.norm(v) * np.linalg.norm(alvo)))

    return sorted(fatos, key=lambda f: (distancia(f), f["n"]))[:maximo]


def bloco_para_o_prompt(fatos: list[dict]) -> str:
    """Os fatos para o modelo: texto nosso (nao de terceiros), como OPCAO."""
    if not fatos:
        return ""
    linhas = [
        "Dados oficiais OPCIONAIS. Use um so se ele responder, de forma natural, "
        "ao que este trecho ja esta dizendo. Se nao encaixar sem forcar, NAO use "
        "nenhum: e melhor um texto sem numero do que um numero fora de lugar. "
        "Nunca mude o assunto do trecho para caber um dado. Ao usar, escreva o "
        "numero EXATAMENTE como esta (sem arredondar), diga o local e o ano, e "
        "cite logo depois com o marcador indicado:"
    ]
    for f in sorted(fatos, key=lambda f: f["n"]):
        unidade = f" {f['unidade']}" if f.get("unidade") else ""
        linhas.append(
            f"- [[DADO_{f['n']}]] {f['titulo']} — {f['local']}, {f['periodo']}: "
            f"{f['valor']}{unidade} (fonte: {f['instituicao']})"
        )
    return "\n".join(linhas)


def com_dados(fontes: str, article, foco: str = "") -> str:
    """O bloco de fontes do prompt, com os dados do trecho no fim (se houver)."""
    bloco = bloco_para_o_prompt(fatos_para_o_trecho(fatos_do_artigo(article), foco))
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
                f"o dado {f['instituicao']} ({f['titulo']}, {f['local']}) e citado, mas o "
                f"numero {f['valor']} nao aparece igual no texto: corrija o numero ou "
                "tire a citacao"
            )
    return faltam
