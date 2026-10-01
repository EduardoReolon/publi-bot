"""Pesquisa da pauta: artigos cientificos achados por sentido, agrupados em angulos.

O segundo jeito de embasar um artigo (o primeiro, o acervo curado antes,
continua). Aqui a pesquisa vem antes da curadoria:

1. **Hipoteses** — o modelo escreve, em ingles, 3 paragrafos como se fossem
   resumos de estudos que responderiam a pauta, os mais diferentes possivel; se
   a ideia central e uma crenca que algum estudo poderia contradizer, mais 2
   paragrafos no sentido contrario (HyDE: a busca vetorial acha melhor a partir
   de um texto parecido com o resultado do que a partir de uma pergunta).
2. **Busca semantica** no OpenAlex com cada paragrafo.
3. **Bola de neve** — os melhores puxam os "artigos relacionados" do OpenAlex.
4. **Ordem** — proximidade com a pauta (o nosso modelo, multilingue), percentil
   de citacao na area e idade. Retratado nunca entra.
5. **Angulos** — os escolhidos sao agrupados pelos vetores dos resumos
   (k-means); os achados pelas hipoteses contrarias formam o "contraponto".
6. **Sintese por angulo** — o modelo le os resumos de cada angulo, diz o que
   eles sustentam e pede (PEDIDOS) o que precisaria do texto completo. So esses
   tem o PDF procurado.

Os escolhidos viram fonte pelo resumo (documento do resumo, curado
automaticamente: e dado do registro, nao texto lido de PDF). A geracao no fluxo
da pesquisa usa so eles (`referencias.trechos_da_pauta`).
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter

import httpx
import numpy as np
from django.utils import timezone

logger = logging.getLogger("publibot.knowledge")

POR_HIPOTESE = 25
ESCOLHIDOS = 15
NO_MAXIMO_CONTRARIOS = 4
RELACIONADOS_DOS_MELHORES = 3
RELACIONADOS_POR_ARTIGO = 5
TRECHOS_DA_PESQUISA = 12
PAUSA_DA_SEMANTICA = 1.1  # o OpenAlex aceita 1 busca semantica por segundo
PESOS = {"sentido": 0.6, "citacoes": 0.25, "idade": 0.15}
FILTRO = "type:article|review,has_abstract:true,is_retracted:false"
CAMPOS_EXTRAS = ",related_works,relevance_score"
MARCA_NA_ORIENTACAO = "--- Pesquisa de artigos (gerado) ---"

# Palavras vazias em ingles, para nomear os angulos pelos titulos.
_VAZIAS_EN = set(
    """
    the a an of in on for to and or with by from at as is are was were be been
    its their this that these those using use based study analysis effect effects
    role impact case evidence new how why what when between among toward towards
    into through over under via vs versus model approach research review
    """.split()
)


class PesquisaIndisponivel(RuntimeError):
    """O OpenAlex nao respondeu: a pesquisa tenta de novo depois."""


# -- Hipoteses ---------------------------------------------------------------
def hipoteses(pauta) -> dict:
    """{"angulos": [{angulo, paragrafo}], "contrarias": [...], "do_modelo": bool}.

    Sem modelo configurado, uma hipotese so: o titulo e a orientacao da pauta
    (a busca semantica entende portugues razoavelmente, mas prefere ingles).
    """
    import json

    from apps.content.inference import SemModeloConfigurado, executar_prompt
    from apps.integrations.models import Site
    from apps.ops.orchestrator import PassoAdiado

    orientacao = _orientacao_sem_pesquisa(pauta.briefing or "")[:1500]
    try:
        resultado = executar_prompt(
            key="research_hypotheses",
            variaveis={
                "pauta": pauta.title,
                "palavra_chave": pauta.target_keyword or pauta.title,
                "orientacao": orientacao or "-",
            },
            site=Site.objects.first(),
            json_schema={
                "type": "object",
                "properties": {
                    "angulos": {"type": "array", "items": {"type": "object"}},
                    "refutavel": {"type": "boolean"},
                    "contrarias": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["angulos", "refutavel", "contrarias"],
            },
        )
        dados = json.loads(resultado.texto)
    except (SemModeloConfigurado, PassoAdiado, LookupError, ValueError) as exc:
        logger.info("Pauta %s: hipoteses sem modelo (%s).", pauta.pk, exc)
        return {
            "angulos": [{"angulo": pauta.title, "paragrafo": f"{pauta.title}. {orientacao}"}],
            "contrarias": [],
            "do_modelo": False,
        }

    def limpar(itens):
        return [
            {"angulo": str(i.get("angulo", ""))[:120], "paragrafo": str(i["paragrafo"])[:1900]}
            for i in itens or []
            if isinstance(i, dict) and str(i.get("paragrafo", "")).strip()
        ]

    return {
        "angulos": limpar(dados.get("angulos"))[:3],
        "contrarias": limpar(dados.get("contrarias"))[:2] if dados.get("refutavel") else [],
        "do_modelo": True,
    }


# -- OpenAlex ----------------------------------------------------------------
def _get(params: dict, *, consulta: str, endpoint: str) -> list[dict]:
    from apps.knowledge.academicos import CAMPOS, OPENALEX, _parametros_da_conta, _registrar
    from apps.radar.models import ChamadaExterna

    params = {**params, "select": CAMPOS + CAMPOS_EXTRAS, **_parametros_da_conta()}
    try:
        resposta = httpx.get(OPENALEX, params=params, timeout=60)
        resposta.raise_for_status()
        itens = resposta.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        _registrar(ChamadaExterna.Provedor.OPENALEX, endpoint, consulta[:200], erro=str(exc)[:500])
        raise PesquisaIndisponivel(f"OpenAlex nao respondeu: {exc}") from exc
    _registrar(ChamadaExterna.Provedor.OPENALEX, endpoint, consulta[:200], itens=len(itens))
    return itens


def busca_semantica(texto: str, *, quantos: int = POR_HIPOTESE) -> list[dict]:
    return _get(
        {"search.semantic": texto[:2000], "per_page": min(quantos, 50), "filter": FILTRO},
        consulta=texto,
        endpoint="works/semantic",
    )


def por_ids(ids: list[str]) -> list[dict]:
    """Os trabalhos destes ids do OpenAlex (W123...), numa chamada."""
    curtos = [i.rsplit("/", 1)[-1] for i in ids if i][:50]
    if not curtos:
        return []
    return _get(
        {"filter": f"openalex:{'|'.join(curtos)},{FILTRO}", "per_page": len(curtos)},
        consulta="relacionados",
        endpoint="works/relacionados",
    )


# -- Ordem -------------------------------------------------------------------
def _chave(dados: dict) -> str:
    from apps.knowledge.academicos import _doi, _normalizar

    return _doi(dados.get("doi") or "").lower() or _normalizar(dados.get("display_name") or "")


def _idade(ano) -> float:
    """1 ate 10 anos; cai ate 0 aos 30."""
    if not ano:
        return 0.3
    anos = max(0, timezone.now().year - int(ano))
    return 1.0 if anos <= 10 else max(0.0, 1 - (anos - 10) / 20)


def _citacoes(dados: dict) -> float:
    percentil = (dados.get("citation_normalized_percentile") or {}).get("value")
    if percentil is not None:
        return float(percentil)
    return min(1.0, math.log10(1 + (dados.get("cited_by_count") or 0)) / 3)


def _texto(dados: dict) -> str:
    from apps.knowledge.academicos import _resumo

    return f"{dados.get('display_name', '')}. {_resumo(dados.get('abstract_inverted_index'))}"


def ordenar(pauta, achados: list[dict]) -> tuple[list[dict], np.ndarray]:
    """Os achados com a nota final, do melhor ao pior, e os vetores (mesma ordem)."""
    from apps.knowledge.embeddings import get_embedding_client, vetorizar_passagens
    from apps.knowledge.referencias import consulta_da_pauta

    if not achados:
        return [], np.zeros((0, 1))
    vetores = np.asarray(
        vetorizar_passagens([_texto(d)[:2500] for d in achados], dono=f"pesquisa:{pauta.pk}"),
        dtype=np.float32,
    )
    alvo = np.asarray(
        get_embedding_client().embed_query(consulta_da_pauta(pauta)[:2000]), dtype=np.float32
    )
    sentido = vetores @ alvo
    baixo, alto = float(sentido.min()), float(sentido.max())
    faixa = (alto - baixo) or 1.0
    for d, s in zip(achados, sentido, strict=True):
        d["_sentido"] = round(float(s), 4)
        d["_nota"] = round(
            PESOS["sentido"] * (float(s) - baixo) / faixa
            + PESOS["citacoes"] * _citacoes(d)
            + PESOS["idade"] * _idade(d.get("publication_year")),
            4,
        )
    ordem = sorted(range(len(achados)), key=lambda i: -achados[i]["_nota"])
    return [achados[i] for i in ordem], vetores[ordem]


def escolher(ordenados: list[dict], vetores: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """Os melhores, com vaga reservada para o contraponto (achado pelas
    hipoteses contrarias), se houver."""
    contrarios = [i for i, d in enumerate(ordenados) if d.get("_contraria")][:NO_MAXIMO_CONTRARIOS]
    resto = [i for i in range(len(ordenados)) if i not in contrarios]
    indices = sorted(contrarios + resto[: max(0, ESCOLHIDOS - len(contrarios))])
    return [ordenados[i] for i in indices], vetores[indices]


# -- Angulos -----------------------------------------------------------------
def _kmeans(vetores: np.ndarray, k: int, *, voltas: int = 25) -> np.ndarray:
    """k-means com inicio k-means++ deterministico (o primeiro centro e o melhor)."""
    centros = [vetores[0]]
    for _ in range(1, k):
        distancias = np.min([1 - vetores @ c for c in centros], axis=0)
        centros.append(vetores[int(np.argmax(distancias))])
    centros = np.asarray(centros)
    rotulos = np.zeros(len(vetores), dtype=int)
    for _ in range(voltas):
        novos = np.argmax(vetores @ centros.T, axis=1)
        if np.array_equal(novos, rotulos) and _ > 0:
            break
        rotulos = novos
        for j in range(k):
            membros = vetores[rotulos == j]
            if len(membros):
                media = membros.mean(axis=0)
                centros[j] = media / (np.linalg.norm(media) or 1)
    return rotulos


def _nome(membros: list[dict], termos: list[str]) -> str:
    topicos = Counter(
        (d.get("primary_topic") or {}).get("display_name")
        for d in membros
        if d.get("primary_topic")
    )
    principal = topicos.most_common(1)[0][0] if topicos else ""
    distintos = ", ".join(termos[:3])
    return f"{principal} — {distintos}" if principal and distintos else (principal or distintos)


def angulos(escolhidos: list[dict], vetores: np.ndarray) -> list[dict]:
    """[{nome, contraponto, artigos: [indices]}]. O contraponto e um angulo a parte."""
    from apps.radar.oportunidades import termos_distintivos

    contrarios = [i for i, d in enumerate(escolhidos) if d.get("_contraria")]
    normais = [i for i in range(len(escolhidos)) if i not in contrarios]
    grupos: dict[str, list[int]] = {}
    if normais:
        k = max(1, min(3, len(normais) // 3))
        rotulos = _kmeans(vetores[normais], k) if k > 1 else np.zeros(len(normais), dtype=int)
        for i, rotulo in zip(normais, rotulos, strict=True):
            grupos.setdefault(f"a{rotulo}", []).append(i)
    if contrarios:
        grupos["contraponto"] = contrarios

    def titulos(indices):
        return [
            " ".join(
                p
                for p in re.findall(r"[a-z]+", (escolhidos[i].get("display_name") or "").lower())
                if p not in _VAZIAS_EN
            )
            for i in indices
        ]

    termos = termos_distintivos({g: titulos(ix) for g, ix in grupos.items()}, quantos=3)
    saida = []
    for g, indices in grupos.items():
        membros = [escolhidos[i] for i in indices]
        saida.append(
            {
                "nome": "Contraponto"
                if g == "contraponto"
                else _nome(membros, termos.get(g, [])) or "Angulo",
                "contraponto": g == "contraponto",
                "artigos": sorted(indices, key=lambda i: -escolhidos[i]["_nota"]),
            }
        )
    saida.sort(key=lambda a: (a["contraponto"], -len(a["artigos"])))
    return saida


# -- Fontes ------------------------------------------------------------------
def registrar_fontes(pauta, escolhidos: list[dict]) -> list[str]:
    """Cada escolhido vira candidato aprovado com o documento do resumo, curado
    automaticamente. Devolve os ids dos candidatos (na ordem)."""
    from apps.knowledge.academicos import ler_trabalho, registrar
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.provisorias import documento_do_resumo
    from apps.knowledge.tasks import pedir_indexacao

    ids = []
    for dados in escolhidos:
        trabalho = ler_trabalho(dados)
        candidato = registrar(
            trabalho, consulta=f"pesquisa: {pauta.title}"[:500], origem="openalex", pauta=pauta
        )
        if candidato is None:  # ja conhecido: reaproveita
            candidato = (
                CandidatoDeFonte.objects.filter(doi=trabalho.doi).first() if trabalho.doi else None
            ) or CandidatoDeFonte.objects.filter(url=trabalho.url[:500]).first()
        if candidato is None:
            continue
        documento = candidato.documento or documento_do_resumo(candidato)
        if documento is None:
            continue
        if candidato.situacao == CandidatoDeFonte.Situacao.PENDENTE:
            candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
            candidato.motivo = "Escolhido pela pesquisa da pauta."
            candidato.decidido_em = timezone.now()
            candidato.save(update_fields=["situacao", "motivo", "decidido_em"])
        if documento.status == documento.Status.PENDING_CURATION and not documento.chunks.exists():
            from apps.knowledge.blocos import preparar_blocos

            blocos = {b.ordem for b in preparar_blocos(documento) if b.paragrafos}
            if blocos:
                # Resumo do registro: curado automaticamente ao ser indexado.
                # Curto: com o worker fora (ou sem a rota), vai no servidor.
                pedir_indexacao(documento, blocos=blocos, concluir=True, por=None, local=True)
        ids.append(str(candidato.pk))
    return ids


def documentos_da_pesquisa(pauta) -> list:
    """Os documentos dos artigos da pesquisa (o PDF, quando ja substituiu o resumo)."""
    from apps.knowledge.models import CandidatoDeFonte

    ids = ((pauta.busca_de_fontes or {}).get("pesquisa") or {}).get("candidatos", [])
    return list(
        CandidatoDeFonte.objects.filter(pk__in=ids, documento__isnull=False).values_list(
            "documento_id", flat=True
        )
    )


# -- Sinteses ----------------------------------------------------------------
def _resumos(escolhidos: list[dict], indices: list[int]) -> str:
    from apps.knowledge.academicos import _resumo

    partes = []
    for i in indices:
        d = escolhidos[i]
        partes.append(
            f'<fonte numero="{i + 1}" ano="{d.get("publication_year") or ""}" '
            f'citacoes="{d.get("cited_by_count") or 0}">\n{d.get("display_name", "")}\n'
            f"{_resumo(d.get('abstract_inverted_index'))[:2500]}\n</fonte>"
        )
    return "\n\n".join(partes)


def sintese(pauta, angulo: dict, escolhidos: list[dict]) -> dict:
    """{"sintese", "pedidos": [{artigo, o_que}]}, ou vazio sem modelo."""
    import json

    from apps.content.inference import SemModeloConfigurado, executar_prompt
    from apps.integrations.models import Site
    from apps.ops.orchestrator import PassoAdiado

    site = Site.objects.first()
    try:
        resultado = executar_prompt(
            key="research_synthesis",
            variaveis={
                "pauta": pauta.title,
                "angulo": angulo["nome"],
                "resumos": _resumos(escolhidos, angulo["artigos"]),
                "idioma": getattr(site, "content_language", "") or "pt-BR",
            },
            site=site,
            json_schema={
                "type": "object",
                "properties": {
                    "sintese": {"type": "string"},
                    "pedidos": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["sintese", "pedidos"],
            },
        )
        dados = json.loads(resultado.texto)
    except (SemModeloConfigurado, PassoAdiado, LookupError, ValueError) as exc:
        logger.info("Pauta %s: sintese sem modelo (%s).", pauta.pk, exc)
        return {}
    validos = {i + 1 for i in angulo["artigos"]}
    pedidos = []
    for pedido in dados.get("pedidos") or []:
        try:
            numero = int(pedido.get("artigo"))
        except (TypeError, ValueError):
            continue
        if numero in validos and str(pedido.get("o_que", "")).strip():
            pedidos.append({"artigo": numero, "o_que": str(pedido["o_que"])[:300]})
    return {"sintese": str(dados.get("sintese", ""))[:2000], "pedidos": pedidos[:3]}


def _pdf_aberto(dados: dict) -> str:
    from apps.knowledge.academicos import ler_trabalho, pdf_pelo_unpaywall

    trabalho = ler_trabalho(dados)
    return trabalho.pdf_url or pdf_pelo_unpaywall(trabalho.doi)


def _orientacao_sem_pesquisa(texto: str) -> str:
    return texto.split(MARCA_NA_ORIENTACAO)[0].rstrip()


def _orientacao_com_pesquisa(pauta, angulos_: list[dict]) -> str:
    """A orientacao da pauta leva os angulos e as sinteses: e dela que o
    planejamento do artigo parte. Refeita a cada pesquisa."""
    linhas = [MARCA_NA_ORIENTACAO]
    for angulo in angulos_:
        linhas.append(f"Angulo: {angulo['nome']}")
        if angulo.get("sintese"):
            linhas.append(angulo["sintese"])
    return f"{_orientacao_sem_pesquisa(pauta.briefing or '')}\n\n" + "\n".join(linhas)


# -- A pesquisa inteira --------------------------------------------------------
def pesquisar(pauta) -> dict:
    """Roda a pesquisa e grava em `busca_de_fontes["pesquisa"]`."""
    from apps.knowledge.referencias import registrar

    registrar(pauta, "pesquisa", situacao="pesquisando", em=timezone.now().isoformat(), erro="")
    hip = hipoteses(pauta)
    achados: dict[str, dict] = {}
    consultas = [(h, False) for h in hip["angulos"]] + [(h, True) for h in hip["contrarias"]]
    for n, (hipotese, contraria) in enumerate(consultas):
        if n:
            time.sleep(PAUSA_DA_SEMANTICA)
        for dados in busca_semantica(hipotese["paragrafo"]):
            chave = _chave(dados)
            if not chave or chave in achados:
                continue
            dados["_contraria"] = contraria
            dados["_hipotese"] = hipotese["angulo"]
            achados[chave] = dados

    ordenados, vetores = ordenar(pauta, list(achados.values()))
    # Bola de neve: os relacionados dos melhores, um nivel so.
    relacionados = []
    for dados in ordenados[:RELACIONADOS_DOS_MELHORES]:
        relacionados += (dados.get("related_works") or [])[:RELACIONADOS_POR_ARTIGO]
    novos = []
    for dados in por_ids(relacionados) if relacionados else []:
        chave = _chave(dados)
        if chave and chave not in achados:
            dados["_contraria"] = False
            dados["_hipotese"] = "relacionado"
            achados[chave] = dados
            novos.append(dados)
    if novos:
        ordenados, vetores = ordenar(pauta, list(achados.values()))

    escolhidos, vetores_escolhidos = escolher(ordenados, vetores)
    grupos = angulos(escolhidos, vetores_escolhidos)
    candidatos = registrar_fontes(pauta, escolhidos)

    pdfs: dict[int, str] = {}
    for angulo in grupos:
        angulo.update(sintese(pauta, angulo, escolhidos))
        for pedido in angulo.get("pedidos", []):
            indice = pedido["artigo"] - 1
            if indice not in pdfs:
                pdfs[indice] = _pdf_aberto(escolhidos[indice])
            pedido["pdf_aberto"] = pdfs[indice]

    pauta.briefing = _orientacao_com_pesquisa(pauta, grupos)
    pauta.save(update_fields=["briefing"])

    artigos = [
        {
            "numero": i + 1,
            "titulo": (d.get("display_name") or "")[:300],
            "ano": d.get("publication_year"),
            "citacoes": d.get("cited_by_count") or 0,
            "percentil": (d.get("citation_normalized_percentile") or {}).get("value"),
            "doi": (d.get("doi") or "")[:200],
            "topico": (d.get("primary_topic") or {}).get("display_name", ""),
            "hipotese": d.get("_hipotese", ""),
            "contraria": bool(d.get("_contraria")),
            "nota": d.get("_nota"),
            "candidato": candidatos[i] if i < len(candidatos) else "",
        }
        for i, d in enumerate(escolhidos)
    ]
    resultado = {
        "situacao": "pronta",
        "em": timezone.now().isoformat(),
        "hipoteses": hip,
        "achados": len(achados),
        "relacionados": len(novos),
        "artigos": artigos,
        "angulos": grupos,
        "candidatos": candidatos,
        "erro": "",
    }
    registrar(pauta, "pesquisa", **resultado)
    return resultado


def pronta_para_gerar(pauta) -> str:
    """Vazio se o fluxo da pesquisa pode gerar; senao, o motivo."""
    from apps.knowledge.models import Document

    pesquisa = (pauta.busca_de_fontes or {}).get("pesquisa") or {}
    if pesquisa.get("situacao") != "pronta":
        return "a pesquisa de artigos ainda nao terminou (ou nao foi feita)."
    documentos = documentos_da_pesquisa(pauta)
    if not documentos:
        return "a pesquisa nao achou artigos com resumo para esta pauta."
    sem_indice = Document.objects.filter(pk__in=documentos, chunks__isnull=True).count()
    if sem_indice == len(documentos):
        return "os resumos ainda estao sendo vetorizados; tente em instantes."
    return ""
