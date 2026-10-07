"""Da ideia solta a pauta investigada.

1. **Ler** (`ler`): o modelo separa a afirmacao que circula, a tese do autor,
   outras explicacoes possiveis e as buscas de cada lado. Uma chamada.
2. **Pauta** (`criar_pauta`): nasce sugerida, com o debate gravado
   (`Topic.debate`), que a geracao le (`content/debate.py`).
3. **Buscar** (`buscar`), tudo como sugestao para a curadoria da pauta:
   * DISCURSO — o que se diz (paginas, videos, os links que a pessoa colou):
     papel DISCURSO, que nunca entra no acervo nem serve de prova;
   * A FAVOR — evidencia que sustentaria a tese;
   * CONTRA — mais consultas e mais vagas que o a favor: a melhor evidencia
     contra a tese e a favor das outras explicacoes. A ideia so vale se
     sobreviver a isso.
   Ao aprovar um discurso, os links de fonte primaria que ele cita viram
   sugestao de evidencia (`fontes_web.seguir_citacoes`): a investigacao mais
   barata que existe, sem modelo.
"""

from __future__ import annotations

import json
import logging
import re

from apps.ideias.models import Ideia

logger = logging.getLogger("publibot.ideias")

# Vagas por lado: o contra tem mais, de proposito.
VAGAS = {"discurso": 4, "a_favor": 3, "contra": 5}
ESTUDOS = {"a_favor": 2, "contra": 3}
VIDEOS_DO_DISCURSO = 2

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "afirmacao": {"type": "string"},
        "tese": {"type": "string"},
        "linhas": {"type": "array", "items": {"type": "string"}},
        "buscas": {
            "type": "object",
            "properties": {
                "discurso": {"type": "array", "items": {"type": "string"}},
                "a_favor": {"type": "array", "items": {"type": "string"}},
                "contra": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["discurso", "a_favor", "contra"],
        },
        "estudos": {"type": "boolean"},
        "videos": {"type": "boolean"},
        "onde": {"type": "string", "enum": ["site", "redes", "os_dois"]},
    },
    "required": ["titulo", "afirmacao", "tese", "buscas", "onde"],
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


def ler(ideia: Ideia) -> dict:
    """A leitura do modelo, limpa e guardada na ideia."""
    from apps.content.inference import executar_prompt
    from apps.integrations.models import Site

    site = Site.objects.first()
    negocio = _negocio()
    resultado = executar_prompt(
        key="ideia_leitura",
        variaveis={
            "ideia": ideia.relato,
            "negocio": negocio or "(nao informado)",
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        json_schema=ESQUEMA,
        com_convite=False,
    )
    dados = _json(resultado.texto)
    buscas = dados.get("buscas") or {}
    leitura = {
        "titulo": str(dados.get("titulo") or "")[:300],
        "afirmacao": str(dados["afirmacao"])[:500],
        "tese": str(dados["tese"])[:500],
        "linhas": _lista(dados.get("linhas"), 3),
        "buscas": {
            "discurso": _lista(buscas.get("discurso"), 3),
            "a_favor": _lista(buscas.get("a_favor"), 2),
            "contra": _lista(buscas.get("contra"), 4),
        },
        "estudos": bool(dados.get("estudos", True)),
        "videos": bool(dados.get("videos", False)),
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
    """A pauta sugerida, com o debate. Ja existindo, so atualiza o debate."""
    from apps.content.models import Topic

    leitura = ideia.leitura
    debate = {
        "afirmacao": leitura.get("afirmacao", ""),
        "tese": leitura.get("tese", ""),
        "linhas": leitura.get("linhas", []),
        "ideia": str(ideia.pk),
    }
    briefing = "\n".join(
        [
            f"O que se diz: {debate['afirmacao']}",
            f"O que o autor suspeita: {debate['tese']}",
            *[f"Outra explicacao possivel: {x}" for x in debate["linhas"]],
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


def _marcar(candidatos: list, lado: str) -> int:
    for candidato in candidatos:
        candidato.metricas = {**(candidato.metricas or {}), "lado": lado}
        candidato.save(update_fields=["metricas"])
    return len(candidatos)


def _links_colados(ideia: Ideia, pauta) -> list:
    """Os links que a pessoa mandou com a ideia: sugeridos como discurso."""
    from apps.knowledge.fontes_web import _ja_conhecida, normalizar_caminho
    from apps.knowledge.models import CandidatoDeFonte

    novos = []
    for url in ideia.links or []:
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
                papel=CandidatoDeFonte.Papel.DISCURSO,
            )
        )
    return novos


def buscar(ideia: Ideia) -> dict:
    """As tres buscas. Cada uma por si: uma que falha nao derruba as outras."""
    from apps.knowledge.fontes_web import _buscar_paginas
    from apps.knowledge.models import CandidatoDeFonte

    pauta = ideia.pauta or criar_pauta(ideia)
    leitura = ideia.leitura
    consultas = leitura.get("buscas") or {}
    feito: dict = {"erros": []}

    def tentar(nome: str, funcao):
        try:
            return funcao()
        except Exception as exc:  # buscador fora, cota, base academica fora
            logger.warning("Ideia %s, busca %s: %s", ideia.pk, nome, exc)
            feito["erros"].append(f"{nome}: {exc}"[:300])
            return []

    discurso = _links_colados(ideia, pauta)
    discurso += tentar(
        "discurso",
        lambda: _buscar_paginas(
            pauta,
            consultas.get("discurso") or [ideia.titulo],
            VAGAS["discurso"],
            papel=CandidatoDeFonte.Papel.DISCURSO,
        ),
    )
    if leitura.get("videos"):
        from apps.radar.youtube import buscar_para_pauta as videos

        discurso += tentar(
            "videos",
            lambda: videos(
                pauta,
                falta=VIDEOS_DO_DISCURSO,
                consulta=(consultas.get("discurso") or [ideia.titulo])[0],
                papel=CandidatoDeFonte.Papel.DISCURSO,
            ),
        )
    feito["discurso"] = _marcar(discurso, "discurso")

    for lado in ("a_favor", "contra"):
        achados = tentar(
            lado, lambda lado=lado: _buscar_paginas(pauta, consultas.get(lado) or [], VAGAS[lado])
        )
        if leitura.get("estudos", True):
            from apps.knowledge.academicos import buscar_para_pauta as estudos

            for consulta in (consultas.get(lado) or [])[:2]:
                achados += tentar(
                    f"estudos {lado}",
                    lambda consulta=consulta, lado=lado: estudos(
                        pauta, limite=ESTUDOS[lado], consulta=consulta
                    ),
                )
        feito[lado] = _marcar(achados, lado)

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
    discurso marcado como discurso e a evidencia com o texto do acervo."""
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
            }
        )
    return saida
