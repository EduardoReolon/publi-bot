"""O radar como fornecedor de fontes.

Cada rodada ja traz a primeira pagina do Google para dezenas de buscas — paga.
Algumas dessas paginas sao boas fontes para os temas que o acervo ainda nao
cobre. Aqui elas viram candidatas, com tres freios contra a fila de curadoria
virar um entulho:

* **cota por rodada** (pela intensidade) e **teto de pendentes**: com muita
  coisa esperando decisao, o radar para de sugerir;
* **so tema sem cobertura**: busca cujo assunto o acervo ja sustenta nao gera
  sugestao;
* **so o que parece artigo**: a pagina e baixada e conferida antes (o tipo que
  o site declara, ou o tamanho do texto corrido) — home, listagem, loja e
  pagina de servico ficam de fora. E o que nao tem jeito: caminho bloqueado,
  plataforma aberta, o proprio site e os concorrentes confirmados.

A busca de fontes da pauta continua existindo, para quando uma pauta precisa
de fonte e nao ha nenhuma: esta aqui enriquece o acervo antes disso.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from django.utils import timezone

from apps.radar.models import ConfiguracaoDoRadar, ResultadoOrganico

logger = logging.getLogger("publibot.radar")

MAXIMO_DE_PENDENTES = 30
TENTATIVAS_POR_SUGESTAO = 3
GUARDAR_RESULTADOS_POR = timezone.timedelta(days=90)


def guardar_resultados(consulta: str, resultados, *, rodada) -> None:
    ResultadoOrganico.objects.bulk_create(
        [
            ResultadoOrganico(
                rodada=rodada,
                consulta=consulta[:500],
                url=item.url[:500],
                titulo=(item.titulo or "")[:500],
                trecho=item.trecho or "",
                posicao=posicao,
            )
            for posicao, item in enumerate(list(resultados)[:10], start=1)
        ]
    )


def _descartavel(url: str, *, proprio: str, concorrentes: set[str]) -> bool:
    from apps.knowledge.fontes_web import _plataforma, bloqueada
    from apps.radar.concorrentes import _CAMINHOS_IGNORADOS

    partes = urlparse(url)
    anfitriao = (partes.hostname or "").lower().removeprefix("www.")
    caminho = partes.path.rstrip("/")
    return (
        not anfitriao
        or not caminho  # a pagina inicial de um site nao e artigo
        or anfitriao == proprio
        or anfitriao in concorrentes
        or _plataforma(anfitriao) is not None
        or bool(_CAMINHOS_IGNORADOS.search(caminho))
        or bloqueada(url)
    )


def _coberta(consulta: str) -> bool:
    from apps.knowledge.models import RetrievalSettings
    from apps.radar.agrupamento import _menor_distancia_ao_acervo, _vetor

    limiar = RetrievalSettings.carregar().max_cosine_distance
    return _menor_distancia_ao_acervo(_vetor(consulta)) <= limiar


def _classificar(url: str):
    from apps.knowledge.web import Classificacao, PaginaIndisponivel, baixar, classificar_pagina

    if urlparse(url).path.lower().endswith(".pdf"):
        return Classificacao(True, "documento PDF")
    try:
        conteudo, url_final, tipo = baixar(url)
    except PaginaIndisponivel as exc:
        return Classificacao(False, str(exc))
    if "pdf" in (tipo or "").lower():
        return Classificacao(True, "documento PDF")
    return classificar_pagina(conteudo, url=url_final)


def sugerir_fontes(rodada, *, cota: int) -> int:
    """Candidatas a fonte das buscas desta rodada. Devolve quantas."""
    from apps.knowledge.fontes_web import _ja_conhecida, aprovar, caminho_de, confiavel
    from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte
    from apps.knowledge.web import TEXTO_MAXIMO
    from apps.radar.concorrentes import _dominio_proprio

    ResultadoOrganico.objects.filter(criado_em__lt=timezone.now() - GUARDAR_RESULTADOS_POR).delete()
    config = ConfiguracaoDoRadar.carregar()
    if not config.fontes_pelo_radar or cota <= 0:
        return 0
    pendentes = CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.PENDENTE).count()
    cota = min(cota, MAXIMO_DE_PENDENTES - pendentes)
    if cota <= 0:
        return 0

    proprio = _dominio_proprio()
    concorrentes = {c["dominio"] for c in config.lista_de_concorrentes}
    resultados = list(ResultadoOrganico.objects.filter(rodada=rodada).order_by("posicao"))
    cobertura: dict[str, bool] = {}
    vistas: set[str] = set()
    criadas, tentativas = 0, 0
    # Na ordem da posicao: o 1o lugar de cada busca antes do 2o de qualquer uma.
    for resultado in resultados:
        if criadas >= cota or tentativas >= cota * TENTATIVAS_POR_SUGESTAO:
            break
        url = resultado.url
        if url in vistas or _ja_conhecida(url):
            continue
        vistas.add(url)
        if _descartavel(url, proprio=proprio, concorrentes=concorrentes):
            continue
        if resultado.consulta not in cobertura:
            cobertura[resultado.consulta] = _coberta(resultado.consulta)
        if cobertura[resultado.consulta]:
            continue
        tentativas += 1
        classificacao = _classificar(url)
        if not classificacao.e_artigo:
            logger.info("Fonte do radar descartada (%s): %s", classificacao.motivo, url)
            continue
        caminho = caminho_de(url)
        candidato = CandidatoDeFonte.objects.create(
            url=url,
            titulo=resultado.titulo[:500],
            trecho=resultado.trecho,
            dominio=(urlparse(url).hostname or "").lower().removeprefix("www.")[:200],
            consulta=resultado.consulta[:500],
            preferido=confiavel(caminho),
            origem=CandidatoDeFonte.Origem.RADAR,
            classificacao=classificacao.motivo[:200],
            texto_extraido=classificacao.texto[:TEXTO_MAXIMO],
        )
        if caminho is not None and caminho.nivel == CaminhoConfiavel.Nivel.APROVAR:
            aprovar(candidato, categoria=caminho.categoria, automatico=True)
        else:
            from apps.knowledge.provisorias import acolher_se_ligado

            acolher_se_ligado(candidato)
        criadas += 1
    return criadas
