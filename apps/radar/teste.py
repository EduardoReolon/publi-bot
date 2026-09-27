"""O resultado de uma oportunidade testada com artigo.

"Testar com um artigo" e o experimento barato antes de investir num servico
novo: publica-se sobre o tema e mede-se se alguem procura, le e chama. Aqui o
experimento fecha: os numeros dos artigos da oportunidade e um veredito
explicito, que a pessoa confirma ou nao.

Veredito (regra fixa, sem modelo de linguagem):

* **aguardando** — nenhum artigo publicado ainda, ou publicado ha menos de
  28 dias sem nenhum sinal (o Google leva semanas para posicionar);
* **validada** — alguem converteu depois de ler o artigo, ou clicou na
  chamada pelo menos 3 vezes: ha interesse de compra, nao so de leitura;
* **sem tracao** — 60 dias no ar, menos de 100 impressoes no Google e nenhum
  clique na chamada: nem a busca nem o leitor responderam;
* **em andamento** — o resto: ha leitura, falta sinal de compra.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Sum
from django.utils import timezone

ESPERA_MINIMA = 28
PRAZO_DO_TESTE = 60
IMPRESSOES_MINIMAS = 100
CLIQUES_NA_CHAMADA = 3


@dataclass
class ResultadoDoTeste:
    veredito: str = "aguardando"
    motivo: str = ""
    artigos: list = field(default_factory=list)
    dias_no_ar: int | None = None
    impressoes: int = 0
    cliques_no_google: int = 0
    leituras: int = 0
    cliques_na_chamada: int = 0
    conversoes: int = 0

    @property
    def rotulo(self) -> str:
        return {
            "aguardando": "Aguardando",
            "validada": "Validada",
            "sem_tracao": "Sem tracao",
            "em_andamento": "Em andamento",
        }[self.veredito]


def artigos_da_oportunidade(oportunidade) -> list:
    from apps.content.models import Article

    pauta_id = oportunidade.grupo.pauta_id
    if pauta_id is None:
        return []
    return list(
        Article.objects.filter(topic_id=pauta_id, status=Article.Status.PUBLISHED).exclude(
            published_url=""
        )
    )


def resultado(oportunidade, *, painel=None) -> ResultadoDoTeste:
    from apps.radar.atualizacoes import _chave, _primeira_publicacao
    from apps.radar.models import ColetaDoConsole

    saida = ResultadoDoTeste(artigos=artigos_da_oportunidade(oportunidade))
    if not saida.artigos:
        saida.motivo = "nenhum artigo da oportunidade foi publicado ainda"
        return saida

    primeiras = [p for p in (_primeira_publicacao(a) for a in saida.artigos) if p]
    if primeiras:
        saida.dias_no_ar = (timezone.now() - min(primeiras)).days

    coleta = ColetaDoConsole.objects.order_by("-coletada_em").first()
    if coleta is not None:
        paginas = {_chave(a.published_url) for a in saida.artigos}
        for linha in coleta.linhas_set.values("pagina").annotate(
            impressoes=Sum("impressoes"), cliques=Sum("cliques")
        ):
            if _chave(linha["pagina"]) in paginas:
                saida.impressoes += linha["impressoes"] or 0
                saida.cliques_no_google += linha["cliques"] or 0

    if painel is None:
        from apps.content.desempenho import painel as montar_painel

        painel = montar_painel(90)
    ids = {a.remote_id for a in saida.artigos if a.remote_id}
    for linha in painel.linhas:
        if linha.remote_id in ids:
            saida.leituras += linha.engaged_views
            saida.cliques_na_chamada += linha.cta_clicks
            saida.conversoes += linha.participou

    if saida.conversoes or saida.cliques_na_chamada >= CLIQUES_NA_CHAMADA:
        saida.veredito = "validada"
        saida.motivo = "houve interesse de compra: conversao ou cliques na chamada"
    elif (
        saida.dias_no_ar is not None
        and saida.dias_no_ar >= PRAZO_DO_TESTE
        and saida.impressoes < IMPRESSOES_MINIMAS
        and not saida.cliques_na_chamada
    ):
        saida.veredito = "sem_tracao"
        saida.motivo = (
            f"{saida.dias_no_ar} dias no ar, {saida.impressoes} impressoes no Google e nenhum "
            "clique na chamada"
        )
    elif saida.dias_no_ar is not None and saida.dias_no_ar < ESPERA_MINIMA:
        saida.motivo = f"publicado ha {saida.dias_no_ar} dias; o Google leva semanas"
    else:
        saida.veredito = "em_andamento"
        saida.motivo = "ha leitura ou busca, ainda sem sinal de compra"
    return saida
