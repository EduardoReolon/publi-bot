"""O que o radar achou, em dois formatos: uma tabela e um texto para outra IA.

**Rendimento das sementes** (algoritmo, sem modelo): por semente, quantos
sinais ela trouxe, quantos tem volume de busca, o volume somado e quantos temas
dela viraram pauta. E o que mostra de cara que uma semente em jargao traz
perguntas sem busca, e outra, na lingua de quem compra, traz milhares.

**Texto para outra IA**: o modelo local (7B a 30B) nao tira desses dados a
leitura de um consultor. Em vez de insistir, o PubliBot monta o contexto
inteiro — o negocio, as sementes e o rendimento, os sinais, os temas, as
pautas e os concorrentes — com as perguntas certas, e a pessoa cola num modelo
grande de sua escolha. Nada sai do sistema sozinho: e texto copiado a mao.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

JANELA = timedelta(days=90)
MAXIMO_DE_SINAIS = 150
MAXIMO_DE_TEMAS = 40


def rendimento_das_sementes() -> list[dict]:
    """Por semente (das configuradas e das buscadas nos ultimos 90 dias)."""
    from apps.radar.models import ConfiguracaoDoRadar, SinalDeDemanda

    linhas: dict[str, dict] = {}
    for semente in ConfiguracaoDoRadar.carregar().lista_de_sementes:
        linhas[semente.lower()] = _linha(semente, configurada=True)

    sinais = SinalDeDemanda.objects.filter(
        criado_em__gte=timezone.now() - JANELA, extra__semente__isnull=False
    ).select_related("grupo")
    for sinal in sinais:
        semente = str(sinal.extra.get("semente") or "").strip()
        if not semente:
            continue
        linha = linhas.setdefault(semente.lower(), _linha(semente, configurada=False))
        linha["sinais"] += 1
        if sinal.volume:
            linha["com_volume"] += 1
            linha["volume"] += sinal.volume
            if sinal.volume > linha["maior_volume"]:
                linha["maior_volume"], linha["melhor_sinal"] = sinal.volume, sinal.texto
        if sinal.grupo_id and sinal.grupo.pauta_id:
            linha["pautas"].add(sinal.grupo.pauta_id)
    for linha in linhas.values():
        linha["pautas"] = len(linha["pautas"])
    return sorted(linhas.values(), key=lambda linha: (-linha["volume"], -linha["sinais"]))


def _linha(semente: str, *, configurada: bool) -> dict:
    return {
        "semente": semente,
        "configurada": configurada,
        "sinais": 0,
        "com_volume": 0,
        "volume": 0,
        "maior_volume": 0,
        "melhor_sinal": "",
        "pautas": set(),
    }


PEDIDO = """\
Voce e um consultor de SEO e de estrategia de conteudo. Abaixo estao os dados
do radar de pautas de um site: o negocio, as palavras-semente e as dores que eu
busco (na ordem em que o radar as usa), o que cada semente rendeu, os sinais de
demanda (buscas e perguntas do Google, com volume mensal quando existe), os
temas que o radar agrupou e os concorrentes. "—" em volume quer dizer que o
Google Ads nao tem numero (busca rara ou pergunta longa), nao que seja zero.

A sua resposta volta para o sistema, que a le sozinho. Por isso responda SO
nos blocos abaixo, nesta ordem, e termine com FIM:

SEMENTES:
- a lista COMPLETA de sementes que devo usar, uma por linha, na ordem de
  prioridade: a primeira e a que o radar busca primeiro. Mantenha as que
  trazem o publico que compraria a oferta, tire as que trazem estudante,
  curioso ou publico errado, e acrescente as que faltam, na lingua de quem
  sente o problema (nao no jargao de quem vende). Ate 25.
DORES:
- a lista COMPLETA de dores do publico, uma por linha, cada uma como o cliente
  diria. Ate 15.
BONS:
- t-xxxxxx: motivo em ate 12 palavras
RUINS:
- t-xxxxxx: motivo em ate 12 palavras
COMENTARIOS:
curto. Para cada dominio em "Concorrentes", uma linha: concorrente de NEGOCIO
(vende o que substitui a minha oferta: confirmar no radar), concorrente de
CONTEUDO (disputa as buscas, vende outra coisa), possivel PARCEIRO (mesmo
publico sem competir) ou IRRELEVANTE (recusar). Depois, um padrao que voce veja
nos dados, e perguntas que voce tenha para mim.
FIM

Regras:
- TODO codigo da lista "Temas para avaliar" (e das oportunidades) aparece uma
  vez, em BONS ou em RUINS. Bom = vale um artigo que leva a oferta;
  ruim = publico errado, sem intencao de compra ou fora do negocio.
- Use o codigo exatamente como esta (t- e seis caracteres). Nao reescreva
  titulos: so classifique.
- Nao invente volumes: onde nao ha numero, e "testar".
"""


def texto_para_ia() -> str:
    from apps.editorial.models import perfil_do_negocio
    from apps.radar.concorrentes import sugeridos_para_a_tela
    from apps.radar.models import (
        ConfiguracaoDoRadar,
        GrupoDeDemanda,
        Oportunidade,
        SinalDeDemanda,
    )

    config = ConfiguracaoDoRadar.carregar()
    perfil = perfil_do_negocio()
    partes = [PEDIDO, "## O negocio"]
    if perfil is not None:
        partes += [
            f"Tema do site: {perfil.tema or '(nao preenchido)'}",
            f"Publico: {perfil.publico or '(nao preenchido)'}",
            f"Oferta (porta de entrada): {perfil.oferta or '(nao preenchida)'}",
        ]
        frentes = [f.strip() for f in perfil.frentes.splitlines() if f.strip()]
        if frentes:
            partes.append("Outras frentes: " + "; ".join(frentes))
    partes.append("Dores do publico: " + ("; ".join(config.lista_de_dores) or "(nenhuma)"))
    regioes = [nome for _codigo, nome in config.locais() if nome]
    partes.append("Regioes: " + (", ".join(regioes) or "o pais inteiro"))

    partes.append("\n## Rendimento das sementes (ultimos 90 dias)")
    for linha in rendimento_das_sementes():
        situacao = "" if linha["configurada"] else " (nao esta mais na lista)"
        melhor = (
            f"; melhor: {linha['melhor_sinal']} ({linha['maior_volume']})"
            if linha["melhor_sinal"]
            else ""
        )
        partes.append(
            f"- {linha['semente']}{situacao}: {linha['sinais']} sinais, "
            f"{linha['com_volume']} com volume, volume somado {linha['volume']}, "
            f"{linha['pautas']} pauta(s){melhor}"
        )

    partes.append("\n## Sinais (fonte | texto | volume/mes | semente)")
    sinais = SinalDeDemanda.objects.filter(criado_em__gte=timezone.now() - JANELA).exclude(
        situacao=SinalDeDemanda.Situacao.DESCARTADO
    )
    for sinal in sinais.order_by("-volume", "-criado_em")[:MAXIMO_DE_SINAIS]:
        partes.append(
            f"- {sinal.get_fonte_display()} | {sinal.texto} | "
            f"{sinal.volume if sinal.volume is not None else '—'} | "
            f"{(sinal.extra or {}).get('semente', '')}"
        )

    from apps.radar.revisao_ia import (
        OPORTUNIDADES_POR_PEDIDO,
        codigo,
        temas_para_avaliar,
    )

    partes.append("\n## Sementes atuais, na ordem (a primeira e buscada primeiro)")
    partes += [f"- {s}" for s in config.lista_de_sementes] or ["(nenhuma)"]
    partes.append("\n## Dores atuais, na ordem")
    partes += [f"- {d}" for d in config.lista_de_dores] or ["(nenhuma)"]

    temas = list(temas_para_avaliar())
    ja_avaliados = GrupoDeDemanda.objects.exclude(avaliacao_ia="").count()
    faltam = (
        GrupoDeDemanda.objects.filter(avaliacao_ia="")
        .exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO)
        .count()
    )
    partes.append(
        f"\n## Temas para avaliar ({len(temas)} de {faltam} sem avaliacao; "
        f"{ja_avaliados} ja avaliados em pedidos anteriores)"
    )
    partes.append("codigo | tema | nota 0-100 | buscas/mes | situacao | parcelas da nota")
    for grupo in temas:
        parcelas = ", ".join(f"{nome} {valor:.2f}" for nome, valor in grupo.parcelas_rotuladas)
        situacao = (
            f"virou pauta: {grupo.pauta.title}" if grupo.pauta_id else grupo.get_situacao_display()
        )
        partes.append(
            f"- {codigo(grupo)} | {grupo.rotulo} | {grupo.nota:.0f} | "
            f"{grupo.volume_total or '—'} | {situacao} | {parcelas}"
        )
    if not temas:
        partes.append("(nenhum tema sem avaliacao)")

    ids_dos_temas = {g.pk for g in temas}
    oportunidades = [
        o
        for o in Oportunidade.objects.exclude(situacao=Oportunidade.Situacao.ARQUIVADA)
        .filter(grupo__avaliacao_ia="")
        .select_related("grupo")
        .order_by("-nota")[: OPORTUNIDADES_POR_PEDIDO * 2]
        if o.grupo_id not in ids_dos_temas
    ][:OPORTUNIDADES_POR_PEDIDO]
    partes.append(
        "\n## Oportunidades para avaliar (temas longe do negocio atual, perto do publico)"
    )
    partes += [
        f"- {codigo(o.grupo)} | {o.grupo.rotulo} | nota {o.nota:.0f} | {o.get_situacao_display()}"
        for o in oportunidades
    ] or ["(nenhuma)"]

    partes.append("\n## Concorrentes")
    partes.append("Confirmados por mim no radar:")
    confirmados = config.lista_de_concorrentes
    partes += [
        f"- {c['dominio']}" + (f" ({c['nome']})" if c.get("nome") else "") for c in confirmados
    ] or ["- (nenhum)"]
    partes.append(
        "Sugeridos pelo radar (aparecem na primeira pagina de buscas diferentes; "
        "dominio | buscas | melhor posicao | paginas que apareceram):"
    )
    sugeridos = sugeridos_para_a_tela()
    for sugerido in sugeridos:
        exemplos = "; ".join(
            f"{e.get('titulo') or e.get('url')} [busca: {e.get('consulta', '')}]"
            for e in sugerido.exemplos[:3]
        )
        partes.append(
            f"- {sugerido.dominio} | {sugerido.aparicoes} | {sugerido.melhor_posicao} | {exemplos}"
        )
    if not sugeridos:
        partes.append("- (nenhum ainda)")
    return "\n".join(partes)
