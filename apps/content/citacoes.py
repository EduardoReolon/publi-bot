"""Conferencia de citacoes: cada frase citada contra o trecho da fonte.

Roda logo depois de cada secao ser escrita (`flows.passo_redigir_secoes`), e
corrige sozinha, so o que esta errado:

1. o modelo julga, frase a frase, se a fonte citada sustenta o que ela diz
   (pergunta curta, que um modelo pequeno faz bem: e mais facil conferir do
   que escrever);
2. nao sustenta, mas outra fonte da secao sustenta (a mais proxima por
   embedding) -> troca o marcador; o texto nao muda;
3. nenhuma sustenta -> reescreve SO a frase, com a dica do que estava errado,
   ate TENTATIVAS vezes, conferindo de novo cada vez;
4. ainda assim nao -> tira o marcador e a frase fica marcada na revisao como
   "afirmacao sem fonte" (bloqueia a aprovacao ate alguem resolver).

Tudo fica registrado em `Article.conferencia_citacoes`, para a revisao mostrar
o que foi trocado e reescrito.
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np

from apps.content.rendering import PADRAO_MARCADOR

logger = logging.getLogger("publibot.content")

TENTATIVAS = 2
MAXIMO_DE_FRASES = 12
TRECHO_MAXIMO = 2500
_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú\[\"“(])")


def _sem_marcadores(texto: str) -> str:
    return " ".join(PADRAO_MARCADOR.sub("", texto).split())


def frases_citadas(texto: str) -> list[str]:
    """As frases do texto que tem marcador [[FONTE_N]], na ordem."""
    frases = []
    for paragrafo in texto.split("\n"):
        for frase in _FIM_DE_FRASE.split(paragrafo):
            if PADRAO_MARCADOR.search(frase):
                frases.append(frase.strip())
    return frases


def _julgar(frase: str, trecho, *, site, job) -> bool:
    """A fonte sustenta a frase? Sem resposta valida, nao derruba: sustenta."""
    from apps.content.inference import executar_prompt

    try:
        resultado = executar_prompt(
            key="citation_check",
            variaveis={"frase": _sem_marcadores(frase), "trecho": trecho.content[:TRECHO_MAXIMO]},
            site=site,
            job=job,
            com_convite=False,
            json_schema={
                "type": "object",
                "properties": {"veredito": {"type": "string"}},
                "required": ["veredito"],
            },
        )
        veredito = str(json.loads(resultado.texto).get("veredito", "")).lower()
    except (ValueError, LookupError):
        return True
    return veredito in {"sustenta", "parcial"}


def _reescrever(frase: str, trecho, numero: int, dica: str, *, site, job) -> str:
    from apps.content.inference import executar_prompt
    from apps.content.rendering import normalizar_marcadores

    resultado = executar_prompt(
        key="citation_fix",
        variaveis={
            "frase": _sem_marcadores(frase),
            "trecho": trecho.content[:TRECHO_MAXIMO],
            "marcador": f"[[FONTE_{numero}]]",
            "dica": dica,
            "idioma": getattr(site, "content_language", "") or "pt-BR",
        },
        site=site,
        job=job,
        com_convite=False,
    )
    nova = normalizar_marcadores(" ".join(resultado.texto.strip().strip('"').split()))
    # So o marcador pedido: a frase reescrita nao cita outra fonte.
    nova = PADRAO_MARCADOR.sub("", nova).strip()
    if not nova:
        return ""
    if nova[-1] in ".!?":
        return f"{nova[:-1]} [[FONTE_{numero}]]{nova[-1]}"
    return f"{nova} [[FONTE_{numero}]]."


def _proximidades(frases: list[str], trechos: list) -> np.ndarray:
    """(frase x trecho), cosseno. Sem vetores: zeros (so a fonte citada conta)."""
    try:
        from apps.knowledge.embeddings import get_embedding_client

        cliente = get_embedding_client()
        vf = np.asarray([cliente.embed_query(_sem_marcadores(f)) for f in frases], dtype=float)
        vt = np.asarray(
            cliente.embed_passage([t.content[:TRECHO_MAXIMO] for t in trechos]), dtype=float
        )
        vf /= np.linalg.norm(vf, axis=1, keepdims=True) + 1e-9
        vt /= np.linalg.norm(vt, axis=1, keepdims=True) + 1e-9
        return vf @ vt.T
    except Exception as exc:  # sem vetores, segue so com o julgamento
        logger.info("Conferencia de citacoes sem vetores: %s", exc)
        return np.zeros((len(frases), len(trechos)))


def conferir_secao(article, secao, trechos: list, *, site=None, job=None) -> list[dict]:
    """Confere e corrige as citacoes da secao. Devolve o registro do que fez.
    `trechos`: as fontes da secao, na ordem do marcador (FONTE_1 = trechos[0])."""
    from django.conf import settings

    from apps.content.inference import SemModeloConfigurado

    texto = secao.body_markdown or ""
    frases = frases_citadas(texto)[:MAXIMO_DE_FRASES]
    if not getattr(settings, "PUBLIBOT_CONFERIR_CITACOES", True):
        frases = []
    registro: list[dict] = []
    if frases and trechos:
        proximidade = _proximidades(frases, trechos)
        try:
            for i, frase in enumerate(frases):
                nova, feito = _conferir_frase(frase, proximidade[i], trechos, site=site, job=job)
                if nova != frase:
                    texto = texto.replace(frase, nova, 1)
                if feito:
                    registro.append({"secao": secao.order, **feito, "aceita": False})
        except SemModeloConfigurado:
            logger.info("Artigo %s: conferencia de citacoes sem modelo.", article.pk)
    secao.body_markdown = texto
    secao.citacoes_conferidas = True
    secao.save(update_fields=["body_markdown", "citacoes_conferidas", "updated_at"])
    if registro:
        article.conferencia_citacoes = [
            r for r in (article.conferencia_citacoes or []) if r.get("secao") != secao.order
        ] + registro
        article.save(update_fields=["conferencia_citacoes"])
    return registro


def _conferir_frase(frase: str, proximidade, trechos: list, *, site, job) -> tuple[str, dict]:
    citadas = [int(n) for n in dict.fromkeys(PADRAO_MARCADOR.findall(frase))]
    validas = [n for n in citadas if 1 <= n <= len(trechos)]
    falhas = [n for n in validas if not _julgar(frase, trechos[n - 1], site=site, job=job)]
    if not falhas and validas:
        return frase, {}

    sustentam = [n for n in validas if n not in falhas]
    if sustentam:
        # Outra fonte da mesma frase sustenta: so sai o marcador que nao sustenta.
        nova = frase
        for n in falhas:
            nova = re.sub(rf"\s*\[\[FONTE_{n}\]\]", "", nova)
        return nova, {
            "frase": _sem_marcadores(frase),
            "acao": "trocada",
            "de": falhas,
            "para": sustentam,
        }

    # A fonte mais proxima por sentido, fora as que ja falharam.
    ordem = [int(j) + 1 for j in np.argsort(-proximidade) if int(j) + 1 not in falhas]
    if ordem:
        melhor = ordem[0]
        if _julgar(frase, trechos[melhor - 1], site=site, job=job):
            nova = PADRAO_MARCADOR.sub("", frase).rstrip()
            nova = re.sub(r"\s+([.!?])$", r"\1", nova)
            nova = (
                f"{nova[:-1]} [[FONTE_{melhor}]]{nova[-1]}"
                if nova[-1:] in ".!?"
                else f"{nova} [[FONTE_{melhor}]]"
            )
            return nova, {
                "frase": _sem_marcadores(frase),
                "acao": "trocada",
                "de": falhas,
                "para": [melhor],
            }

    alvo = (ordem[0] if ordem else None) or (validas[0] if validas else 1)
    dica = "a fonte citada nao diz isso; afirme so o que o trecho sustenta"
    for _tentativa in range(TENTATIVAS):
        nova = _reescrever(frase, trechos[alvo - 1], alvo, dica, site=site, job=job)
        if nova and _julgar(nova, trechos[alvo - 1], site=site, job=job):
            return nova, {
                "frase": _sem_marcadores(frase),
                "acao": "reescrita",
                "para": [alvo],
                "nova": _sem_marcadores(nova),
            }
        dica = "a reescrita anterior ainda afirmava algo que o trecho nao diz; seja mais literal"

    sem = " ".join(PADRAO_MARCADOR.sub("", frase).split())
    sem = re.sub(r"\s+([.!?,;:])", r"\1", sem)
    return sem, {"frase": sem, "acao": "sem_fonte", "de": validas}


def pendencias(article) -> list[str]:
    """Afirmacao sem fonte ainda no texto e nao aceita: bloqueia a aprovacao."""
    corpo = " ".join((article.body_markdown or "").split())
    faltam = []
    for r in article.conferencia_citacoes or []:
        if r.get("acao") != "sem_fonte" or r.get("aceita"):
            continue
        if r["frase"][:80] in corpo:
            faltam.append(
                f'afirmacao sem fonte (nenhuma fonte sustenta): "{r["frase"][:160]}" — '
                "edite, apague ou aceite como opiniao do texto"
            )
    return faltam


# -- Revisao visual ------------------------------------------------------------
NOTAS = {
    "trocada": (
        "info",
        "Esta frase citava o estudo errado. O PubliBot trocou pela fonte que realmente diz isso.",
    ),
    "reescrita": (
        "atencao",
        "A frase original dizia algo que o estudo nao sustenta. Foi reescrita para dizer so "
        "o que ele diz. Confira se o sentido continua bom.",
    ),
    "sem_fonte": (
        "urgente",
        "Nenhuma das fontes sustenta esta frase, e ela trava a publicacao. Edite, apague "
        "ou aceite como opiniao do texto.",
    ),
}
_FIM = re.compile(r"[.!?](?=\s|<|$)")


def anotar(corpo_html: str, registro: list[dict]) -> tuple[str, list[dict]]:
    """Marca no HTML da previa as frases que a conferencia mexeu, numeradas,
    e devolve as notas para a coluna ao lado (como a revisao do Word).
    Frase que nao se acha no HTML continua nas notas, sem marca."""
    import html as html_mod

    notas = []
    for n, r in enumerate(registro or [], start=1):
        acao = r.get("acao", "")
        nivel, texto = NOTAS.get(acao, ("info", ""))
        if acao == "sem_fonte" and r.get("aceita"):
            nivel, texto = "info", "Aceita como opiniao do texto (sem fonte)."
        nota = {"n": n, "nivel": nivel, "texto": texto, "marcada": False}
        if acao == "reescrita":
            nota["antes"] = r.get("frase", "")
        alvo = (r.get("nova") or r.get("frase") or "").strip()
        inicio = html_mod.escape(alvo[:45], quote=False)
        pos = corpo_html.find(inicio) if len(inicio) >= 20 else -1
        if pos >= 0:
            fim = _FIM.search(corpo_html, pos + len(inicio))
            final = fim.end() if fim else -1
            trecho = corpo_html[pos:final] if final > 0 else ""
            equilibrado = trecho.count("<a ") == trecho.count("</a>") and not re.search(
                r"</?(p|h\d|li|ul|ol|details|summary|aside)\b", trecho
            )
            if trecho and equilibrado:
                marca = (
                    f'<mark class="nota-no-texto {nivel}" id="nota-{n}">{trecho}'
                    f'<sup class="nota-numero">{n}</sup></mark>'
                )
                corpo_html = corpo_html[:pos] + marca + corpo_html[final:]
                nota["marcada"] = True
        notas.append(nota)
    ordem = {"urgente": 0, "atencao": 1, "info": 2}
    notas.sort(key=lambda x: (ordem.get(x["nivel"], 3), x["n"]))
    return corpo_html, notas
