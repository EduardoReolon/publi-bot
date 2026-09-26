"""A rodada do radar, e a busca manual.

Uma rodada:

1. parte das palavras-semente (e, sem elas, do nicho do site);
2. para cada semente, busca e colhe "as pessoas tambem perguntam" e as buscas
   relacionadas;
3. junta as perguntas dos visitantes do site — poucas, mas do publico certo;
4. pede o volume de busca de tudo numa chamada so (o custo e por chamada);
5. agrupa, da nota, e propoe como pauta os melhores grupos ainda nao propostos.

A intensidade decide quantas buscas e quantas pautas. O teto de custo pode
parar a rodada no meio: o que ja foi colhido fica, e a rodada registra que
parou no teto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.radar import custos
from apps.radar.agrupamento import (
    agrupar,
    pontuar,
    vetor_do_negocio,
    vetores_do_que_ja_foi_escrito,
)
from apps.radar.models import (
    BuscaManual,
    ChamadaExterna,
    ConfiguracaoDoRadar,
    ContasExternas,
    GrupoDeDemanda,
    RodadaDoRadar,
    SinalDeDemanda,
)
from apps.radar.provedores import ProvedorIndisponivel, buscar, volume_dataforseo

logger = logging.getLogger("publibot.radar")


@dataclass(frozen=True)
class Plano:
    rodadas_por_semana: int
    buscas: int
    pautas: int
    videos: int


INTENSIDADES = {
    "minimo": Plano(rodadas_por_semana=1, buscas=5, pautas=2, videos=0),
    "normal": Plano(rodadas_por_semana=2, buscas=10, pautas=4, videos=5),
    "intenso": Plano(rodadas_por_semana=3, buscas=20, pautas=6, videos=10),
}


def plano_de(config: ConfiguracaoDoRadar) -> Plano:
    # Rodada pedida na tela com o radar desligado usa o minimo: quem clicou
    # quer ver o radar funcionar, e o minimo e o que menos gasta.
    return INTENSIDADES.get(config.intensidade, INTENSIDADES["minimo"])


def rodada_devida(config: ConfiguracaoDoRadar, agora=None) -> bool:
    """Se ja passou o intervalo desde a ultima rodada, pela intensidade."""
    if config.intensidade == ConfiguracaoDoRadar.Intensidade.DESLIGADO:
        return False
    agora = agora or timezone.now()
    if config.ultima_rodada_em is None:
        return True
    intervalo = timezone.timedelta(days=7 / plano_de(config).rodadas_por_semana)
    return agora - config.ultima_rodada_em >= intervalo


def _ja_existe(texto: str) -> bool:
    return SinalDeDemanda.objects.filter(texto__iexact=texto.strip()).exists()


def _novo_sinal(texto: str, fonte: str, *, rodada=None, busca=None, situacao=None, **extra):
    texto = (texto or "").strip()
    if len(texto) < 3:
        return None
    if busca is None and _ja_existe(texto):
        return None
    return SinalDeDemanda.objects.create(
        texto=texto[:500],
        fonte=fonte,
        rodada=rodada,
        busca_manual=busca,
        situacao=situacao or SinalDeDemanda.Situacao.ATIVO,
        extra=extra,
    )


def _sementes(config: ConfiguracaoDoRadar) -> list[str]:
    sementes = config.lista_de_sementes
    if sementes:
        return sementes
    from apps.integrations.models import Site

    site = Site.objects.first()
    return [site.niche] if site and site.niche else []


def _colher_da_busca(consulta: str, *, rodada, finalidade: str) -> list[SinalDeDemanda]:
    resultado = buscar(consulta, finalidade=finalidade)
    novos = []
    for pergunta in resultado.perguntas:
        novos.append(
            _novo_sinal(
                pergunta,
                SinalDeDemanda.Fonte.PERGUNTA_RELACIONADA,
                rodada=rodada,
                semente=consulta,
                provedor=resultado.provedor,
            )
        )
    for relacionada in resultado.relacionadas:
        novos.append(
            _novo_sinal(
                relacionada,
                SinalDeDemanda.Fonte.BUSCA_RELACIONADA,
                rodada=rodada,
                semente=consulta,
                provedor=resultado.provedor,
            )
        )
    return [s for s in novos if s is not None]


def _perguntas_do_site(rodada, limite: int = 50) -> list[SinalDeDemanda]:
    from apps.content.models import Question

    novos = []
    for texto in Question.objects.order_by("-submitted_at").values_list("question_text", flat=True)[
        :limite
    ]:
        sinal = _novo_sinal(texto, SinalDeDemanda.Fonte.PERGUNTA_DO_SITE, rodada=rodada)
        if sinal is not None:
            novos.append(sinal)
    return novos


def _preencher_volumes(sinais: list[SinalDeDemanda], *, finalidade: str) -> None:
    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()
    candidatos = [
        s for s in sinais if s.volume is None and s.fonte != SinalDeDemanda.Fonte.PERGUNTA_DO_SITE
    ]
    if not candidatos or not contas.tem_dataforseo:
        return
    volumes = volume_dataforseo(
        [s.texto for s in candidatos], config=config, contas=contas, finalidade=finalidade
    )
    for sinal in candidatos:
        volume = volumes.get(sinal.texto.strip().lower())
        if volume is not None:
            sinal.volume = volume
            sinal.save(update_fields=["volume"])


def executar_rodada(origem: str = RodadaDoRadar.Origem.AGENDADA) -> RodadaDoRadar:
    config = ConfiguracaoDoRadar.carregar()
    plano = plano_de(config)
    rodada = RodadaDoRadar.objects.create(origem=origem)
    inicio = rodada.iniciada_em
    resumo = {"sementes": [], "sinais": 0, "erros": [], "pautas": []}
    finalidade = ChamadaExterna.Finalidade.RADAR

    try:
        novos: list[SinalDeDemanda] = []
        for semente in _sementes(config)[: plano.buscas]:
            resumo["sementes"].append(semente)
            sinal = _novo_sinal(semente, SinalDeDemanda.Fonte.SEMENTE, rodada=rodada)
            if sinal is not None:
                novos.append(sinal)
            if config.usar_serp:
                try:
                    novos += _colher_da_busca(semente, rodada=rodada, finalidade=finalidade)
                except ProvedorIndisponivel as exc:
                    resumo["erros"].append(f"{semente}: {exc}")

        if config.usar_perguntas_do_site:
            novos += _perguntas_do_site(rodada)

        novos += _coletas_extras(config, plano, rodada, resumo)

        if config.usar_volume:
            try:
                _preencher_volumes(novos, finalidade=finalidade)
            except ProvedorIndisponivel as exc:
                resumo["erros"].append(f"volume: {exc}")

        resumo["sinais"] = len(novos)
        grupos = agrupar(novos)
        resumo["pautas"] = propor_pautas(grupos, limite=plano.pautas)
        rodada.situacao = RodadaDoRadar.Situacao.CONCLUIDA
    except custos.TetoAtingido as exc:
        rodada.situacao = RodadaDoRadar.Situacao.PARADA_NO_TETO
        rodada.erro = str(exc)
    except Exception as exc:
        logger.exception("Rodada do radar falhou")
        rodada.situacao = RodadaDoRadar.Situacao.FALHOU
        rodada.erro = str(exc)[:2000]

    rodada.custo_usd = ChamadaExterna.objects.filter(criado_em__gte=inicio).aggregate(
        t=Sum("custo_usd")
    )["t"] or Decimal("0")
    rodada.resumo = resumo
    rodada.concluida_em = timezone.now()
    rodada.save()

    config.ultima_rodada_em = rodada.concluida_em
    config.save(update_fields=["ultima_rodada_em"])
    return rodada


def _coletas_extras(config, plano, rodada, resumo) -> list[SinalDeDemanda]:
    """YouTube e Search Console, quando ligados. Falha de um nao para a rodada."""
    novos: list[SinalDeDemanda] = []
    if config.usar_youtube and plano.videos:
        try:
            from apps.radar.youtube import colher_sinais

            novos += colher_sinais(_sementes(config), rodada=rodada, videos=plano.videos)
        except ProvedorIndisponivel as exc:
            resumo["erros"].append(f"youtube: {exc}")
    if config.usar_search_console:
        try:
            from apps.radar.search_console import colher_sinais as colher_do_console

            novos += colher_do_console(rodada=rodada)
        except ProvedorIndisponivel as exc:
            resumo["erros"].append(f"search console: {exc}")
    return novos


def propor_pautas(
    ids_de_grupos: set, *, limite: int, nota_minima: float | None = None
) -> list[str]:
    """Pontua os grupos e transforma os melhores em pautas sugeridas.

    A pauta nasce SUGERIDA: quem decide se ela vale e uma pessoa. O que vai
    junto e a evidencia — os sinais, o volume, a nota e as parcelas —, para
    a decisao nao ser sobre um titulo solto.
    """
    from apps.content.models import Topic

    negocio = vetor_do_negocio()
    ja_escrito = vetores_do_que_ja_foi_escrito()
    if nota_minima is None:
        nota_minima = float(getattr(settings, "RADAR_NOTA_MINIMA", 40))

    candidatos = []
    for grupo in GrupoDeDemanda.objects.filter(pk__in=ids_de_grupos):
        pontuar(grupo, vetor_do_negocio=negocio, ja_escrito=ja_escrito)
        if grupo.situacao == GrupoDeDemanda.Situacao.NOVO and grupo.nota >= nota_minima:
            # Muito perto do que ja existe nao vira pauta: seria outro texto
            # competindo com o anterior pela mesma busca.
            if grupo.parcelas.get("canibalizacao", 0) < 0.8:
                candidatos.append(grupo)

    criadas = []
    for grupo in sorted(candidatos, key=lambda g: -g.nota)[:limite]:
        sinais = list(grupo.sinais.exclude(situacao=SinalDeDemanda.Situacao.DESCARTADO)[:12])
        briefing = "Demanda observada:\n" + "\n".join(
            f"- {s.texto}" + (f" ({s.volume}/mes)" if s.volume is not None else "") for s in sinais
        )
        pauta = Topic.objects.create(
            title=grupo.rotulo[:300],
            target_keyword=grupo.rotulo[:120],
            briefing=briefing,
            status=Topic.Status.SUGGESTED,
            origin=Topic.Origin.RADAR,
            demand_score=grupo.nota,
            cannibalization_score=grupo.parcelas.get("canibalizacao", 0),
            evidence={
                "nota": grupo.nota,
                "parcelas": grupo.parcelas,
                "volume_total": grupo.volume_total,
                "sinais": [
                    {"texto": s.texto, "fonte": s.get_fonte_display(), "volume": s.volume}
                    for s in sinais
                ],
            },
        )
        grupo.situacao = GrupoDeDemanda.Situacao.PAUTA
        grupo.pauta = pauta
        grupo.save(update_fields=["situacao", "pauta", "atualizado_em"])
        criadas.append(pauta.title)
    return criadas


# ---------------------------------------------------------------------------
# Busca manual
# ---------------------------------------------------------------------------
def busca_manual(consulta: str, *, com_volume: bool = False) -> BuscaManual:
    """Busca por curiosidade, na tela. Os sinais ficam esperando decisao.

    O custo vai para o livro-caixa como "busca manual", seja qual for a
    decisao depois.
    """
    busca = BuscaManual.objects.create(consulta=consulta[:500], com_volume=com_volume)
    finalidade = ChamadaExterna.Finalidade.MANUAL
    try:
        resultado = buscar(consulta, finalidade=finalidade)
        sinais = [
            _novo_sinal(
                consulta,
                SinalDeDemanda.Fonte.MANUAL,
                busca=busca,
                situacao=SinalDeDemanda.Situacao.PENDENTE,
            )
        ]
        for pergunta in resultado.perguntas:
            sinais.append(
                _novo_sinal(
                    pergunta,
                    SinalDeDemanda.Fonte.PERGUNTA_RELACIONADA,
                    busca=busca,
                    situacao=SinalDeDemanda.Situacao.PENDENTE,
                )
            )
        for relacionada in resultado.relacionadas:
            sinais.append(
                _novo_sinal(
                    relacionada,
                    SinalDeDemanda.Fonte.BUSCA_RELACIONADA,
                    busca=busca,
                    situacao=SinalDeDemanda.Situacao.PENDENTE,
                )
            )
        sinais = [s for s in sinais if s is not None]
        if com_volume:
            _preencher_volumes(sinais, finalidade=finalidade)
        busca.resultados = [{"url": r.url, "titulo": r.titulo} for r in resultado.resultados[:10]]
    except (ProvedorIndisponivel, custos.TetoAtingido) as exc:
        busca.erro = str(exc)
    busca.save()
    return busca


def decidir_busca(busca: BuscaManual, *, guardar: bool) -> None:
    """Guardar: os sinais entram no radar e sao agrupados. Descartar: nao."""
    if guardar:
        sinais = list(busca.sinais.all())
        # Sinal que ja existe no radar nao entra duplicado.
        mantidos = []
        for sinal in sinais:
            if (
                SinalDeDemanda.objects.filter(texto__iexact=sinal.texto)
                .exclude(pk=sinal.pk)
                .exists()
            ):
                sinal.delete()
                continue
            sinal.situacao = SinalDeDemanda.Situacao.ATIVO
            sinal.save(update_fields=["situacao"])
            mantidos.append(sinal)
        grupos = agrupar(mantidos)
        negocio = vetor_do_negocio()
        ja_escrito = vetores_do_que_ja_foi_escrito()
        for grupo in GrupoDeDemanda.objects.filter(pk__in=grupos):
            pontuar(grupo, vetor_do_negocio=negocio, ja_escrito=ja_escrito)
        busca.decisao = BuscaManual.Decisao.GUARDADA
    else:
        busca.sinais.update(situacao=SinalDeDemanda.Situacao.DESCARTADO)
        busca.decisao = BuscaManual.Decisao.DESCARTADA
    busca.save(update_fields=["decisao"])
