"""Pauta que discute uma afirmacao: "dizem X; o autor suspeita Y; o que a
evidencia mostra?" (vem da caixa de ideias, `apps/ideias`).

Dois tipos de fonte, e a diferenca e o que protege o texto:

* **evidencia** — os documentos do acervo, como em qualquer pauta: sustentam o
  que o artigo afirma, com citacao;
* **discurso** — o "o que se diz" (materia, post, video de influencer): o
  CandidatoDeFonte aprovado com papel DISCURSO. Nunca vira documento, entao a
  busca do acervo nunca o encontra; chega ao artigo SO por este bloco, rotulado
  como discurso, para ser citado como o que se diz e analisado — nunca como
  prova. Numero dele so aparece atribuido ("segundo a reportagem X").

O bloco tambem pede a postura de quem testa a propria ideia: a conclusao sai
da evidencia, e pode ser que X esteja certo, que Y esteja, ou que haja Z.
"""

from __future__ import annotations

TRECHO_POR_DISCURSO = 1500
MAXIMO_DE_DISCURSOS = 6


def discursos(pauta) -> list:
    """Os exemplos do discurso aprovados para a pauta."""
    from apps.knowledge.models import CandidatoDeFonte

    return list(
        CandidatoDeFonte.objects.filter(
            pauta=pauta,
            papel=CandidatoDeFonte.Papel.DISCURSO,
            situacao=CandidatoDeFonte.Situacao.APROVADO,
        ).order_by("decidido_em")[:MAXIMO_DE_DISCURSOS]
    )


def bloco(pauta) -> str:
    """O bloco do debate para os prompts do artigo, ou vazio na pauta comum."""
    if pauta is None:
        return ""
    debate = getattr(pauta, "debate", None) or {}
    exemplos = discursos(pauta)
    if not (debate.get("afirmacao") or debate.get("tese") or exemplos):
        return ""
    linhas = [
        "DEBATE DESTA PAUTA — o autor quer TESTAR uma ideia, nao defende-la a qualquer custo.",
    ]
    if debate.get("afirmacao"):
        linhas.append(f"- O que se diz (a afirmacao em discussao): {debate['afirmacao']}")
    if debate.get("tese"):
        linhas.append(f"- O que o autor suspeita: {debate['tese']}")
    for outra in debate.get("linhas") or []:
        linhas.append(f"- Outra explicacao possivel, a considerar: {outra}")
    papeis = {
        "discurso": "o que se diz (analisar, nao provar)",
        "a_favor": "sustentaria o autor",
        "contra": "contra o autor",
        "alternativa": "outra explicacao",
    }
    for frente in debate.get("frentes") or []:
        linhas.append(
            f"- Frente '{frente.get('nome', '')}' — {papeis.get(frente.get('papel'), '')}: "
            f"{frente.get('descricao', '')}"
        )
    linhas += [
        "- A conclusao sai das FONTES (evidencia), e so delas. Se elas sustentam o que se "
        "diz, diga isso com clareza; se sustentam o autor, mostre como; se apontam outra "
        "explicacao, apresente-a. Diga tambem o que a evidencia NAO permite concluir.",
        "- Estrutura que costuma funcionar: o que se diz -> por que isso convence -> o que "
        "a evidencia mostra (a favor e contra) -> conclusao honesta.",
        "- Critique a afirmacao, nunca as pessoas; quem disse X costuma ter visto parte do "
        "quadro, nao estar mentindo.",
    ]
    if exemplos:
        linhas.append(
            "EXEMPLOS DO DISCURSO (NAO sao evidencia: cite como 'o que se diz', pelo nome "
            "do veiculo ou do autor, sem link e sem numero de fonte; numero deles so "
            "atribuido, 'segundo a reportagem X'; nada do que dizem sustenta conclusao):"
        )
        for n, candidato in enumerate(exemplos, start=1):
            origem = candidato.canal_nome or candidato.dominio or candidato.url
            frente = (candidato.metricas or {}).get("frente")
            if frente:
                origem = f"{origem} (frente '{frente}')"
            texto = (candidato.texto_extraido or candidato.trecho or "").strip()
            # Sem colchete: [n] e o marcador de citacao das fontes de evidencia.
            linhas.append(
                f"Discurso {n}: {origem} — {candidato.titulo}\n{texto[:TRECHO_POR_DISCURSO]}"
            )
    return "\n".join(linhas)
