"""Da ideia solta a pauta investigada, por FRENTES.

Uma frente e uma linha de raciocinio com as proprias fontes: "o que o jornal
diz", "a minha suspeita", "quem acha que e produtividade". Cada uma tem um
papel:

* **discurso** — o que se diz: as fontes dela tem papel DISCURSO, que nunca
  entra no acervo nem serve de prova;
* **a_favor** — sustentaria a tese do autor;
* **contra** — a melhor evidencia contra a tese (mais vagas: a ideia so vale se
  sobreviver a isso);
* **alternativa** — outra explicacao, que nem a afirmacao nem a tese
  consideram.

A pessoa pode nomear e classificar as frentes no proprio texto ("considere a
frente X como contraria"); o modelo segue, e completa com as que faltarem.
Os links colados vao para a frente que o texto indicar (ou para o discurso).

1. **Ler** (`ler`): afirmacao, tese e frentes, com as buscas de cada uma.
2. **Pauta** (`criar_pauta`): sugerida, com o debate e as frentes em
   `Topic.debate`, que a geracao le (`content/debate.py`).
3. **Buscar** (`buscar`): cada frente na web, no OpenAlex e no YouTube (se for
   o caso); tudo como sugestao para a curadoria, marcado com a frente
   (`metricas["frente"]`) e o papel (`metricas["lado"]`). Ao aprovar um
   discurso, os links de fonte primaria que ele cita viram sugestao de
   evidencia (`fontes_web.seguir_citacoes`).
"""

from __future__ import annotations

import json
import logging
import re

from apps.ideias.models import Ideia

logger = logging.getLogger("publibot.ideias")

PAPEIS = ("discurso", "a_favor", "contra", "alternativa")
# Vagas de paginas e de estudos por frente, conforme o papel. O contra tem mais.
VAGAS = {"discurso": 4, "a_favor": 3, "contra": 5, "alternativa": 3}
ESTUDOS = {"discurso": 0, "a_favor": 2, "contra": 3, "alternativa": 2}
VIDEOS = 2
MAXIMO_DE_FRENTES = 6

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "afirmacao": {"type": "string"},
        "tese": {"type": "string"},
        "frentes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "papel": {"type": "string", "enum": list(PAPEIS)},
                    "descricao": {"type": "string"},
                    "buscas": {"type": "array", "items": {"type": "string"}},
                    "estudos": {"type": "boolean"},
                    "videos": {"type": "boolean"},
                    "links": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["nome", "papel", "buscas"],
            },
        },
        "onde": {"type": "string", "enum": ["site", "redes", "os_dois"]},
    },
    "required": ["titulo", "afirmacao", "tese", "frentes", "onde"],
}


class LeituraInvalida(ValueError):
    pass


def _json(texto: str) -> dict:
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```\w*\n|```$", "", texto).strip()
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise LeituraInvalida(f"o modelo nao devolveu JSON: {exc}") from exc
    if not isinstance(dados, dict) or not dados.get("afirmacao") or not dados.get("tese"):
        raise LeituraInvalida("a leitura veio sem a afirmacao ou sem a tese.")
    return dados


def _lista(valor, maximo: int) -> list[str]:
    return [str(x).strip()[:200] for x in (valor or []) if str(x).strip()][:maximo]


def _frentes(dados: dict, links_da_ideia: list[str]) -> list[dict]:
    """As frentes limpas. Sem discurso nem contra, a leitura nao serve para
    investigar: garante as duas. Link que o modelo inventou fica de fora (so os
    que a pessoa colou)."""
    frentes = []
    for item in dados.get("frentes") or []:
        if not isinstance(item, dict) or not str(item.get("nome", "")).strip():
            continue
        papel = item.get("papel") if item.get("papel") in PAPEIS else "alternativa"
        frentes.append(
            {
                "nome": str(item["nome"]).strip()[:80],
                "papel": papel,
                "descricao": str(item.get("descricao") or "").strip()[:300],
                "buscas": _lista(item.get("buscas"), 3),
                "estudos": bool(item.get("estudos", papel != "discurso")),
                "videos": bool(item.get("videos", False)),
                "links": [u for u in (item.get("links") or []) if u in links_da_ideia],
            }
        )
    frentes = frentes[:MAXIMO_DE_FRENTES]
    papeis = {f["papel"] for f in frentes}
    if "discurso" not in papeis:
        frentes.insert(
            0,
            {
                "nome": "O que se diz",
                "papel": "discurso",
                "descricao": dados.get("afirmacao", ""),
                "buscas": [str(dados.get("afirmacao", ""))[:120]],
                "estudos": False,
                "videos": False,
                "links": [],
            },
        )
    if "contra" not in papeis:
        frentes.append(
            {
                "nome": "Contra a tese",
                "papel": "contra",
                "descricao": f"A melhor evidencia contra: {dados.get('tese', '')}"[:300],
                "buscas": [str(dados.get("tese", ""))[:120]],
                "estudos": True,
                "videos": False,
                "links": [],
            }
        )
    return frentes


def ler(ideia: Ideia) -> dict:
    """A leitura do modelo, limpa e guardada na ideia."""
    from apps.content.inference import executar_prompt
    from apps.integrations.models import Site

    site = Site.objects.first()
    links = "\n".join(ideia.links or []) or "(nenhum)"
    resultado = executar_prompt(
        key="ideia_leitura",
        variaveis={
            "ideia": ideia.relato,
            "links": links,
            "negocio": _negocio() or "(nao informado)",
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        json_schema=ESQUEMA,
        com_convite=False,
    )
    dados = _json(resultado.texto)
    leitura = {
        "titulo": str(dados.get("titulo") or "")[:300],
        "afirmacao": str(dados["afirmacao"])[:500],
        "tese": str(dados["tese"])[:500],
        "frentes": _frentes(dados, ideia.links or []),
    }
    ideia.leitura = leitura
    ideia.onde = dados.get("onde") if dados.get("onde") in Ideia.Onde.values else Ideia.Onde.SITE
    ideia.save(update_fields=["leitura", "onde", "atualizada_em"])
    return leitura


def _negocio() -> str:
    from apps.editorial.models import PerfilDoNegocio

    perfil = PerfilDoNegocio.objects.first()
    return "; ".join(x for x in [getattr(perfil, "tema", ""), getattr(perfil, "oferta", "")] if x)[
        :500
    ]


def criar_pauta(ideia: Ideia):
    """A pauta sugerida, com o debate e as frentes. Ja existindo, atualiza."""
    from apps.content.models import Topic

    leitura = ideia.leitura
    frentes = [
        {"nome": f["nome"], "papel": f["papel"], "descricao": f.get("descricao", "")}
        for f in leitura.get("frentes", [])
    ]
    debate = {
        "afirmacao": leitura.get("afirmacao", ""),
        "tese": leitura.get("tese", ""),
        "frentes": frentes,
        "ideia": str(ideia.pk),
    }
    briefing = "\n".join(
        [
            f"O que se diz: {debate['afirmacao']}",
            f"O que o autor suspeita: {debate['tese']}",
            *[f"Frente '{f['nome']}' ({f['papel']}): {f['descricao']}" for f in frentes],
            "Investigar: a conclusao sai da evidencia, a favor ou contra o autor.",
        ]
    )
    if ideia.pauta_id:
        Topic.objects.filter(pk=ideia.pauta_id).update(debate=debate, briefing=briefing)
        ideia.pauta.refresh_from_db()
        return ideia.pauta
    pauta = Topic.objects.create(
        title=(leitura.get("titulo") or ideia.titulo)[:300],
        target_keyword="",
        briefing=briefing,
        status=Topic.Status.SUGGESTED,
        origin=Topic.Origin.IDEIA,
        debate=debate,
    )
    ideia.pauta = pauta
    ideia.save(update_fields=["pauta", "atualizada_em"])
    return pauta


def _marcar(candidatos: list, frente: dict) -> int:
    for candidato in candidatos:
        candidato.metricas = {
            **(candidato.metricas or {}),
            "frente": frente["nome"],
            "lado": frente["papel"],
        }
        candidato.save(update_fields=["metricas"])
    return len(candidatos)


def _links_colados(urls: list[str], pauta, *, discurso: bool) -> list:
    """Os links que a pessoa mandou, como sugestao da frente deles."""
    from apps.knowledge.fontes_web import _ja_conhecida, normalizar_caminho
    from apps.knowledge.models import CandidatoDeFonte

    novos = []
    for url in urls:
        if _ja_conhecida(url):
            continue
        novos.append(
            CandidatoDeFonte.objects.create(
                url=url[:500],
                titulo=url[:500],
                trecho="Link que voce mandou com a ideia.",
                dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                consulta="mandado com a ideia",
                pauta=pauta,
                papel=CandidatoDeFonte.Papel.DISCURSO if discurso else "",
            )
        )
    return novos


def buscar(ideia: Ideia) -> dict:
    """Cada frente por si: uma busca que falha nao derruba as outras."""
    from apps.knowledge.fontes_web import _buscar_paginas
    from apps.knowledge.models import CandidatoDeFonte

    pauta = ideia.pauta or criar_pauta(ideia)
    frentes = ideia.leitura.get("frentes") or []
    feito: dict = {"frentes": {}, "erros": []}

    def tentar(nome: str, funcao):
        try:
            return funcao()
        except Exception as exc:  # buscador fora, cota, base academica fora
            logger.warning("Ideia %s, busca %s: %s", ideia.pk, nome, exc)
            feito["erros"].append(f"{nome}: {exc}"[:300])
            return []

    # Link que o texto nao atribuiu a nenhuma frente: e o que a pessoa viu,
    # entao vai para o discurso.
    atribuidos = {u for f in frentes for u in f.get("links", [])}
    soltos = [u for u in ideia.links or [] if u not in atribuidos]

    for frente in frentes:
        papel = frente["papel"]
        discurso = papel == "discurso"
        consultas = frente.get("buscas") or [frente["nome"]]
        urls = frente.get("links", []) + (soltos if discurso else [])
        if discurso:
            soltos = []
        achados = _links_colados(urls, pauta, discurso=discurso)
        achados += tentar(
            frente["nome"],
            lambda consultas=consultas, papel=papel: _buscar_paginas(
                pauta,
                consultas,
                VAGAS[papel],
                papel=CandidatoDeFonte.Papel.DISCURSO if papel == "discurso" else "",
            ),
        )
        if frente.get("videos"):
            from apps.radar.youtube import buscar_para_pauta as videos

            achados += tentar(
                f"{frente['nome']} (videos)",
                lambda consultas=consultas, discurso=discurso: videos(
                    pauta,
                    falta=VIDEOS,
                    consulta=consultas[0],
                    papel=CandidatoDeFonte.Papel.DISCURSO if discurso else "",
                ),
            )
        if frente.get("estudos") and ESTUDOS[papel]:
            from apps.knowledge.academicos import buscar_para_pauta as estudos

            for consulta in consultas[:2]:
                achados += tentar(
                    f"{frente['nome']} (estudos)",
                    lambda consulta=consulta, papel=papel: estudos(
                        pauta, limite=ESTUDOS[papel], consulta=consulta
                    ),
                )
        feito["frentes"][frente["nome"]] = _marcar(achados, frente)

    if soltos:  # sem frente de discurso (nao acontece: _frentes garante uma)
        _links_colados(soltos, pauta, discurso=True)
    ideia.buscas = feito
    ideia.situacao = Ideia.Situacao.CURADORIA
    ideia.save(update_fields=["buscas", "situacao", "atualizada_em"])
    return feito


def processar(ideia: Ideia) -> None:
    """Ler, criar a pauta e buscar (o audio ja transcrito)."""
    ideia.situacao = Ideia.Situacao.LENDO
    ideia.erro = ""
    ideia.save(update_fields=["situacao", "erro", "atualizada_em"])
    try:
        ler(ideia)
        criar_pauta(ideia)
        ideia.situacao = Ideia.Situacao.BUSCANDO
        ideia.save(update_fields=["situacao", "atualizada_em"])
        buscar(ideia)
    except LeituraInvalida as exc:
        ideia.situacao = Ideia.Situacao.ERRO
        ideia.erro = str(exc)[:2000]
        ideia.save(update_fields=["situacao", "erro", "atualizada_em"])


def material_para_as_redes(ideia: Ideia) -> list[dict]:
    """O que a pauta tem de aprovado, como referencias do post das redes: o
    discurso marcado como discurso, a evidencia com o texto do acervo, e a
    frente de cada uma."""
    from apps.knowledge.models import CandidatoDeFonte

    saida = []
    aprovados = CandidatoDeFonte.objects.filter(
        pauta=ideia.pauta, situacao=CandidatoDeFonte.Situacao.APROVADO
    ).select_related("documento")
    for candidato in aprovados:
        if candidato.papel == CandidatoDeFonte.Papel.DISCURSO:
            texto = candidato.texto_extraido or candidato.trecho
        else:
            documento = candidato.documento
            texto = getattr(documento, "markdown_full", "") or candidato.trecho
        if not texto:
            continue
        saida.append(
            {
                "url": candidato.url,
                "titulo": candidato.titulo[:300],
                "site": (candidato.canal_nome or candidato.dominio)[:120],
                "texto": texto[:12000],
                "papel": candidato.papel or "evidencia",
                "frente": (candidato.metricas or {}).get("frente", ""),
            }
        )
    return saida


def fontes_por_frente(ideia: Ideia) -> list[dict]:
    """Para a tela: cada frente com quantas fontes esperam e quantas aprovadas."""
    from apps.knowledge.models import CandidatoDeFonte

    frentes = [
        {"nome": f["nome"], "papel": f["papel"], "descricao": f.get("descricao", "")}
        for f in (ideia.leitura or {}).get("frentes", [])
    ]
    por_nome = {f["nome"]: {**f, "esperando": 0, "aprovadas": 0} for f in frentes}
    if ideia.pauta_id:
        for candidato in CandidatoDeFonte.objects.filter(pauta_id=ideia.pauta_id).only(
            "situacao", "metricas"
        ):
            linha = por_nome.get((candidato.metricas or {}).get("frente", ""))
            if linha is None:
                continue
            if candidato.situacao == CandidatoDeFonte.Situacao.PENDENTE:
                linha["esperando"] += 1
            elif candidato.situacao == CandidatoDeFonte.Situacao.APROVADO:
                linha["aprovadas"] += 1
    return list(por_nome.values())
