"""A rodada do radar, e a busca manual.

Uma rodada, em fases:

1. **coleta** — parte das palavras-semente (e, sem elas, do nicho do site);
   busca cada uma e colhe "as pessoas tambem perguntam" e as buscas
   relacionadas; junta as perguntas dos visitantes, YouTube, Search Console e
   concorrentes;
2. **volume** — pede o volume de busca de tudo numa tarefa so (o custo e por
   tarefa);
3. **fim** — agrupa, da nota e propoe como pauta os melhores grupos ainda nao
   propostos.

Com a DataForSEO na fila padrao (o normal), as buscas e o volume sao POSTADOS
e a rodada fica "aguardando": o batimento `colher_fila` colhe os resultados e
avanca a fase quando tudo da fase voltou. Ao vivo, tudo acontece de uma vez.

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
    TarefaNaFila,
)
from apps.radar.provedores import (
    ProvedorIndisponivel,
    buscar,
    palavra_para_volume,
    volume_dataforseo,
)

logger = logging.getLogger("publibot.radar")


@dataclass(frozen=True)
class Plano:
    rodadas_por_semana: int
    buscas: int
    pautas: int
    videos: int
    # Por concorrente: paginas do sitemap, buscas do Labs e avaliacoes.
    paginas_de_concorrente: int = 50
    buscas_de_concorrente: int = 50
    avaliacoes: int = 20


INTENSIDADES = {
    "minimo": Plano(rodadas_por_semana=1, buscas=5, pautas=2, videos=0),
    "normal": Plano(
        rodadas_por_semana=2,
        buscas=10,
        pautas=4,
        videos=5,
        paginas_de_concorrente=100,
        buscas_de_concorrente=100,
        avaliacoes=40,
    ),
    "intenso": Plano(
        rodadas_por_semana=3,
        buscas=20,
        pautas=6,
        videos=10,
        paginas_de_concorrente=200,
        buscas_de_concorrente=200,
        avaliacoes=60,
    ),
}

# Fontes que nao vao para o pedido de volume: a do site e a avaliacao sao
# frases, nao buscas; Search Console e Labs ja trazem o numero.
SEM_PEDIDO_DE_VOLUME = {
    SinalDeDemanda.Fonte.PERGUNTA_DO_SITE,
    SinalDeDemanda.Fonte.AVALIACAO,
    SinalDeDemanda.Fonte.YOUTUBE,
}


def plano_de(config: ConfiguracaoDoRadar) -> Plano:
    # Rodada pedida na tela com o radar desligado usa o minimo: quem clicou
    # quer ver o radar funcionar, e o minimo e o que menos gasta.
    return INTENSIDADES.get(config.intensidade, INTENSIDADES["minimo"])


def rodada_devida(config: ConfiguracaoDoRadar, agora=None) -> bool:
    """Se ja passou o intervalo desde a ultima rodada, pela intensidade."""
    if config.intensidade == ConfiguracaoDoRadar.Intensidade.DESLIGADO:
        return False
    if RodadaDoRadar.objects.filter(situacao=RodadaDoRadar.Situacao.AGUARDANDO).exists():
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
    return _sinais_da_serp(buscar(consulta, finalidade=finalidade), consulta, rodada=rodada)


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
    """Volume ao vivo, numa chamada so. Usado pela busca manual e pela rodada
    com a DataForSEO no modo ao vivo."""
    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()
    candidatos = _precisam_de_volume(sinais)
    if not candidatos or not contas.tem_dataforseo:
        return
    volumes = volume_dataforseo(
        [s.texto for s in candidatos], config=config, contas=contas, finalidade=finalidade
    )
    _aplicar_volumes(candidatos, volumes)


def _precisam_de_volume(sinais) -> list[SinalDeDemanda]:
    return [
        s
        for s in sinais
        if s.volume is None
        and s.fonte not in SEM_PEDIDO_DE_VOLUME
        and palavra_para_volume(s.texto) is not None
    ]


def _aplicar_volumes(sinais, volumes: dict[str, int]) -> None:
    for sinal in sinais:
        volume = volumes.get(palavra_para_volume(sinal.texto) or "")
        if volume is not None:
            sinal.volume = volume
            sinal.save(update_fields=["volume"])


def _usa_fila(config: ConfiguracaoDoRadar, contas: ContasExternas) -> bool:
    return (
        contas.tem_dataforseo and config.modo_dataforseo == ConfiguracaoDoRadar.ModoDataForSEO.FILA
    )


def _corpo_serp(config: ConfiguracaoDoRadar, consulta: str) -> dict:
    return {
        "keyword": consulta,
        "location_code": config.codigo_de_local,
        "language_code": config.codigo_de_idioma,
        "device": "desktop",
        "depth": 10,
    }


def _sinais_da_serp(resultado, consulta: str, *, rodada) -> list[SinalDeDemanda]:
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


def executar_rodada(origem: str = RodadaDoRadar.Origem.AGENDADA) -> RodadaDoRadar:
    """Comeca uma rodada: a fase de coleta inteira, e avanca o quanto der."""
    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()
    plano = plano_de(config)
    rodada = RodadaDoRadar.objects.create(origem=origem)
    resumo = {"sementes": [], "sinais": 0, "erros": [], "pautas": [], "tarefas": 0}
    rodada.resumo = resumo
    finalidade = ChamadaExterna.Finalidade.RADAR
    # Marcada no inicio: com a fila, a rodada dura minutos, e o batimento nao
    # pode comecar outra nesse meio tempo.
    config.ultima_rodada_em = rodada.iniciada_em
    config.save(update_fields=["ultima_rodada_em"])
    fila = _usa_fila(config, contas)

    try:
        sementes = _sementes(config)[: plano.buscas]
        for semente in sementes:
            resumo["sementes"].append(semente)
            _novo_sinal(semente, SinalDeDemanda.Fonte.SEMENTE, rodada=rodada)

        if config.usar_serp and sementes:
            if fila and config.buscador == ConfiguracaoDoRadar.Buscador.DATAFORSEO:
                from apps.radar.fila import postar

                try:
                    resumo["tarefas"] += len(
                        postar(
                            TarefaNaFila.Tipo.SERP,
                            [(_corpo_serp(config, s), {"semente": s}) for s in sementes],
                            contas=contas,
                            finalidade=finalidade,
                            rodada=rodada,
                        )
                    )
                except ProvedorIndisponivel as exc:
                    resumo["erros"].append(f"busca: {exc}")
            else:
                for semente in sementes:
                    try:
                        _colher_da_busca(semente, rodada=rodada, finalidade=finalidade)
                    except ProvedorIndisponivel as exc:
                        resumo["erros"].append(f"{semente}: {exc}")

        if config.usar_perguntas_do_site:
            _perguntas_do_site(rodada)

        _coletas_extras(config, plano, rodada, resumo)
        _coletar_concorrentes(config, contas, plano, rodada, resumo, fila=fila)
    except custos.TetoAtingido as exc:
        return _encerrar(rodada, RodadaDoRadar.Situacao.PARADA_NO_TETO, str(exc))
    except Exception as exc:
        logger.exception("Rodada do radar falhou")
        return _encerrar(rodada, RodadaDoRadar.Situacao.FALHOU, str(exc)[:2000])

    rodada.save(update_fields=["resumo"])
    return avancar(rodada)


def avancar(rodada: RodadaDoRadar) -> RodadaDoRadar:
    """Leva a rodada adiante ate onde as tarefas da fila deixarem.

    Chamada no fim da coleta e a cada batimento de `colher_fila`. Idempotente:
    com tarefa da fase ainda esperando, nao faz nada.
    """
    if rodada.tarefas.filter(situacao=TarefaNaFila.Situacao.AGUARDANDO).exists():
        if rodada.situacao != RodadaDoRadar.Situacao.AGUARDANDO:
            rodada.situacao = RodadaDoRadar.Situacao.AGUARDANDO
            rodada.save(update_fields=["situacao"])
        return rodada

    config = ConfiguracaoDoRadar.carregar()
    contas = ContasExternas.carregar()
    plano = plano_de(config)
    resumo = rodada.resumo
    finalidade = ChamadaExterna.Finalidade.RADAR
    try:
        if rodada.fase == "coleta":
            rodada.fase = "volume"
            candidatos = _precisam_de_volume(_sem_grupo())
            if config.usar_volume and candidatos and contas.tem_dataforseo:
                if _usa_fila(config, contas):
                    from apps.radar.fila import postar

                    palavras = list(dict.fromkeys(palavra_para_volume(s.texto) for s in candidatos))
                    corpo = {
                        "keywords": palavras[:1000],
                        "location_code": config.codigo_de_local,
                        "language_code": config.codigo_de_idioma,
                    }
                    try:
                        postar(
                            TarefaNaFila.Tipo.VOLUME,
                            [(corpo, {})],
                            contas=contas,
                            finalidade=finalidade,
                            rodada=rodada,
                        )
                    except ProvedorIndisponivel as exc:
                        # Sem volume a rodada ainda propoe: a nota usa o
                        # tamanho do grupo.
                        resumo.setdefault("erros", []).append(f"volume: {exc}")
                    else:
                        resumo["tarefas"] = resumo.get("tarefas", 0) + 1
                        rodada.situacao = RodadaDoRadar.Situacao.AGUARDANDO
                        rodada.save(update_fields=["fase", "situacao", "resumo"])
                        return rodada
                try:
                    _preencher_volumes(candidatos, finalidade=finalidade)
                except ProvedorIndisponivel as exc:
                    resumo.setdefault("erros", []).append(f"volume: {exc}")

        rodada.fase = "fim"
        sinais = _sem_grupo()
        resumo["sinais"] = len(sinais)
        grupos = agrupar(sinais)
        resumo["pautas"] = propor_pautas(grupos, limite=plano.pautas)
    except custos.TetoAtingido as exc:
        return _encerrar(rodada, RodadaDoRadar.Situacao.PARADA_NO_TETO, str(exc))
    except Exception as exc:
        logger.exception("Rodada do radar falhou")
        return _encerrar(rodada, RodadaDoRadar.Situacao.FALHOU, str(exc)[:2000])
    return _encerrar(rodada, RodadaDoRadar.Situacao.CONCLUIDA)


def _sem_grupo() -> list[SinalDeDemanda]:
    """Todo sinal ativo ainda sem grupo — inclusive o que sobrou de rodada que
    parou no teto, ou de tarefa colhida depois de a rodada terminar."""
    return list(
        SinalDeDemanda.objects.filter(situacao=SinalDeDemanda.Situacao.ATIVO, grupo__isnull=True)
    )


def _encerrar(rodada: RodadaDoRadar, situacao: str, erro: str = "") -> RodadaDoRadar:
    rodada.situacao = situacao
    rodada.erro = erro
    rodada.concluida_em = timezone.now()
    # O custo da rodada e o das chamadas do radar desde que ela comecou. A busca
    # manual e a de fontes tem finalidade propria e nao entram.
    rodada.custo_usd = ChamadaExterna.objects.filter(
        criado_em__gte=rodada.iniciada_em,
        finalidade__in=[
            ChamadaExterna.Finalidade.RADAR,
            ChamadaExterna.Finalidade.CONCORRENTES,
        ],
    ).aggregate(t=Sum("custo_usd"))["t"] or Decimal("0")
    rodada.save()
    # Tarefa que ainda estiver na fila e colhida mesmo assim (ja foi paga); o
    # sinal dela entra na proxima rodada.
    return rodada


def colher_fila() -> int:
    """Colhe as tarefas prontas e avanca as rodadas. Devolve quantas colheu."""
    from apps.radar import fila

    contas = ContasExternas.carregar()
    agora = timezone.now()
    colhidas = 0
    for tarefa in TarefaNaFila.objects.filter(situacao=TarefaNaFila.Situacao.AGUARDANDO):
        if fila.vencida(tarefa, agora):
            tarefa.situacao = TarefaNaFila.Situacao.EXPIRADA
            tarefa.save(update_fields=["situacao"])
            continue
        try:
            resultado = fila.colher(tarefa, contas)
        except ProvedorIndisponivel as exc:
            tarefa.situacao = TarefaNaFila.Situacao.FALHOU
            tarefa.erro = str(exc)[:2000]
            tarefa.save(update_fields=["situacao", "erro"])
            if tarefa.rodada_id:
                tarefa.rodada.resumo.setdefault("erros", []).append(f"{tarefa}: {exc}")
                tarefa.rodada.save(update_fields=["resumo"])
            continue
        if resultado is None:
            continue
        _processar(tarefa, resultado)
        tarefa.situacao = TarefaNaFila.Situacao.COLHIDA
        tarefa.colhida_em = timezone.now()
        tarefa.save(update_fields=["situacao", "colhida_em"])
        colhidas += 1

    for rodada in RodadaDoRadar.objects.filter(situacao=RodadaDoRadar.Situacao.AGUARDANDO):
        avancar(rodada)
    return colhidas


def _processar(tarefa: TarefaNaFila, resultado: dict) -> None:
    from apps.radar.provedores import ler_serp, ler_volumes

    if tarefa.tipo == TarefaNaFila.Tipo.SERP:
        _sinais_da_serp(
            ler_serp(resultado), tarefa.contexto.get("semente", ""), rodada=tarefa.rodada
        )
    elif tarefa.tipo == TarefaNaFila.Tipo.VOLUME:
        _aplicar_volumes(_precisam_de_volume(_sem_grupo()), ler_volumes(resultado))
    elif tarefa.tipo == TarefaNaFila.Tipo.AVALIACOES:
        from apps.radar.concorrentes import ler_avaliacoes

        ler_avaliacoes(resultado, tarefa.contexto, rodada=tarefa.rodada)


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


def _coletar_concorrentes(config, contas, plano, rodada, resumo, *, fila: bool) -> None:
    """Sitemap (gratuito), buscas do Labs e avaliacoes (pagos)."""
    if not config.lista_de_concorrentes:
        return
    from apps.radar import concorrentes

    if config.usar_concorrentes_conteudo:
        concorrentes.colher_conteudo(config, rodada=rodada, limite=plano.paginas_de_concorrente)
    if config.usar_concorrentes_buscas:
        try:
            concorrentes.colher_buscas(
                config, contas, rodada=rodada, limite=plano.buscas_de_concorrente
            )
        except ProvedorIndisponivel as exc:
            resumo["erros"].append(f"concorrentes (buscas): {exc}")
    if config.usar_avaliacoes:
        if not contas.tem_dataforseo:
            resumo["erros"].append("avaliacoes: precisa da conta da DataForSEO.")
            return
        # As avaliacoes so existem na fila, mesmo com o modo ao vivo.
        try:
            resumo["tarefas"] += concorrentes.postar_avaliacoes(
                config, contas, rodada=rodada, profundidade=plano.avaliacoes
            )
        except ProvedorIndisponivel as exc:
            resumo["erros"].append(f"avaliacoes: {exc}")


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
        if not grupo.sinais.exclude(fonte=SinalDeDemanda.Fonte.AVALIACAO).exists():
            # Avaliacao sozinha e a frase de um cliente, nao um tema: ela
            # reforca o grupo que outra fonte trouxe, mas nao vira pauta.
            continue
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
