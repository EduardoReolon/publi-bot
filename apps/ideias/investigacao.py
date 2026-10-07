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

from django.db import transaction

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


def _frentes_atuais(ideia: Ideia) -> list[dict]:
    return list((ideia.leitura or {}).get("frentes") or [])


def _bloco_do_refino(ideia: Ideia) -> str:
    """Investigar de novo e refinamento: a leitura atual vai junto, com as
    frentes FIXAS (as fontes ja achadas e decididas estao ligadas a elas)."""
    frentes = _frentes_atuais(ideia)
    if not frentes:
        return ""
    leitura = ideia.leitura
    linhas = [
        "",
        "REFINAMENTO DE UMA LEITURA QUE JA EXISTE. A leitura atual:",
        f"- O que se diz: {leitura.get('afirmacao', '')}",
        f"- A suspeita: {leitura.get('tese', '')}",
        "- Frentes ja definidas (MANTENHA o nome e o papel de cada uma, exatamente; "
        "pode melhorar a descricao e as buscas; pode ACRESCENTAR frentes novas; "
        "nao apague nem renomeie nenhuma):",
        *[f"  * {f['nome']} ({f['papel']}): {f.get('descricao', '')}" for f in frentes],
    ]
    pedido = (ideia.refino or {}).get("pedido", "").strip()
    if pedido:
        linhas.append(f"O que o autor quer refinar agora: {pedido}")
    return "\n".join(linhas)


def _variaveis(ideia: Ideia) -> dict:
    from apps.integrations.models import Site

    site = Site.objects.first()
    return {
        "ideia": ideia.relato + _bloco_do_refino(ideia),
        "links": "\n".join(ideia.links or []) or "(nenhum)",
        "negocio": _negocio() or "(nao informado)",
        "idioma": getattr(site, "content_language", "") or "pt-BR",
    }


def _guardar_leitura(ideia: Ideia, dados: dict, *, links_permitidos=None) -> dict:
    """A leitura limpa (as mesmas regras, venha do modelo daqui ou da outra IA)."""
    permitidos = (ideia.links or []) if links_permitidos is None else links_permitidos
    antigas = _frentes_atuais(ideia)
    novas = _frentes(dados, permitidos)
    frentes, a_buscar = fundir_frentes(antigas, novas) if antigas else (novas, None)
    if antigas and (ideia.refino or {}).get("buscar_existentes"):
        a_buscar = [f["nome"] for f in frentes]
    leitura = {
        "titulo": str(dados.get("titulo") or "")[:300] or (ideia.leitura or {}).get("titulo", ""),
        "afirmacao": str(dados["afirmacao"])[:500],
        "tese": str(dados["tese"])[:500],
        "frentes": frentes,
    }
    ideia.leitura = leitura
    ideia.onde = dados.get("onde") if dados.get("onde") in Ideia.Onde.values else Ideia.Onde.SITE
    ideia.refino = {**(ideia.refino or {}), "buscar": a_buscar} if antigas else {}
    ideia.save(update_fields=["leitura", "onde", "refino", "atualizada_em"])
    return leitura


# As frentes que `_frentes` acrescenta quando a leitura nao traz discurso ou contra.
_AUTOMATICAS = {"O que se diz": "discurso", "Contra a tese": "contra"}


def fundir_frentes(antigas: list[dict], novas: list[dict]) -> tuple[list[dict], list[str]]:
    """Refinamento: as frentes antigas FICAM (nome e papel; as fontes estao
    ligadas a elas), com a descricao e as buscas melhoradas quando a leitura
    nova traz a mesma frente; as frentes novas entram no fim. Devolve
    (frentes, nomes das novas: so elas sao buscadas)."""
    por_nome = {f["nome"].casefold(): f for f in novas}
    papeis_antigos = {f["papel"] for f in antigas}
    saida = []
    for antiga in antigas:
        nova = por_nome.pop(antiga["nome"].casefold(), None) or {}
        melhorado = {k: nova[k] for k in ("descricao", "buscas", "links") if nova.get(k)}
        saida.append({**antiga, **melhorado, "nome": antiga["nome"], "papel": antiga["papel"]})
    acrescentadas = []
    for nova in novas:
        if nova["nome"].casefold() not in por_nome:
            continue  # ja fundida numa antiga
        if _AUTOMATICAS.get(nova["nome"]) in papeis_antigos:
            continue  # a frente automatica nao duplica o papel que ja existe
        if len(saida) >= MAXIMO_DE_FRENTES:
            break
        saida.append(nova)
        acrescentadas.append(nova["nome"])
    return saida, acrescentadas


def ler(ideia: Ideia) -> dict:
    """A leitura do modelo daqui, limpa e guardada na ideia."""
    from apps.content.inference import executar_prompt
    from apps.integrations.models import Site

    resultado = executar_prompt(
        key="ideia_leitura",
        variaveis=_variaveis(ideia),
        site=Site.objects.first(),
        json_schema=ESQUEMA,
        com_convite=False,
    )
    return _guardar_leitura(ideia, _json(resultado.texto))


# -- A leitura feita por outra IA (maior, com pesquisa na web) ---------------------
PARA_A_OUTRA_IA = (
    "\n\nVOCE PODE PESQUISAR NA WEB: se o assunto for recente ou voce nao tiver certeza, "
    "pesquise para entender o contexto e montar buscas melhores. Em 'links' de cada frente "
    "voce pode incluir links REAIS que encontrou (alem dos do autor), copiados exatamente: "
    "nenhum deles e usado sem passar pela curadoria do autor. Nunca invente link.\n\n"
    "ANTES DO JSON, CONVERSE COM O AUTOR. A ideia vem escrita as pressas, e um erro de "
    "leitura aqui contamina toda a investigacao. Entao:\n"
    "1. Reescreva com as suas palavras: o que se diz (e quem diz), qual e a suspeita do "
    "autor e qual CONTRASTE ele quer mostrar.\n"
    "2. Aponte as ambiguidades e as palavras que podem ser lidas de dois jeitos (por "
    "exemplo: 'falta' de que? de quem constroi ou de quem usa? a critica e ao fato ou ao "
    "enquadramento?).\n"
    "3. Pergunte o que for preciso, poucas perguntas por vez, e espere as respostas.\n"
    "4. Proponha as frentes e confirme com o autor.\n"
    "So depois que ele confirmar, responda com o JSON, num bloco de codigo (e e so o "
    "bloco que ele vai colar de volta)."
)


def pedido_para_outra_ia(ideia: Ideia) -> str:
    """O mesmo pedido do modelo daqui (a versao em uso do prompt `ideia_leitura`),
    com licenca para pesquisar, para colar numa IA grande."""
    from apps.content.services import escolher_versao_de_prompt

    versao = escolher_versao_de_prompt("ideia_leitura")
    corpo = versao.user_prompt_template.format(**_variaveis(ideia))
    return f"{versao.system_prompt}{PARA_A_OUTRA_IA}\n\n{corpo}"


def aplicar_resposta(ideia: Ideia, texto: str) -> dict:
    """A resposta colada da outra IA: o JSON (achado no meio do texto, se vier
    com conversa em volta), conferido com as mesmas regras. Os links que ela
    achou valem como sugestao (passam pela curadoria)."""
    inicio, fim = (texto or "").find("{"), (texto or "").rfind("}")
    if inicio < 0 or fim <= inicio:
        raise LeituraInvalida("nao achei o JSON na resposta colada.")
    dados = _json(texto[inicio : fim + 1])
    achados = [
        str(u).strip()
        for f in dados.get("frentes") or []
        if isinstance(f, dict)
        for u in f.get("links") or []
        if str(u).strip().startswith(("http://", "https://"))
    ]
    leitura = _guardar_leitura(ideia, dados, links_permitidos=[*(ideia.links or []), *achados])
    ideia.leitura = {**leitura, "pela_outra_ia": True}
    ideia.save(update_fields=["leitura", "atualizada_em"])
    return ideia.leitura


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


def _links_colados(urls: list[str], pauta, *, discurso: bool, do_autor=()) -> list:
    """Os links que a pessoa mandou, como sugestao da frente deles."""
    from apps.knowledge.fontes_web import _ja_conhecida, normalizar_caminho
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.videos import tratar_como_video

    novos = []
    for url in urls:
        if _ja_conhecida(url):
            continue
        novos.append(
            tratar_como_video(
                CandidatoDeFonte.objects.create(
                    url=url[:500],
                    titulo=url[:500],
                    trecho="Link que voce mandou com a ideia."
                    if url in do_autor
                    else "Achado pela outra IA ao preparar a ideia (confira).",
                    dominio=normalizar_caminho(url).split("/", 1)[0][:200],
                    consulta="mandado com a ideia" if url in do_autor else "achado pela outra IA",
                    pauta=pauta,
                    papel=CandidatoDeFonte.Papel.DISCURSO if discurso else "",
                )
            )
        )
    return novos


def buscar(ideia: Ideia) -> dict:
    """Cada frente por si: uma busca que falha nao derruba as outras. No
    refinamento, so as frentes novas (ou todas, se a pessoa pediu)."""
    from apps.knowledge.fontes_web import _buscar_paginas
    from apps.knowledge.models import CandidatoDeFonte

    pauta = ideia.pauta or criar_pauta(ideia)
    frentes = ideia.leitura.get("frentes") or []
    so_estas = (ideia.refino or {}).get("buscar")
    anteriores = (ideia.buscas or {}).get("frentes", {}) if so_estas is not None else {}
    if so_estas is not None:
        frentes = [f for f in frentes if f["nome"] in so_estas]
    feito: dict = {"frentes": dict(anteriores), "erros": []}

    def tentar(nome: str, funcao):
        try:
            return funcao()
        except Exception as exc:  # buscador fora, cota, base academica fora
            logger.warning("Ideia %s, busca %s: %s", ideia.pk, nome, exc)
            feito["erros"].append(f"{nome}: {exc}"[:300])
            return []

    # Link que o texto nao atribuiu a nenhuma frente: e o que a pessoa viu,
    # entao vai para o discurso.
    atribuidos = {u for f in ideia.leitura.get("frentes") or [] for u in f.get("links", [])}
    soltos = [u for u in ideia.links or [] if u not in atribuidos]

    for frente in frentes:
        papel = frente["papel"]
        discurso = papel == "discurso"
        consultas = frente.get("buscas") or [frente["nome"]]
        urls = frente.get("links", []) + (soltos if discurso else [])
        if discurso:
            soltos = []
        achados = _links_colados(urls, pauta, discurso=discurso, do_autor=ideia.links or [])
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
        _links_colados(soltos, pauta, discurso=True, do_autor=ideia.links or [])
    ideia.buscas = feito
    ideia.situacao = Ideia.Situacao.CURADORIA
    ideia.refino = {}
    ideia.save(update_fields=["buscas", "situacao", "refino", "atualizada_em"])
    # Os videos achados: a legenda e conferida antes da decisao, para a tela
    # dizer se ha transcricao.
    from apps.knowledge.tasks import verificar_legendas_da_pauta

    pk_da_pauta = str(pauta.pk)
    transaction.on_commit(lambda: verificar_legendas_da_pauta.delay(pk_da_pauta))
    return feito


def so_buscar(ideia: Ideia) -> None:
    """A leitura ja existe (veio da outra IA): pauta e buscas."""
    ideia.situacao = Ideia.Situacao.BUSCANDO
    ideia.erro = ""
    ideia.save(update_fields=["situacao", "erro", "atualizada_em"])
    criar_pauta(ideia)
    buscar(ideia)


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


def texto_para_as_redes(ideia: Ideia) -> str:
    """O texto do post de "noticia ou estudo comentado": o que se diz, a
    suspeita e o relato."""
    leitura = ideia.leitura or {}
    return "\n".join(
        x
        for x in [
            f"O que se diz: {leitura.get('afirmacao', '')}",
            f"O que eu suspeito: {leitura.get('tese', '')}",
            ideia.relato,
        ]
        if x.strip()
    )


def aprovar(ideia: Ideia) -> None:
    """A ideia pronta para as redes (com ou sem artigo no site)."""
    from django.utils import timezone

    ideia.situacao = Ideia.Situacao.APROVADA
    ideia.aprovada_em = ideia.aprovada_em or timezone.now()
    ideia.save(update_fields=["situacao", "aprovada_em", "atualizada_em"])


JANELA_DAS_APROVADAS_DIAS = 30


def para_as_redes() -> list[dict]:
    """Extensao `ideias_para_as_redes`: as ideias aprovadas nos ultimos 30 dias,
    com o material curado, para a escolha do dia das redes. A ideia cuja pauta
    ja tem artigo publicado fica de fora: o artigo e que vai."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.content.models import Article

    desde = timezone.now() - timedelta(days=JANELA_DAS_APROVADAS_DIAS)
    saida = []
    for ideia in Ideia.objects.filter(
        situacao=Ideia.Situacao.APROVADA, aprovada_em__gte=desde, pauta__isnull=False
    ).order_by("-aprovada_em"):
        if Article.objects.filter(topic=ideia.pauta, status=Article.Status.PUBLISHED).exists():
            continue
        referencias = material_para_as_redes(ideia)
        if not referencias:
            continue
        saida.append(
            {
                "id": str(ideia.pk),
                "titulo": ideia.titulo,
                "texto": texto_para_as_redes(ideia),
                "referencias": referencias,
            }
        )
    return saida


def artigo_salvo(sender, instance, **kwargs) -> None:
    """Sinal: o artigo de uma pauta que nasceu de ideia foi aprovado (ou
    publicado) -> a ideia fica aprovada tambem."""
    from apps.content.models import Article

    if not instance.topic_id or instance.status not in (
        Article.Status.APPROVED_SCHEDULED,
        Article.Status.PUBLISHED,
    ):
        return
    for ideia in Ideia.objects.filter(pauta_id=instance.topic_id).exclude(
        situacao__in=[Ideia.Situacao.APROVADA, Ideia.Situacao.DESCARTADA]
    ):
        aprovar(ideia)
