"""Saude da busca: o que o indice esta devolvendo, e se o limiar ainda serve.

Existe porque o modo de falhar do RAG e silencioso. Nada quebra quando o limiar
fica errado: com ele apertado demais as geracoes passam a falhar por "sem
fonte" e alguem conclui que o acervo e pequeno; com ele solto demais o texto sai
apoiado em trechos que so falam do mesmo assunto, e isso ninguem percebe pela
tela de revisao, porque a citacao parece legitima.

Todas as consultas daqui sao agregacoes sobre uma janela recente, com indice, e
rodam ao abrir a pagina. Nenhuma delas justifica uma rotina em segundo plano —
o que se ganharia em latencia se perderia em ter um numero velho na tela sem
que ninguem saiba de quando ele e.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Aggregate, Count, F, FloatField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.knowledge.models import (
    Document,
    RetrievalHit,
    RetrievalQuery,
    RetrievalSettings,
    SuperChunk,
)

# Janela padrao das metricas. Trinta dias porque a comparacao com os trinta
# anteriores e o que transforma um numero solto ("18% sem resultado") em algo
# acionavel ("era 4%").
JANELA_EM_DIAS = 30

# Abaixo disto nao ha amostra para afirmar nada, e um alerta disparado por duas
# consultas ensina a ignorar alertas.
MINIMO_PARA_ALERTAR = 5

# Fracao de consultas sem nenhuma fonte a partir da qual vale avisar.
FRACAO_SEM_RESULTADO_RUIM = 0.30

# Piso para a comparacao entre periodos valer alguma coisa. Sem ele, um salto
# de 2% para 4% viraria "piorou" — verdade aritmetica, ruido na tela.
PISO_PARA_TENDENCIA = 0.10

# E um piso ABSOLUTO tambem, porque so o percentual nao basta: com 20 consultas,
# uma busca vazia virando duas ja salta de 5% para 10% e cruzaria o piso acima.
# Duas buscas nao sao uma tendencia.
MINIMO_DE_VAZIAS_PARA_TENDENCIA = 3

# Quando a mediana das distancias aceitas chega a esta fracao do limiar, o
# filtro esta trabalhando no limite: qualquer variacao de redacao da consulta
# passa a cair fora.
FRACAO_DE_MARGEM_APERTADA = 0.90


class Percentil(Aggregate):
    """PERCENTILE_CONT do PostgreSQL.

    O Django nao traz percentil pronto. A mediana importa mais que a media
    aqui: a distribuicao de distancias e assimetrica, e uma unica consulta ruim
    desloca a media o bastante para esconder o comportamento tipico.
    """

    function = "PERCENTILE_CONT"
    name = "percentil"
    template = "%(function)s(%(percentil)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expressao, percentil=0.5, **extra):
        super().__init__(expressao, percentil=percentil, **extra)


@dataclass
class MetricasDeJanela:
    consultas: int = 0
    sem_resultado: int = 0
    abaixo_do_alvo: int = 0
    distancia_mediana: float | None = None
    distancia_p90: float | None = None
    distancia_maxima: float | None = None

    @property
    def fracao_sem_resultado(self) -> float:
        return self.sem_resultado / self.consultas if self.consultas else 0.0

    @property
    def percentual_sem_resultado(self) -> int:
        return round(self.fracao_sem_resultado * 100)


@dataclass
class ResumoDaBusca:
    config: RetrievalSettings | None = None
    atual: MetricasDeJanela = field(default_factory=MetricasDeJanela)
    anterior: MetricasDeJanela = field(default_factory=MetricasDeJanela)
    janela_em_dias: int = JANELA_EM_DIAS

    trechos_ativos: int = 0
    trechos_sem_vetor: int = 0
    documentos_indexados: int = 0
    documentos_nunca_citados: int = 0
    modelos_no_indice: list[tuple[str, int]] = field(default_factory=list)
    faixas: list[Faixa] = field(default_factory=list)

    @property
    def modelo_em_uso(self) -> str:
        return settings.EMBEDDING_MODEL

    @property
    def indice_tem_modelo_estranho(self) -> bool:
        """Ha trecho vetorizado por um modelo que nao e o vigente.

        Vetores de modelos diferentes ocupam espacos diferentes. Misturados na
        mesma consulta, as distancias deixam de ser comparaveis entre si e a
        ordenacao vira sorteio — sem erro nenhum no caminho.
        """
        return any(nome != self.modelo_em_uso for nome, _total in self.modelos_no_indice)

    @property
    def margem_ate_o_limiar(self) -> float | None:
        """Quanto ainda sobra entre a fonte tipica aceita e o corte."""
        if self.config is None or self.atual.distancia_mediana is None:
            return None
        return self.config.max_cosine_distance - self.atual.distancia_mediana

    @property
    def margem_esta_apertada(self) -> bool:
        if self.config is None or self.atual.distancia_mediana is None:
            return False
        limiar = self.config.max_cosine_distance
        if not limiar:
            return False
        return self.atual.distancia_mediana >= limiar * FRACAO_DE_MARGEM_APERTADA

    @property
    def piorou(self) -> bool:
        """Deterioracao de verdade, e nao oscilacao.

        Tres condicoes, e nao uma: cresceu metade, passou de um piso percentual
        E ha buscas vazias em numero que sustente a leitura. So a primeira
        chamaria "4% para 5%" de piora; sem a terceira, uma busca vazia virando
        duas ja cruzaria o piso percentual num periodo de vinte consultas.
        Marcar ruido como problema e o caminho mais curto para o painel parar de
        ser lido.
        """
        if self.anterior.consultas < MINIMO_PARA_ALERTAR:
            return False
        if self.atual.consultas < MINIMO_PARA_ALERTAR:
            return False
        if self.atual.sem_resultado < MINIMO_DE_VAZIAS_PARA_TENDENCIA:
            return False
        atual = self.atual.fracao_sem_resultado
        return atual >= PISO_PARA_TENDENCIA and atual >= self.anterior.fracao_sem_resultado * 1.5


@dataclass
class Faixa:
    """Uma barra do histograma de distancias."""

    inicio: float
    fim: float
    total: int
    aceita: bool
    # O pico da serie, para a barra ter altura relativa. Guardado por faixa
    # porque o template nao tem como calcular o maximo da lista sozinho.
    pico: int = 1

    @property
    def rotulo(self) -> str:
        return f"{self.inicio:.2f} a {self.fim:.2f}"

    @property
    def altura(self) -> int:
        return round(self.total / self.pico * 100) if self.pico else 0


def _metricas(inicio, fim) -> MetricasDeJanela:
    consultas = RetrievalQuery.objects.filter(created_at__gte=inicio, created_at__lt=fim)

    total = consultas.count()
    # "Sem resultado" e a metrica que mais importa: e a geracao que falhou antes
    # de chamar o modelo, e do lado de fora ela aparece so como um trabalho que
    # nao andou. Contada numa consulta propria de proposito: junta-la a contagem
    # total no mesmo `aggregate` faz o LEFT JOIN de `hits` multiplicar as linhas
    # e o total sai inflado.
    vazias = consultas.filter(hits__isnull=True).count()

    distancias = RetrievalHit.objects.filter(
        query__created_at__gte=inicio, query__created_at__lt=fim
    ).aggregate(
        mediana=Percentil("distance", 0.5),
        p90=Percentil("distance", 0.9),
        maxima=Percentil("distance", 1.0),
    )

    # Consultas que trouxeram alguma fonte, mas menos do que pediram. Nao e
    # falha; e o sinal de que o acervo esta raso para o tema.
    abaixo = (
        consultas.annotate(encontradas=Count("hits"))
        .filter(encontradas__gt=0, encontradas__lt=F("top_k"))
        .count()
    )

    return MetricasDeJanela(
        consultas=total,
        sem_resultado=vazias,
        abaixo_do_alvo=abaixo,
        distancia_mediana=distancias["mediana"],
        distancia_p90=distancias["p90"],
        distancia_maxima=distancias["maxima"],
    )


def montar_resumo_da_busca(dias: int = JANELA_EM_DIAS) -> ResumoDaBusca:
    """Estado da recuperacao neste tenant. Barato o bastante para toda visita."""
    agora = timezone.now()
    inicio = agora - timedelta(days=dias)
    inicio_anterior = inicio - timedelta(days=dias)

    config = RetrievalSettings.carregar()

    resumo = ResumoDaBusca(
        config=config,
        atual=_metricas(inicio, agora),
        anterior=_metricas(inicio_anterior, inicio),
        janela_em_dias=dias,
    )

    ativos = SuperChunk.objects.filter(is_active=True)
    resumo.trechos_ativos = ativos.filter(embedding__isnull=False).count()
    resumo.trechos_sem_vetor = ativos.filter(embedding__isnull=True).count()
    resumo.documentos_indexados = ativos.values("document_id").distinct().count()
    # `values("pk")` antes do `distinct` de proposito: sem isso o DISTINCT cai
    # sobre a linha inteira do documento, e o custo cresce com o numero de
    # colunas em vez de com o numero de documentos.
    resumo.documentos_nunca_citados = (
        Document.objects.filter(chunks__is_active=True)
        .exclude(chunks__hits__isnull=False)
        .values("pk")
        .distinct()
        .count()
    )
    resumo.modelos_no_indice = sorted(
        ativos.exclude(embedding_model="")
        .values_list("embedding_model")
        .annotate(total=Count("id"))
        .order_by(),
        key=lambda linha: -linha[1],
    )
    resumo.faixas = _histograma(inicio, agora, config.max_cosine_distance)
    return resumo


def _histograma(inicio, fim, limiar: float, faixas: int = 12) -> list[Faixa]:
    """Onde as distancias aceitas se acumulam, em relacao ao corte.

    O histograma responde a pergunta que um numero sozinho nao responde: se a
    massa esta colada no limiar, o corte esta no lugar errado; se ha um vale
    claro antes dele, esta no lugar certo.
    """
    distancias = list(
        RetrievalHit.objects.filter(
            query__created_at__gte=inicio, query__created_at__lt=fim
        ).values_list("distance", flat=True)
    )
    if not distancias:
        return []

    # A escala vai um pouco alem do limiar de proposito: sem isso o grafico
    # nunca mostraria o que ficou de fora por pouco.
    topo = max(max(distancias), limiar * 1.25) or 1.0
    largura = topo / faixas

    baldes = [0] * faixas
    for d in distancias:
        indice = min(int(d / largura), faixas - 1)
        baldes[indice] += 1

    pico = max(baldes) or 1
    return [
        Faixa(
            inicio=i * largura,
            fim=(i + 1) * largura,
            total=total,
            aceita=(i * largura) < limiar,
            pico=pico,
        )
        for i, total in enumerate(baldes)
    ]


def alertas_da_busca(resumo: ResumoDaBusca) -> list[str]:
    """So o que muda o que alguem faria hoje.

    Cada linha aqui e uma condicao que nao levanta erro em lugar nenhum e que,
    sem aviso, so apareceria como "os artigos andam ruins" muito depois.
    """
    config = resumo.config
    avisos: list[str] = []

    if config is None:
        return avisos

    if config.calibracao_e_de_outro_modelo:
        avisos.append(
            str(
                _(
                    "O limiar foi medido com %(antigo)s e o modelo em uso e "
                    "%(atual)s. Distancia de cosseno nao e comparavel entre "
                    "modelos: o valor atual nao quer dizer nada. Refaca o teste."
                )
            )
            % {"antigo": config.calibrated_model, "atual": resumo.modelo_em_uso}
        )
    elif not config.foi_calibrado and resumo.trechos_ativos:
        avisos.append(
            str(
                _(
                    "O limiar ainda e o valor de fabrica, medido em outro acervo. "
                    "Teste uma consulta real na tela de qualidade da busca antes "
                    "de confiar no filtro."
                )
            )
        )

    if resumo.indice_tem_modelo_estranho:
        avisos.append(
            str(
                _(
                    "Ha trechos no indice vetorizados por outro modelo. As "
                    "distancias deixam de ser comparaveis entre si; reindexe os "
                    "documentos afetados."
                )
            )
        )

    if resumo.trechos_sem_vetor:
        avisos.append(
            str(_("%(total)s trecho(s) marcados mas sem vetor: nunca serao encontrados."))
            % {"total": resumo.trechos_sem_vetor}
        )

    atual = resumo.atual
    if atual.consultas >= MINIMO_PARA_ALERTAR and (
        atual.fracao_sem_resultado >= FRACAO_SEM_RESULTADO_RUIM
    ):
        avisos.append(
            str(
                _(
                    "%(pct)s%% das buscas dos ultimos %(dias)s dias nao acharam "
                    "nenhuma fonte. Ou o acervo nao cobre os temas pedidos, ou o "
                    "limiar esta apertado demais."
                )
            )
            % {"pct": atual.percentual_sem_resultado, "dias": resumo.janela_em_dias}
        )

    if resumo.piorou:
        avisos.append(
            str(
                _(
                    "As buscas sem fonte subiram de %(antes)s%% para %(agora)s%% em "
                    "relacao ao periodo anterior. Ou entraram pautas de temas que o "
                    "acervo nao cobre, ou algo mudou no indice."
                )
            )
            % {
                "antes": resumo.anterior.percentual_sem_resultado,
                "agora": atual.percentual_sem_resultado,
            }
        )

    if resumo.margem_esta_apertada:
        avisos.append(
            str(
                _(
                    "A fonte tipica aceita esta a %(margem).4f do corte. O filtro "
                    "trabalha no limite: uma consulta redigida de outro jeito ja "
                    "cai fora."
                )
            )
            % {"margem": resumo.margem_ate_o_limiar or 0.0}
        )

    return avisos
