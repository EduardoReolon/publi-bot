"""O dialogo com a outra IA: o pedido de cada rodada e a leitura da resposta.

O pedido leva a descricao da empresa, a area (cidades, distancia, populacao),
como os dados sao medidos e quanto custam, e o que as rodadas anteriores
acharam. A resposta vem em blocos com rotulo (core/resposta_ia.py).
"""

from __future__ import annotations

from apps.estudos import coleta, funil, municipios
from apps.estudos.models import Estudo, Rodada
from core.resposta_ia import itens, ler_blocos

ROTULOS = ["ANALISE", "MODO", "PALAVRAS", "SEMENTES", "SITE", "CIDADES", "CAMPANHA", "NEGATIVAS"]
NO_PEDIDO = 150  # palavras das rodadas anteriores que vao no pedido

PEDIDO = """\
Voce e um especialista em Google Ads (rede de pesquisa) e em pesquisa de
mercado local. Vamos estudar, em rodadas, as buscas no Google para a empresa
abaixo: quais palavras as pessoas da regiao usam, quantas buscas ha, quanto
custa o clique e quantos clientes isso pode virar. Eu rodo as buscas no
Planejador de Palavras-chave do Google (pela DataForSEO) e te devolvo os
numeros; voce analisa e decide a proxima rodada.

A EMPRESA (o que me contaram):
{descricao}

A AREA: {centro}, raio de {raio} km ({n} cidades, {populacao} habitantes):
{cidades}

COMO OS NUMEROS SAO MEDIDOS (e o custo, que eu pago):
- ESTIMATIVA (barata, cerca de US$ 0,09 por estado da area, ate 1000 palavras):
  o volume de cada palavra no estado inteiro, dividido pela fatia de cada
  cidade na populacao do estado. Bom para explorar e comparar palavras; supoe
  que a cidade busca na proporcao da populacao.
- REAL (US$ 0,09 por cidade): o volume que o Google mede em cada cidade
  escolhida. Cidade pequena volta muitas vezes "sem dado" (o Google so informa
  acima de um minimo). Use para confirmar as palavras candidatas nas cidades
  que mais pesam, e compare com a estimativa (o fator real/estimado mostra se a
  regiao busca mais ou menos que a media do estado).
- SEMENTES (US$ 0,09): o Google sugere palavras a partir de ate 20 sementes (ou
  do SITE de uma empresa, inclusive concorrente), ja com volume.
- Cada palavra vem com o volume medio mensal, o custo por clique (US$), a
  concorrencia (0 a 100), a tendencia (12 meses contra os 12 anteriores) e o
  mes de pico.

O FUNIL que uso para estimar clientes: {ctr}% das buscas viram clique, {conversao}%
dos cliques viram contato e {fechamento}% dos contatos viram cliente{valor}. Diga se
esses percentuais nao fazem sentido para este negocio.

{historico}
O QUE FAZER NESTA RODADA:
- Explore TODAS as abordagens possiveis para esta empresa: o produto pelo nome
  e pelos sinonimos, o problema que ele resolve, o objetivo de quem compra, as
  comparacoes (com alternativas e concorrentes), as duvidas de quem ainda esta
  pesquisando, as buscas com a cidade ou "perto de mim", publicos diferentes.
  Separe o que e intencao de compra do que e curiosidade.
- Comece pela ESTIMATIVA; peca REAL so quando valer confirmar (palavras
  candidatas a campanha) e escolha poucas cidades, as que mais pesam.
- Analise os numeros que ja vieram: o que tem volume, o que e caro, o que
  cresce, quando e o pico, o que nao vale a pena.
- Quando a pesquisa estiver madura, diga a CAMPANHA (as palavras que eu devo
  anunciar) e as NEGATIVAS.

Responda SO neste formato (blocos vazios podem ficar sem itens):
ANALISE: o que os numeros mostram e por que a proxima rodada e esta
MODO: estimativa ou real
PALAVRAS:
- uma palavra-chave por linha (ate 300, em minusculas, sem pontuacao)
SEMENTES:
- ate 20, para o Google sugerir mais (opcional)
SITE: o endereco de um site para o Google sugerir palavras (opcional)
CIDADES:
- so no modo real: o nome das cidades da area para medir uma a uma
CAMPANHA:
- as palavras que voce recomenda anunciar (quando ja der para dizer)
NEGATIVAS:
- palavras negativas
FIM
"""


def _numero(valor) -> str:
    return f"{int(valor):,}".replace(",", ".") if valor is not None else "?"


def _historico(estudo: Estudo) -> str:
    rodadas = list(estudo.rodadas.filter(situacao=Rodada.Situacao.PRONTA).order_by("numero"))
    if not rodadas:
        return "AINDA NAO HA RODADAS: esta e a primeira.\n"
    linhas = [f"O QUE AS {len(rodadas)} RODADAS ANTERIORES ACHARAM:"]
    for rodada in rodadas[-5:]:
        if rodada.analise:
            linhas.append(f"- rodada {rodada.numero} ({rodada.modo}): {rodada.analise[:600]}")
    dados = coleta.consolidado(estudo)
    nomes = {str(m.codigo): m.nome for m in coleta.cidades_da_area(estudo)}
    ordem = sorted(dados.items(), key=lambda x: -(x[1].get("volume_area") or 0))
    linhas.append(
        "\nPalavra | buscas/mes na area (estimado) | real por cidade | US$/clique | "
        "concorrencia | tendencia | pico"
    )
    for palavra, item in ordem[:NO_PEDIDO]:
        reais = ", ".join(
            f"{nomes.get(c, c)}: {'sem dado' if v is None else v}"
            for c, v in (item.get("reais") or {}).items()
        )
        tendencia = funil.tendencia(item.get("meses_area"))
        linhas.append(
            f"{palavra} | {round(item.get('volume_area') or 0)} | {reais or '-'} | "
            f"{item.get('cpc') if item.get('cpc') is not None else '-'} | "
            f"{item.get('competicao') if item.get('competicao') is not None else '-'} | "
            f"{f'{tendencia:+.0f}%' if tendencia is not None else '-'} | "
            f"{funil.pico(item.get('meses_area')) or '-'}"
        )
    if len(ordem) > NO_PEDIDO:
        linhas.append(f"(e mais {len(ordem) - NO_PEDIDO} palavras com menos buscas)")
    sem_volume = [p for p, i in ordem if not i.get("volume_area")]
    if sem_volume:
        linhas.append("Sem buscas medidas: " + ", ".join(sem_volume[:60]))
    if estudo.campanha:
        conta = funil.funil(estudo, dados)["total"]
        linhas.append(
            f"\nCAMPANHA ATUAL ({len(estudo.campanha)} palavras): {', '.join(estudo.campanha)}"
            f"\nPelo funil: {conta['cliques']} cliques, R$ {conta['custo']} e "
            f"{conta['clientes']} clientes por mes."
        )
    if estudo.negativas:
        linhas.append("NEGATIVAS ATUAIS: " + ", ".join(estudo.negativas))
    return "\n".join(linhas) + "\n\n"


def montar(estudo: Estudo) -> str:
    cidades = coleta.area(estudo)
    dentro = [c for c in cidades if c["dentro"]]
    centro = municipios.todos().get(estudo.cidade)
    valor = (
        f", e um cliente deixa R$ {estudo.valor_do_cliente} para a empresa"
        if estudo.valor_do_cliente
        else ""
    )
    return PEDIDO.format(
        descricao=estudo.descricao.strip() or "(sem descricao)",
        centro=centro.rotulo if centro else "(sem cidade)",
        raio=estudo.raio_km,
        n=len(dentro),
        populacao=_numero(sum(c["populacao"] or 0 for c in dentro)),
        cidades="\n".join(
            f"- {c['municipio'].nome}: {c['distancia']} km, {_numero(c['populacao'])} hab."
            for c in dentro
        )
        or "- (nenhuma)",
        ctr=estudo.ctr,
        conversao=estudo.conversao,
        fechamento=estudo.fechamento,
        valor=valor,
        historico=_historico(estudo),
    )


def site_limpo(texto: str) -> str:
    """So o dominio: a IA costuma mandar o site como link em markdown
    ('[https://x.com](https://x.com)'), com caminho, ou 'nenhum'."""
    import re
    from urllib.parse import urlparse

    achado = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)", texto or "", re.I)
    if not achado:
        return ""
    dominio = urlparse("//" + achado.group(0).split("://")[-1]).hostname or ""
    return dominio.removeprefix("www.")[:200]


def ler(estudo: Estudo, resposta: str) -> dict:
    """O que a resposta pede, ja limpo e com as cidades em codigo do IBGE."""
    from apps.radar.locais import normalizar
    from apps.radar.provedores import palavra_para_volume

    blocos = ler_blocos(resposta, ROTULOS)

    def lista(rotulo: str, limite: int) -> list[str]:
        return list(
            dict.fromkeys(filter(None, map(palavra_para_volume, itens(blocos.get(rotulo, "")))))
        )[:limite]

    por_nome = {normalizar(m.nome): m.codigo for m in coleta.cidades_da_area(estudo)}
    cidades = []
    for nome in itens(blocos.get("CIDADES", "")):
        codigo = por_nome.get(normalizar(nome.split("(")[0].split(",")[0]))
        if codigo and codigo not in cidades:
            cidades.append(codigo)
    modo = normalizar(blocos.get("MODO", ""))
    site = site_limpo((itens(blocos.get("SITE", "")) or [""])[0])
    return {
        "analise": blocos.get("ANALISE", "").strip(),
        "modo": Rodada.Modo.REAL if modo.startswith("real") else Rodada.Modo.ESTIMATIVA,
        "palavras": lista("PALAVRAS", 300),
        "sementes": lista("SEMENTES", 20),
        "site": site,
        "cidades": cidades,
        "campanha": lista("CAMPANHA", 300),
        "negativas": lista("NEGATIVAS", 300),
    }
