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
do radar de pautas de um site: o negocio, as palavras-semente que eu uso, o que
cada semente rendeu, os sinais de demanda (perguntas e buscas do Google, com
volume mensal quando existe), os temas agrupados com a nota do sistema, as
pautas criadas e os concorrentes. "—" em volume quer dizer que o Google Ads nao
tem numero (busca rara ou pergunta longa), nao que seja zero.

Quero, em portugues, em listas curtas e diretas:

1. Quais sementes trazem o PUBLICO CERTO (quem compraria a oferta) e quais
   trazem publico errado ou so curiosos. Diga quais manter, quais trocar.
2. De 10 a 15 sementes novas, na lingua de quem sente o problema (e nao no
   jargao de quem vende), marcando as 5 que eu deveria testar primeiro.
3. De 5 a 10 dores do publico, cada uma numa frase como o cliente diria.
4. Para cada pauta criada, um angulo de artigo que atenda a busca E leve
   naturalmente a oferta — e um titulo melhor, se o atual for fraco.
5. Temas da lista que eu deveria descartar, e por que.
6. Para cada dominio em "Concorrentes", classifique: concorrente de NEGOCIO
   (vende algo que substitui a minha oferta), concorrente de CONTEUDO (disputa
   as mesmas buscas, mas vende outra coisa), possivel PARCEIRO (publica para o
   mesmo publico sem competir: artigo convidado, indicacao, conteudo em
   conjunto) ou IRRELEVANTE. Uma linha de porque para cada, e diga quais eu
   deveria confirmar como concorrente no radar e quais recusar.
7. Um padrao que voce veja nos dados e que eu nao tenha perguntado.

Nao invente volumes: onde nao ha numero, diga "testar". Se algo depender de
informacao que nao esta aqui, pergunte no fim.
"""


def texto_para_ia() -> str:
    from apps.editorial.models import perfil_do_negocio
    from apps.radar.concorrentes import sugeridos_para_a_tela
    from apps.radar.models import (
        ConfiguracaoDoRadar,
        GrupoDeDemanda,
        Oportunidade,
        RodadaDoRadar,
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

    partes.append("\n## Temas agrupados (nota 0-100 | volume | situacao | parcelas)")
    grupos = GrupoDeDemanda.objects.exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO)
    for grupo in grupos.order_by("-nota")[:MAXIMO_DE_TEMAS]:
        parcelas = ", ".join(f"{nome} {valor:.2f}" for nome, valor in grupo.parcelas_rotuladas)
        partes.append(
            f"- {grupo.rotulo} | {grupo.nota:.0f} | {grupo.volume_total or '—'} | "
            f"{grupo.get_situacao_display()} | {parcelas}"
        )

    pautas = [
        pauta
        for rodada in RodadaDoRadar.objects.order_by("-iniciada_em")[:5]
        for pauta in (rodada.resumo or {}).get("pautas") or []
    ]
    partes.append("\n## Pautas criadas pelo radar nas ultimas rodadas")
    partes += [f"- {p}" for p in dict.fromkeys(pautas)] or ["(nenhuma)"]

    oportunidades = Oportunidade.objects.exclude(
        situacao=Oportunidade.Situacao.ARQUIVADA
    ).select_related("grupo")
    partes.append("\n## Oportunidades (temas longe do negocio atual, perto do publico)")
    partes += [
        f"- {o.grupo.rotulo} | nota {o.nota:.0f} | {o.get_situacao_display()}"
        for o in oportunidades.order_by("-nota")[:15]
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
