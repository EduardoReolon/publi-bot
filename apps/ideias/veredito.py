"""O dossie da investigacao, para uma IA grande dar o veredito.

Um modelo de fora nao abre vinte links (abre dois ou tres, quando abre). Entao
o CONTEUDO vai dentro do pedido: a afirmacao, a suspeita, as frentes e, de cada
fonte, o texto que o PubliBot ja tem (o do acervo, o capturado na curadoria ou,
na falta, o resumo da busca), cortado para caber. Cada fonte leva o MEIO
(video, veiculo escrito, orgao oficial, estudo...), para o modelo comparar
tambem como cada meio conta a mesma historia e para que publico.

So texto: nada aqui chama modelo nem muda a pauta.
"""

from __future__ import annotations

from apps.knowledge.fontes_web import natureza_sugerida

LIMITE_DO_DOSSIE = 60000  # caracteres de conteudo das fontes, somados
TRECHO_MINIMO = 1200
TRECHO_MAXIMO = 8000

MEIOS = {
    "video": "video (fala: TV, YouTube, podcast)",
    "cientifico": "artigo cientifico",
    "normativo": "orgao oficial / governo",
    "comunidade": "comunidade ou forum",
    "veiculo": "texto publicado (jornal, revista, blog, empresa)",
}

PAPEIS = {
    "discurso": "o que se diz",
    "a_favor": "a favor da suspeita",
    "contra": "contra a suspeita",
    "alternativa": "outra explicacao",
}

INSTRUCOES = """\
Voce vai dar um VEREDITO sobre uma afirmacao, com o material abaixo. O material
foi juntado por frentes: o que se diz, o que sustenta a suspeita do autor, a
melhor evidencia contra e outras explicacoes. Cada fonte traz o MEIO em que
saiu, o veiculo e o conteudo que conseguimos capturar.

Regras:
- Trabalhe com o CONTEUDO que esta aqui. Nao tente abrir todos os links; se um
  for indispensavel, abra no maximo dois e diga quais.
- Separe o que a fonte MOSTRA (dado, estudo, metodo) do que ela DIZ (opiniao,
  manchete, fala de entrevistado). Fonte marcada "o que se diz" e discurso: e
  objeto de analise, nao prova.
- Fonte "ainda nao curada" nao foi conferida pelo autor; "so o resumo da busca"
  quer dizer que o texto nao foi capturado: pese isso.
- Cite as fontes pelo codigo [F1], [F2]...

Responda com estas partes:
1. VEREDITO: a afirmacao e a suspeita estao confirmadas, em parte, refutadas ou
   sem base suficiente? Diga o que exatamente se sustenta e o que nao, com as
   fontes.
2. POR MEIO E PUBLICO: compare como cada meio (video/TV, texto publicado, orgao
   oficial, estudo) conta a historia. Para que publico parece falar? A
   diferenca e de FATO (dizem coisas incompativeis) ou de ENFASE e
   enquadramento (mesma base, recorte diferente para gerar mais impacto ou
   para um publico que ja conhece o assunto)? De exemplos com as fontes.
3. O QUE FALTA: que tipo de fonte decidiria o que ficou em aberto.
4. PARA O ARTIGO: em 3 a 5 frases, a conclusao honesta que um artigo poderia
   defender com este material.
"""


def _meio(candidato) -> str:
    natureza = natureza_sugerida(candidato)
    meio = MEIOS.get(natureza, natureza)
    if candidato.canal_nome:
        meio += f" — canal {candidato.canal_nome}"
    elif candidato.revista:
        meio += f" — {candidato.revista}"
    return meio


def _conteudo(candidato) -> tuple[str, bool]:
    """(texto, se e so o resumo da busca)."""
    documento = candidato.documento
    if documento is not None and documento.markdown_full:
        return documento.markdown_full, False
    texto = (candidato.texto_extraido or "").strip()
    resumo = "\n".join(x for x in [candidato.titulo, candidato.trecho] if x).strip()
    if texto and texto != resumo:
        return texto, False
    return resumo, True


def _cortar(texto: str, limite: int) -> str:
    texto = texto.strip()
    if len(texto) <= limite:
        return texto
    corte = texto.rfind(" ", 0, limite)
    return texto[: corte if corte > limite // 2 else limite] + " [...]"


def fontes_do_dossie(pauta) -> list[dict]:
    """As fontes das frentes (aprovadas e as que esperam decisao; recusadas
    ficam de fora), na ordem das frentes."""
    from apps.knowledge.models import CandidatoDeFonte

    frentes = [f["nome"] for f in (pauta.debate or {}).get("frentes") or []]
    ordem = {nome: n for n, nome in enumerate(frentes)}
    candidatos = (
        CandidatoDeFonte.objects.filter(pauta=pauta)
        .exclude(
            situacao__in=[CandidatoDeFonte.Situacao.RECUSADO, CandidatoDeFonte.Situacao.FALHOU]
        )
        .select_related("documento")
        .order_by("encontrado_em")
    )
    saida = []
    for candidato in candidatos:
        frente = (candidato.metricas or {}).get("frente", "")
        if frente not in ordem:
            continue
        texto, so_resumo = _conteudo(candidato)
        saida.append(
            {
                "candidato": candidato,
                "frente": frente,
                "texto": texto,
                "so_resumo": so_resumo,
                "curada": candidato.situacao != CandidatoDeFonte.Situacao.PENDENTE,
            }
        )
    saida.sort(key=lambda f: ordem[f["frente"]])
    return saida


def dossie(pauta, *, limite: int = LIMITE_DO_DOSSIE) -> str:
    """O pedido inteiro, pronto para copiar."""
    debate = pauta.debate or {}
    fontes = fontes_do_dossie(pauta)
    por_fonte = max(TRECHO_MINIMO, min(TRECHO_MAXIMO, limite // max(len(fontes), 1)))

    partes = [INSTRUCOES, f"PAUTA: {pauta.title}"]
    if debate.get("afirmacao"):
        partes.append(f"O QUE SE DIZ (a afirmacao): {debate['afirmacao']}")
    if debate.get("tese"):
        partes.append(f"A SUSPEITA DO AUTOR: {debate['tese']}")
    linhas = ["FRENTES:"]
    for frente in debate.get("frentes") or []:
        papel = PAPEIS.get(frente.get("papel", ""), frente.get("papel", ""))
        descricao = f": {frente['descricao']}" if frente.get("descricao") else ""
        linhas.append(f"- {frente['nome']} ({papel}){descricao}")
    partes.append("\n".join(linhas))

    if not fontes:
        partes.append("FONTES: nenhuma ainda (a busca das frentes nao terminou).")
    for n, fonte in enumerate(fontes, 1):
        c = fonte["candidato"]
        papel = "o que se diz" if c.papel == c.Papel.DISCURSO else "evidencia"
        situacao = "aprovada pelo autor" if fonte["curada"] else "ainda nao curada"
        data = c.publicado_em or c.ano or ""
        cabecalho = [
            f"[F{n}] {c.titulo or c.url}",
            f"Frente: {fonte['frente']} · papel: {papel} · {situacao}",
            f"Meio: {_meio(c)} · veiculo: {c.dominio or '-'}"
            + (f" · data: {data}" if data else ""),
            f"Link: {c.url}",
        ]
        nota = " (so o resumo da busca: o texto nao foi capturado)" if fonte["so_resumo"] else ""
        partes.append(
            "\n".join(cabecalho)
            + f"\nConteudo{nota}:\n{_cortar(fonte['texto'], por_fonte) or '(vazio)'}"
        )
    return "\n\n".join(partes) + "\n"


def capturar_textos(pauta) -> int:
    """Baixa o texto das paginas das frentes que so tem o resumo da busca, para
    o dossie levar o conteudo (e nao o link). Devolve quantas capturou."""
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.web import PaginaIndisponivel, texto_da_pagina

    capturadas = 0
    for fonte in fontes_do_dossie(pauta):
        c = fonte["candidato"]
        if not fonte["so_resumo"] or c.tipo != CandidatoDeFonte.Tipo.PAGINA:
            continue
        try:
            c.texto_extraido = texto_da_pagina(c.url)
        except PaginaIndisponivel:
            continue
        c.save(update_fields=["texto_extraido"])
        capturadas += 1
    return capturadas
