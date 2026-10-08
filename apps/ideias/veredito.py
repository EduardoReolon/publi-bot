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

import json
import re

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
5. REFINAMENTO: o que a investigacao deve fazer a seguir para fechar o que
   ficou em aberto (o "O QUE FALTA"), num bloco ```json no FIM da resposta. O
   autor cola a resposta inteira de volta no sistema, que aplica o bloco:
   ```json
   {
     "afirmacao": "so se precisar corrigir o que se diz; senao, omita",
     "tese": "so se precisar corrigir a suspeita; senao, omita",
     "frentes": [
       {
         "nome": "o nome EXATO de uma frente acima (para melhorar) ou um nome novo",
         "papel": "discurso | a_favor | contra | alternativa",
         "descricao": "o que esta frente procura, em uma frase",
         "buscas": ["ate 3 buscas curtas, como se digita num buscador"],
         "estudos": true,
         "videos": false,
         "links": ["links REAIS que voce conhece ou achou (opcional)"],
         "buscar_de_novo": false
       }
     ],
     "capturar_texto": ["F3", "F7"],
     "pedidos_de_pdf": [
       {"fonte": "F4", "o_que": "o dado que falta, em poucas palavras"}
     ],
     "sugestoes_de_curadoria": [
       {"fonte": "F5", "acao": "aprovar | recusar | fora_da_pauta | discurso", "motivo": "..."},
       "... uma para CADA fonte ainda nao curada"
     ]
   }
   ```
   Regras do bloco: liste so as frentes que mudam ou que entram (as outras
   ficam como estao; nenhuma frente e apagada nem renomeada). "buscar_de_novo"
   numa frente que ja existe faz o sistema buscar de novo nela; frente nova e
   buscada sempre. "capturar_texto": as fontes que vieram "so o resumo da
   busca" e merecem o texto completo. "pedidos_de_pdf": o que procurar dentro do
   texto completo de um estudo (o sistema extrai os paragrafos que respondem,
   sem modelo, quando o PDF estiver no acervo). Nunca invente link.

   "sugestoes_de_curadoria" e a FAXINA COMPLETA: uma sugestao para CADA fonte
   marcada "ainda nao curada", sem pular nenhuma. Opine em todas:
   - recusar: LIXO, que nao serve para nenhuma pauta (nunca mais e sugerida):
     duplicata (diga de qual [Fn]), pagina sem conteudo (so menu, propaganda,
     chamada para outro link), fonte velha que outra mais nova substitui;
   - fora_da_pauta: material bom, mas que nao serve para ESTA pauta ou frente
     (fora do escopo, outro recorte): sai daqui e fica livre para outras pautas;
   - discurso: o "o que se diz" (materia, post, video de opiniao), mesmo que a
     frente dela seja de evidencia;
   - aprovar: evidencia que serve (dado, estudo, relatorio, documento oficial),
     mesmo que so o resumo tenha vindo (diga no motivo se o texto completo e
     indispensavel).
   O motivo e curto (uma linha) e e o que o autor le para decidir: ele aplica
   tudo de uma vez ou confere uma por uma. Fontes ja aprovadas: so sugira
   "recusar" se uma delas for claramente lixo ou duplicata.
   Sem frente a mudar, devolva "frentes": [] (o resto do bloco vale igual).
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
    """O pedido inteiro, pronto para copiar. Guarda na pauta qual fonte e cada
    [Fn], para a resposta colada de volta apontar a fonte certa mesmo que a
    lista mude depois."""
    from apps.content.models import Topic

    debate = pauta.debate or {}
    fontes = fontes_do_dossie(pauta)
    codigos = [str(f["candidato"].pk) for f in fontes]
    if debate.get("codigos_do_dossie") != codigos:
        debate = {**debate, "codigos_do_dossie": codigos}
        Topic.objects.filter(pk=pauta.pk).update(debate=debate)
        pauta.debate = debate
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
            + _texto_dos_pedidos(c)
        )
    return "\n\n".join(partes) + "\n"


# -- Pedidos de PDF: o que procurar dentro do texto completo de um estudo -----------
TRECHOS_POR_PEDIDO_NO_DOSSIE = 3


def documento_completo(candidato):
    """O documento com o texto completo do estudo (o PDF), ou None."""
    for documento in (candidato.documento_completo, candidato.documento):
        if (
            documento is not None
            and documento.markdown_full
            and (documento.extraction_method != "resumo")
        ):
            return documento
    return None


def trechos_dos_pedidos(candidato) -> dict:
    """{pedido: [paragrafos]} dos pedidos de PDF desta fonte, achados por vetor
    no texto completo (o mesmo mecanismo da pesquisa do B, sem modelo). Feito
    uma vez por pedido e guardado na fonte; sem PDF ainda, vazio."""
    pedidos = (candidato.metricas or {}).get("pedidos_de_pdf") or []
    guardados = (candidato.metricas or {}).get("trechos_pedidos") or {}
    faltam = [p for p in pedidos if p not in guardados]
    documento = documento_completo(candidato)
    if faltam and documento is not None:
        from apps.knowledge.pesquisa import trechos_pedidos

        try:
            achados = trechos_pedidos(documento, faltam, titulo=candidato.titulo)
        except Exception:  # sem embedding agora: tenta da proxima vez
            achados = {}
        if achados:
            guardados = {**guardados, **achados}
            candidato.metricas = {**(candidato.metricas or {}), "trechos_pedidos": guardados}
            candidato.save(update_fields=["metricas"])
    return {p: guardados[p] for p in pedidos if p in guardados}


def _texto_dos_pedidos(candidato) -> str:
    pedidos = (candidato.metricas or {}).get("pedidos_de_pdf") or []
    if not pedidos:
        return ""
    trechos = trechos_dos_pedidos(candidato)
    linhas = []
    for pedido in pedidos:
        if pedido in trechos:
            corpo = "\n".join(
                _cortar(p, 1200) for p in trechos[pedido][:TRECHOS_POR_PEDIDO_NO_DOSSIE]
            )
            linhas.append(f"Do texto completo, sobre '{pedido}':\n{corpo}")
        else:
            linhas.append(f"Pedido '{pedido}': o texto completo ainda nao esta no acervo.")
    return "\n" + "\n".join(linhas)


def capturar_textos(pauta) -> int:
    """Baixa o texto das paginas das frentes que so tem o resumo da busca e a
    legenda dos videos ainda nao conferidos, para o dossie levar o conteudo (e
    nao o link). Devolve quantas fontes tentou."""
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
    # Videos: a transcricao, pela legenda (a fala e o que o video diz).
    from apps.knowledge.videos import verificar_legendas

    return capturadas + verificar_legendas(pauta, limite=20)


# -- A resposta da outra IA, colada de volta ---------------------------------------
class VereditoInvalido(ValueError):
    pass


_BLOCO_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
ACOES_DE_CURADORIA = {"aprovar", "recusar", "fora_da_pauta", "discurso"}


def ler_resposta(texto: str) -> tuple[str, dict]:
    """(o texto do veredito, o bloco de refinamento). O bloco e o ULTIMO ```json
    da resposta; sem ele, do primeiro "{" ao ultimo "}"."""
    texto = (texto or "").strip()
    if not texto:
        raise VereditoInvalido("a resposta esta vazia.")
    candidatos = _BLOCO_JSON.findall(texto)
    if not candidatos and "{" in texto:
        candidatos = [texto[texto.find("{") : texto.rfind("}") + 1]]
    for bruto in reversed(candidatos):
        try:
            dados = json.loads(bruto)
        except json.JSONDecodeError:
            continue
        if isinstance(dados, dict):
            veredito = _BLOCO_JSON.sub("", texto).strip() if _BLOCO_JSON.search(texto) else texto
            return veredito, dados
    raise VereditoInvalido("nao achei o bloco JSON do refinamento no fim da resposta.")


def _candidatos_por_codigo(pauta, codigos) -> dict:
    from apps.knowledge.models import CandidatoDeFonte

    ids = (pauta.debate or {}).get("codigos_do_dossie") or []
    por_codigo = {}
    for codigo in codigos or []:
        achado = re.fullmatch(r"\[?F(\d+)\]?", str(codigo).strip(), re.IGNORECASE)
        if achado and 0 < int(achado.group(1)) <= len(ids):
            por_codigo[f"F{int(achado.group(1))}"] = ids[int(achado.group(1)) - 1]
    existentes = {
        str(c.pk): c for c in CandidatoDeFonte.objects.filter(pk__in=list(por_codigo.values()))
    }
    return {k: existentes[v] for k, v in por_codigo.items() if v in existentes}


def aplicar_veredito(pauta, texto: str) -> dict:
    """Guarda o veredito na pauta e aplica o refinamento: as frentes (as
    existentes ficam; melhora e acrescenta, pelas mesmas regras da leitura), as
    fontes a capturar e as sugestoes de curadoria (so sugestao, no cartao).
    Devolve o resumo; a busca das frentes fica para a tarefa de fundo."""
    from django.utils import timezone

    from apps.content.models import Topic
    from apps.ideias.investigacao import _guardar_leitura, criar_pauta
    from apps.ideias.models import Ideia

    veredito, dados = ler_resposta(texto)
    ideia = next((i for i in pauta.ideias.all() if (i.leitura or {}).get("frentes")), None)
    if ideia is None:
        raise VereditoInvalido("esta pauta nao tem investigacao (frentes) para refinar.")

    leitura = ideia.leitura
    frentes = [f for f in dados.get("frentes") or [] if isinstance(f, dict)]
    rebuscar = [str(f.get("nome", "")).strip() for f in frentes if f.get("buscar_de_novo")]
    links = [
        str(u).strip()
        for f in frentes
        for u in f.get("links") or []
        if str(u).strip().startswith(("http://", "https://"))
    ]
    ideia.refino = {"pedido": "", "buscar_existentes": False}
    antes = {f["nome"] for f in leitura.get("frentes") or []}
    _guardar_leitura(
        ideia,
        {
            "titulo": leitura.get("titulo", ""),
            "afirmacao": str(dados.get("afirmacao") or leitura.get("afirmacao", "")),
            "tese": str(dados.get("tese") or leitura.get("tese", "")),
            "frentes": frentes,
            "onde": ideia.onde,
        },
        links_permitidos=[*(ideia.links or []), *links],
    )
    a_buscar = list((ideia.refino or {}).get("buscar") or [])
    a_buscar += [n for n in rebuscar if n in antes and n not in a_buscar]
    ideia.refino = {**ideia.refino, "buscar": a_buscar}
    ideia.save(update_fields=["refino", "atualizada_em"])
    criar_pauta(ideia)

    pauta.refresh_from_db()
    capturar = _candidatos_por_codigo(pauta, dados.get("capturar_texto"))
    sugeridas = 0
    sugestoes = [s for s in dados.get("sugestoes_de_curadoria") or [] if isinstance(s, dict)]
    por_codigo = _candidatos_por_codigo(pauta, [s.get("fonte") for s in sugestoes])
    for sugestao in sugestoes:
        acao = str(sugestao.get("acao", "")).strip().lower()
        codigo = re.sub(r"[^\dF]", "", str(sugestao.get("fonte", "")).upper())
        candidato = por_codigo.get(codigo)
        if candidato is None or acao not in ACOES_DE_CURADORIA:
            continue
        candidato.metricas = {
            **(candidato.metricas or {}),
            "sugestao_da_ia": {"acao": acao, "motivo": str(sugestao.get("motivo", ""))[:400]},
        }
        candidato.save(update_fields=["metricas"])
        sugeridas += 1

    pedidos = [p for p in dados.get("pedidos_de_pdf") or [] if isinstance(p, dict)]
    por_codigo_pdf = _candidatos_por_codigo(pauta, [p.get("fonte") for p in pedidos])
    pedidos_feitos = 0
    for pedido in pedidos:
        codigo = re.sub(r"[^\dF]", "", str(pedido.get("fonte", "")).upper())
        candidato = por_codigo_pdf.get(codigo)
        o_que = str(pedido.get("o_que") or "").strip()[:200]
        if candidato is None or not o_que:
            continue
        atuais = (candidato.metricas or {}).get("pedidos_de_pdf") or []
        if o_que not in atuais:
            candidato.metricas = {
                **(candidato.metricas or {}),
                "pedidos_de_pdf": [*atuais, o_que],
            }
            candidato.save(update_fields=["metricas"])
        pedidos_feitos += 1

    resumo = {
        "pedidos_de_pdf": pedidos_feitos,
        "frentes_novas": [f for f in a_buscar if f not in antes],
        "rebuscar": [f for f in a_buscar if f in antes],
        "capturar": [str(c.pk) for c in capturar.values()],
        "sugestoes": sugeridas,
    }
    debate = {
        **(pauta.debate or {}),
        "veredito": {"texto": veredito[:20000], "em": timezone.now().isoformat(timespec="minutes")},
    }
    Topic.objects.filter(pk=pauta.pk).update(debate=debate)
    ideia.situacao = Ideia.Situacao.BUSCANDO
    ideia.save(update_fields=["situacao", "atualizada_em"])
    return {**resumo, "ideia": str(ideia.pk)}


def capturar_fontes(ids: list[str]) -> int:
    """O texto completo das fontes que o veredito pediu (pagina: o texto;
    video: a legenda)."""
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.videos import verificar_legenda
    from apps.knowledge.web import PaginaIndisponivel, texto_da_pagina

    feitas = 0
    for c in CandidatoDeFonte.objects.filter(pk__in=ids):
        try:
            if c.tipo == CandidatoDeFonte.Tipo.VIDEO:
                verificar_legenda(c)
            elif c.tipo == CandidatoDeFonte.Tipo.PAGINA:
                c.texto_extraido = texto_da_pagina(c.url)
                c.save(update_fields=["texto_extraido"])
            else:
                continue
        except PaginaIndisponivel:
            continue
        feitas += 1
    return feitas


def pedidos_de_pdf_da_pauta(pauta) -> list[tuple]:
    """[(documento, pedido)] dos pedidos de PDF das fontes aprovadas da pauta
    com o texto completo no acervo: a geracao do A busca neles o paragrafo
    que responde (`referencias.trechos_da_pauta`)."""
    from apps.knowledge.models import CandidatoDeFonte

    saida = []
    for candidato in CandidatoDeFonte.objects.filter(
        pauta=pauta,
        situacao=CandidatoDeFonte.Situacao.APROVADO,
        metricas__has_key="pedidos_de_pdf",
    ).select_related("documento", "documento_completo"):
        documento = documento_completo(candidato)
        if documento is None:
            continue
        saida += [(documento, p) for p in candidato.metricas.get("pedidos_de_pdf") or []]
    return saida


def aplicar_sugestoes(pauta, *, por=None) -> dict:
    """As sugestoes de curadoria do veredito, aplicadas. Recusar, fora da pauta
    e discurso, sempre; aprovar, so quando a IA viu o texto (nao so o resumo da busca) —
    as outras continuam como sugestao. Estudo sem PDF aberto e video sem
    legenda seguem o caminho de sempre (esperando o arquivo, na frente)."""
    from apps.knowledge.fontes_web import (
        aprovar,
        aprovar_como_discurso,
        dispensar_da_pauta,
        recusar,
    )
    from apps.knowledge.models import CandidatoDeFonte
    from apps.knowledge.perfis import categoria_da_natureza

    feitos = {"aprovadas": 0, "recusadas": 0, "fora": 0, "discurso": 0, "ficaram": 0}
    for candidato in CandidatoDeFonte.objects.filter(
        pauta=pauta,
        situacao=CandidatoDeFonte.Situacao.PENDENTE,
        metricas__has_key="sugestao_da_ia",
    ).select_related("documento"):
        acao = (candidato.metricas.get("sugestao_da_ia") or {}).get("acao")
        if acao == "recusar":
            recusar(candidato, por=por, motivo="sugestao do veredito da outra IA")
            feitos["recusadas"] += 1
        elif acao == "fora_da_pauta":
            dispensar_da_pauta(candidato, por=por, motivo="sugestao do veredito da outra IA")
            feitos["fora"] += 1
        elif acao == "discurso":
            aprovar_como_discurso(candidato, por=por)
            feitos["discurso"] += 1
        elif acao == "aprovar" and not _conteudo(candidato)[1]:
            aprovar(
                candidato,
                categoria=categoria_da_natureza(natureza_sugerida(candidato)),
                por=por,
            )
            feitos["aprovadas"] += 1
        else:
            feitos["ficaram"] += 1
    return feitos
