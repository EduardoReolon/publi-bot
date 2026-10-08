"""Artigos cientificos como fonte: achar, sugerir e trazer o PDF.

Tres entradas, todas terminando em Fontes sugeridas (nada entra sozinho):

* **OpenAlex**, gratuito e ativo: para a pauta sem fonte e para os temas da
  rodada que o acervo nao cobre. Base aberta com centenas de milhoes de
  trabalhos, com resumo, citacoes e o PDF de acesso aberto quando existe.
* **Google Academico, pela DataForSEO**: o bloco "Scholarly articles" que o
  Google mostra em buscas de tema de pesquisa ja vem na pagina de resultados
  que o radar paga. Cada item e completado pelo OpenAlex (DOI, resumo, PDF).
* **O PDF**: aprovar baixa o de acesso aberto (OpenAlex, ou Unpaywall pelo
  DOI). Sem PDF livre, ou com o site recusando o download por robo, o
  candidato fica "aguardando o PDF": a pessoa baixa pelo navegador (ou pela
  biblioteca da instituicao) e envia.

A autoridade do documento sai das citacoes: um artigo aprovado nasce "fonte
forte" (ver `knowledge.services.fonte_forte`) e ganha a vaga reservada.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
from django.utils import timezone

from apps.knowledge.models import CandidatoDeFonte, Document

logger = logging.getLogger("publibot.knowledge")

OPENALEX = "https://api.openalex.org/works"
UNPAYWALL = "https://api.unpaywall.org/v2"
TIMEOUT = 30.0
CAMPOS = (
    "id,doi,display_name,publication_year,cited_by_count,language,type,"
    "abstract_inverted_index,open_access,best_oa_location,primary_location,authorships,"
    "fwci,citation_normalized_percentile,is_retracted,primary_topic"
)
MAXIMO_DE_PENDENTES = 30
SEMELHANCA_DO_TITULO = 0.85


class BaseIndisponivel(RuntimeError):
    """OpenAlex ou Unpaywall nao responderam como deviam."""


@dataclass
class Trabalho:
    titulo: str
    doi: str = ""
    ano: int | None = None
    autores: list[str] = field(default_factory=list)
    citacoes: int = 0
    resumo: str = ""
    pdf_url: str = ""
    pagina_url: str = ""
    revista: str = ""
    metricas: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        """O endereco que vai para a citacao: o DOI, que nao muda."""
        return f"https://doi.org/{self.doi}" if self.doi else self.pagina_url


# ---------------------------------------------------------------------------
# OpenAlex e Unpaywall
# ---------------------------------------------------------------------------
def _contas():
    from apps.radar.models import ContasExternas

    return ContasExternas.carregar()


def _parametros_da_conta() -> dict:
    from apps.inference.security import decifrar

    contas = _contas()
    parametros = {}
    if contas.tem_openalex:
        parametros["api_key"] = decifrar(contas.openalex_chave_ciphertext)
    if contas.email_para_bases_academicas:
        parametros["mailto"] = contas.email_para_bases_academicas
    return parametros


def _registrar(provedor: str, endpoint: str, consulta: str, *, itens=0, erro=""):
    from apps.radar import custos
    from apps.radar.models import ChamadaExterna

    custos.registrar(
        provedor=provedor,
        endpoint=endpoint,
        finalidade=ChamadaExterna.Finalidade.FONTES,
        consulta=consulta,
        itens=itens,
        sucesso=not erro,
        erro=erro,
    )


def _resumo(invertido: dict | None) -> str:
    """O OpenAlex guarda o resumo como indice invertido (palavra -> posicoes)."""
    if not invertido:
        return ""
    posicoes = {p: palavra for palavra, lista in invertido.items() for p in lista}
    return " ".join(posicoes[i] for i in sorted(posicoes))


def _doi(bruto: str) -> str:
    return (bruto or "").removeprefix("https://doi.org/").strip()


def ler_trabalho(dados: dict) -> Trabalho:
    melhor = dados.get("best_oa_location") or {}
    principal = dados.get("primary_location") or {}
    acesso = dados.get("open_access") or {}
    pdf = melhor.get("pdf_url") or ""
    if not pdf and (acesso.get("oa_url") or "").lower().endswith(".pdf"):
        pdf = acesso["oa_url"]
    return Trabalho(
        titulo=(dados.get("display_name") or "").strip(),
        doi=_doi(dados.get("doi") or ""),
        ano=dados.get("publication_year"),
        autores=[
            (a.get("author") or {}).get("display_name", "")
            for a in (dados.get("authorships") or [])[:6]
            if (a.get("author") or {}).get("display_name")
        ],
        citacoes=int(dados.get("cited_by_count") or 0),
        resumo=_resumo(dados.get("abstract_inverted_index")),
        pdf_url=pdf,
        pagina_url=melhor.get("landing_page_url") or principal.get("landing_page_url") or "",
        revista=((principal.get("source") or {}).get("display_name") or ""),
        metricas=_metricas(dados),
    )


def _metricas(dados: dict) -> dict:
    """Reputacao do artigo, para a curadoria (e para nunca citar retratado).

    `fwci`: citacoes comparadas com artigos da mesma area e ano (1 = media).
    `percentil`: posicao nas citacoes entre os da mesma area e ano.
    """
    percentil = dados.get("citation_normalized_percentile") or {}
    topico = dados.get("primary_topic") or {}
    metricas = {
        "fwci": dados.get("fwci"),
        "percentil": percentil.get("value"),
        "top_10_por_cento": percentil.get("is_in_top_10_percent"),
        "retratado": dados.get("is_retracted"),
        "tipo": dados.get("type"),
        "idioma": dados.get("language"),
        "acesso_aberto": (dados.get("open_access") or {}).get("oa_status"),
        "topico": topico.get("display_name"),
        "area": (topico.get("field") or {}).get("display_name"),
    }
    return {k: v for k, v in metricas.items() if v not in (None, "")}


def buscar_openalex(consulta: str, *, quantos: int = 5, idiomas: str = "pt|en") -> list[Trabalho]:
    """Artigos e revisoes com resumo, mais relevantes primeiro."""
    from apps.radar.models import ChamadaExterna

    parametros = {
        "search": consulta,
        "filter": f"type:article|review,has_abstract:true,language:{idiomas}",
        "per_page": max(1, min(quantos, 25)),
        "select": CAMPOS,
        **_parametros_da_conta(),
    }
    try:
        resposta = httpx.get(OPENALEX, params=parametros, timeout=TIMEOUT)
        resposta.raise_for_status()
        itens = resposta.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        _registrar(ChamadaExterna.Provedor.OPENALEX, "works", consulta, erro=str(exc)[:500])
        raise BaseIndisponivel(f"OpenAlex nao respondeu: {exc}") from exc
    _registrar(ChamadaExterna.Provedor.OPENALEX, "works", consulta, itens=len(itens))
    return [t for t in (ler_trabalho(i) for i in itens) if t.titulo]


def por_doi(doi: str) -> Trabalho | None:
    """O trabalho do OpenAlex pelo DOI, ou None (DOI desconhecido ou base fora).

    Titulo, autores e ano de registro da editora: melhores que qualquer
    heuristica sobre a primeira pagina, que pode ser uma capa de download.
    """
    from apps.radar.models import ChamadaExterna

    if not doi:
        return None
    try:
        resposta = httpx.get(
            f"{OPENALEX}/https://doi.org/{doi}",
            params={"select": CAMPOS, **_parametros_da_conta()},
            timeout=TIMEOUT,
        )
        if resposta.status_code == 404:
            _registrar(ChamadaExterna.Provedor.OPENALEX, "works/doi", doi)
            return None
        resposta.raise_for_status()
        trabalho = ler_trabalho(resposta.json())
    except (httpx.HTTPError, ValueError) as exc:
        _registrar(ChamadaExterna.Provedor.OPENALEX, "works/doi", doi, erro=str(exc)[:500])
        return None
    _registrar(ChamadaExterna.Provedor.OPENALEX, "works/doi", doi, itens=1)
    return trabalho if trabalho.titulo else None


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9 ]", "", sem_acento.lower()).strip()


def por_titulo(titulo: str) -> Trabalho | None:
    """O trabalho do OpenAlex com este titulo, se a semelhanca for alta."""
    alvo = _normalizar(titulo)
    for trabalho in buscar_openalex(titulo, quantos=3, idiomas="pt|en|es"):
        razao = difflib.SequenceMatcher(None, alvo, _normalizar(trabalho.titulo)).ratio()
        if razao >= SEMELHANCA_DO_TITULO:
            return trabalho
    return None


def consultar_unpaywall(doi: str) -> str:
    """O PDF de acesso aberto pelo DOI, ou "". Levanta `BaseIndisponivel` se falhar."""
    from apps.radar.models import ChamadaExterna

    email = _contas().email_para_bases_academicas
    if not doi or not email:
        return ""
    try:
        resposta = httpx.get(f"{UNPAYWALL}/{doi}", params={"email": email}, timeout=TIMEOUT)
        if resposta.status_code == 404:
            return ""
        resposta.raise_for_status()
        melhor = resposta.json().get("best_oa_location") or {}
    except (httpx.HTTPError, ValueError) as exc:
        _registrar(ChamadaExterna.Provedor.UNPAYWALL, "doi", doi, erro=str(exc)[:500])
        raise BaseIndisponivel(f"Unpaywall nao respondeu: {exc}") from exc
    _registrar(ChamadaExterna.Provedor.UNPAYWALL, "doi", doi, itens=1)
    return melhor.get("url_for_pdf") or ""


def pdf_pelo_unpaywall(doi: str) -> str:
    """O PDF legal de acesso aberto pelo DOI, ou "" (exige e-mail de contato).

    Falha vira "": o artigo so fica aguardando o PDF.
    """
    try:
        return consultar_unpaywall(doi)
    except BaseIndisponivel:
        return ""


def autoridade(citacoes: int) -> int:
    """70 (fonte forte) mais ate 25 pelas citacoes: 10 -> 78, 100 -> 86, 1000+ -> 94."""
    return min(95, 70 + int(math.log10(max(citacoes, 0) + 1) * 8))


# ---------------------------------------------------------------------------
# Candidatos
# ---------------------------------------------------------------------------
def _pendentes() -> int:
    """Artigos esperando curadoria. So artigos: paginas e videos pendentes nao
    podem travar a busca de artigos (e travavam, calados)."""
    return CandidatoDeFonte.objects.filter(
        situacao=CandidatoDeFonte.Situacao.PENDENTE, tipo=CandidatoDeFonte.Tipo.ARTIGO
    ).count()


def registrar(trabalho: Trabalho, *, consulta: str, origem: str, pauta=None):
    """O trabalho como candidato a fonte. None se ja conhecido ou bloqueado."""
    from apps.knowledge.fontes_web import _ja_conhecida, bloqueada, reaproveitar

    url = trabalho.url[:500]
    if url and (reaproveitado := reaproveitar(url, pauta)) is not None:
        return reaproveitado
    if not url or _ja_conhecida(url) or bloqueada(trabalho.pagina_url or url):
        return None
    if trabalho.metricas.get("retratado"):
        return None  # artigo retratado nunca vira fonte
    if trabalho.doi and CandidatoDeFonte.objects.filter(doi=trabalho.doi).exists():
        return None
    candidato = CandidatoDeFonte.objects.create(
        url=url,
        tipo=CandidatoDeFonte.Tipo.ARTIGO,
        titulo=trabalho.titulo[:500],
        trecho=trabalho.resumo,
        doi=trabalho.doi[:200],
        autores=", ".join(trabalho.autores)[:300],
        ano=trabalho.ano,
        revista=trabalho.revista[:300],
        citacoes=trabalho.citacoes,
        pdf_url=trabalho.pdf_url[:500],
        dominio=(urlparse(trabalho.pagina_url or url).hostname or "")[:200],
        consulta=consulta[:500],
        pauta=pauta,
        origem=origem,
        metricas=trabalho.metricas,
    )
    # O resumo entra no indice como fonte provisoria, se houver worker.
    from apps.knowledge.provisorias import acolher_se_ligado

    acolher_se_ligado(candidato)
    return candidato


def buscar_para_pauta(pauta, *, limite: int = 5, consulta: str = "") -> list:
    """Artigos para a pauta sem fonte (fluxo A). Por sentido: a busca semantica
    do OpenAlex com a pauta inteira (titulo, palavra-chave e orientacao) — no
    teste com pautas reais, acertou o tema onde a busca por palavras trouxe lixo
    ou nada. A busca por palavras fica de reserva, se a semantica falhar."""
    from apps.knowledge.pesquisa import PesquisaIndisponivel, busca_semantica
    from apps.knowledge.referencias import consulta_da_pauta

    texto = consulta or consulta_da_pauta(pauta)
    try:
        trabalhos = [ler_trabalho(d) for d in busca_semantica(texto, quantos=limite * 3)]
        trabalhos = [t for t in trabalhos if t.titulo]
    except PesquisaIndisponivel as exc:
        logger.info("Pauta %s: busca semantica falhou (%s); vai por palavras.", pauta.pk, exc)
        trabalhos = buscar_openalex(
            consulta or pauta.target_keyword or pauta.title, quantos=limite * 2
        )
    novos = []
    for trabalho in trabalhos:
        if len(novos) >= limite:
            break
        candidato = registrar(
            trabalho, consulta=texto[:500], origem=CandidatoDeFonte.Origem.OPENALEX, pauta=pauta
        )
        if candidato is not None:
            novos.append(candidato)
    return novos


SEMENTES_POR_RODADA = 3
POR_SEMENTE = 3
SEMENTES_POR_BUSCA_MANUAL = 15


def buscar_por_sementes(
    sementes: list[str], *, cota: int | None = None, por_semente: int = POR_SEMENTE
) -> list[dict]:
    """Busca cada semente cientifica no OpenAlex e registra os artigos novos.

    Devolve, por semente, quantos o OpenAlex achou e quantos entraram na fila
    (os ja conhecidos ficam de fora): e o que diz se a semente presta.
    """
    relatorio, criados = [], 0
    for semente in sementes:
        if (cota is not None and criados >= cota) or _pendentes() >= MAXIMO_DE_PENDENTES:
            break
        trabalhos = buscar_openalex(semente, quantos=por_semente * 2)
        novos = 0
        for trabalho in trabalhos:
            if novos >= por_semente or (cota is not None and criados >= cota):
                break
            if registrar(trabalho, consulta=semente, origem=CandidatoDeFonte.Origem.OPENALEX):
                novos += 1
                criados += 1
        relatorio.append({"semente": semente, "achados": len(trabalhos), "novos": novos})
    return relatorio


def sugerir_pelo_radar(rodada, *, cota: int) -> int:
    """Artigos pelas sementes cientificas da vez.

    Nao pelas buscas do Google da rodada: artigo academico fala de outro jeito
    ("customer retention strategies", e nao "cliente sumiu depois da primeira
    compra"), e a frase comercial trazia pouco ou lixo. Sem sementes
    cientificas, nada e buscado.
    """
    from apps.radar.coleta import _da_vez
    from apps.radar.models import ConfiguracaoDoRadar

    lista = ConfiguracaoDoRadar.carregar().lista_de_sementes_cientificas
    cota = min(cota, MAXIMO_DE_PENDENTES - _pendentes())
    if not lista or cota <= 0:
        return 0
    sementes = _da_vez(lista, SEMENTES_POR_RODADA, chave="sementes_cientificas")
    relatorio = buscar_por_sementes(sementes, cota=cota)
    # Registrado na rodada para a rotacao: a proxima pega as seguintes.
    rodada.resumo["sementes_cientificas"] = [linha["semente"] for linha in relatorio]
    return sum(linha["novos"] for linha in relatorio)


ROTULOS_CIENTIFICOS = ["CIENTIFICAS", "COMENTARIOS"]


def pedido_das_sementes_cientificas() -> str:
    """O pedido para uma IA traduzir o negocio para o vocabulario academico."""
    from apps.editorial.models import perfil_do_negocio
    from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda

    config = ConfiguracaoDoRadar.carregar()
    perfil = perfil_do_negocio()
    partes = [
        """\
Quero achar ARTIGOS CIENTIFICOS que sustentem os textos do meu site. Os artigos
academicos quase nunca usam as palavras de quem vende ou de quem compra: um
metodo para reter clientes aparece como "customer retention" ou "churn
prediction", e nao como "cliente sumiu". Por isso preciso de sementes proprias
para a busca academica (OpenAlex), separadas das sementes do Google.

Antes de responder, pesquise: pense em como a literatura chama cada problema
abaixo — nomes de metodos, estrategias, modelos, teorias e construtos, e as
areas em que isso e estudado. Dê prioridade aos temas com mais busca e aos
marcados como bons. Evite termos genericos ("gestao", "tecnologia") e nomes de
produto.

Responda SO nos blocos abaixo e termine com FIM:

CIENTIFICAS:
- de 10 a 20 termos de busca academica, de 2 a 5 palavras cada, como
  apareceriam no titulo ou no resumo de um artigo. A maioria em ingles (onde
  esta a maior parte da literatura), alguns em portugues. Os mais importantes
  primeiro.
COMENTARIOS:
curto: areas de pesquisa e autores ou revistas de referencia, se souber.
FIM
""",
        "## O negocio",
    ]
    if perfil is not None:
        partes += [
            f"Tema: {perfil.tema or '-'}",
            f"Publico: {perfil.publico or '-'}",
            f"Oferta: {perfil.oferta or '-'}",
        ]
        frentes = [f.strip() for f in perfil.frentes.splitlines() if f.strip()]
        if frentes:
            partes.append("Outras frentes: " + "; ".join(frentes))
    partes.append("\n## Sementes do Google (comerciais)")
    partes += [f"- {s}" for s in config.lista_de_sementes] or ["(nenhuma)"]
    partes.append("\n## Dores do publico")
    partes += [f"- {d}" for d in config.lista_de_dores] or ["(nenhuma)"]
    partes.append("\n## Temas que o radar achou (buscas/mes; os bons primeiro)")
    temas = GrupoDeDemanda.objects.exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO).exclude(
        avaliacao_ia="ruim"
    )
    ordenados = sorted(
        temas.order_by("-nota")[:60],
        key=lambda g: (g.avaliacao_ia != "boa", -(g.volume_total or 0)),
    )[:20]
    partes += [
        f"- {g.rotulo} ({g.volume_total or '—'})" + (" [bom]" if g.avaliacao_ia == "boa" else "")
        for g in ordenados
    ] or ["(nenhum ainda: rode o radar antes, para a IA saber onde focar)"]
    atuais = config.lista_de_sementes_cientificas
    if atuais:
        partes.append("\n## Sementes cientificas que ja uso (mantenha as boas)")
        partes += [f"- {s}" for s in atuais]
    return "\n".join(partes)


def ler_sementes_cientificas(resposta: str) -> list[str]:
    from core.resposta_ia import itens, ler_blocos

    vistos, saida = set(), []
    for termo in itens(ler_blocos(resposta, ROTULOS_CIENTIFICOS).get("CIENTIFICAS", "")):
        if termo.lower() not in vistos:
            vistos.add(termo.lower())
            saida.append(termo[:200])
    return saida


def _url_do_scholar(url: str) -> str:
    """O Scholar embrulha o link: scholar_url?url=<o artigo>&..."""
    alvo = parse_qs(urlparse(url).query).get("url")
    return alvo[0] if alvo else url


def registrar_do_google(itens: list[dict], *, consulta: str) -> int:
    """O bloco "Google Academico" de uma busca ja paga, completado pelo OpenAlex."""
    criados = 0
    for item in itens[:3]:
        if _pendentes() >= MAXIMO_DE_PENDENTES:
            break
        try:
            trabalho = por_titulo(item["titulo"])
        except BaseIndisponivel:
            trabalho = None
        if trabalho is None:
            # Sem o OpenAlex, fica o que o Google mostrou: titulo, autor e o link.
            destino = _url_do_scholar(item.get("url") or "")
            if not destino.startswith("http"):
                continue
            trabalho = Trabalho(
                titulo=item["titulo"],
                autores=[item["autor"]] if item.get("autor") else [],
                pagina_url=destino,
                pdf_url=destino if destino.lower().endswith(".pdf") else "",
            )
        if registrar(trabalho, consulta=consulta, origem=CandidatoDeFonte.Origem.SCHOLAR):
            criados += 1
    return criados


# ---------------------------------------------------------------------------
# Aprovar: trazer o PDF, ou esperar a pessoa
# ---------------------------------------------------------------------------
def _completar_documento(documento: Document, candidato) -> None:
    documento.title = candidato.titulo[:500]
    documento.authors = candidato.autores[:300]
    documento.year = candidato.ano
    documento.source_url = candidato.url[:500]
    documento.authority_score = autoridade(candidato.citacoes or 0)
    documento.save()


def _aguardar_pdf(candidato, *, por, motivo: str):
    candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    candidato.motivo = motivo[:2000]
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    return candidato


def aprovar_artigo(candidato, *, categoria, por=None, automatico: bool = False):
    """Baixa o PDF de acesso aberto e manda para o acervo; sem ele, espera."""
    from apps.knowledge.entradas import ingerir_url
    from apps.knowledge.tasks import iniciar_ingestao
    from apps.knowledge.web import PaginaIndisponivel

    pdf = candidato.pdf_url or pdf_pelo_unpaywall(candidato.doi)
    onde = candidato.url
    if not pdf:
        return _aguardar_pdf(
            candidato,
            por=por,
            motivo=(
                f"Sem PDF de acesso aberto. Abra {onde} (pelo navegador, ou pela "
                "biblioteca da sua instituicao) e envie o PDF aqui."
            ),
        )
    try:
        resultado = ingerir_url(
            pdf, category=categoria, uploaded_by=por, origin=Document.Origin.WEB, iniciar=False
        )
    except PaginaIndisponivel as exc:
        return _aguardar_pdf(
            candidato,
            por=por,
            motivo=(
                f"O site nao deixou baixar o PDF ({exc}). Abra {pdf} no navegador "
                "e envie o arquivo aqui."
            ),
        )
    documento = resultado.document
    if not documento.original_file.name.lower().endswith(".pdf"):
        # Veio uma pagina (captcha, "aceite os cookies"), e nao o PDF.
        if not resultado.ja_existia:
            documento.delete()
        return _aguardar_pdf(
            candidato,
            por=por,
            motivo=f"O endereco do PDF devolveu uma pagina. Abra {pdf} e envie o arquivo.",
        )
    da_pesquisa = _guardar_o_pdf(candidato, documento)
    if not resultado.ja_existia:
        _completar_documento(documento, candidato)
        if automatico and not da_pesquisa:
            Document.objects.filter(pk=documento.pk).update(auto_curate=True)
        iniciar_ingestao(documento)
    elif da_pesquisa and documento.markdown_full:
        from apps.knowledge.pesquisa import extrair_do_pdf

        extrair_do_pdf(documento)
    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    if not da_pesquisa:
        candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.motivo = ""
    candidato.save()
    return candidato


def _guardar_o_pdf(candidato, documento) -> bool:
    """Onde o PDF fica. Artigo da pesquisa (fluxo B): o resumo continua sendo a
    fonte e o PDF vai para `documento_completo` (dele saem so os trechos
    pedidos). Fora da pesquisa: o PDF toma o lugar do resumo. Devolve se e da
    pesquisa."""
    from apps.knowledge.pesquisa import em_pesquisa
    from apps.knowledge.provisorias import descartar_resumo

    if em_pesquisa(candidato):
        candidato.documento_completo = documento
        CandidatoDeFonte.objects.filter(pk=candidato.pk).update(documento_completo=documento)
        return True
    descartar_resumo(candidato)
    return False


def receber_pdf(candidato, arquivo, *, categoria, por=None) -> Document:
    """O PDF que a pessoa baixou: vira documento e segue para a curadoria (ou,
    no artigo da pesquisa, para a extracao do que foi pedido)."""
    from apps.knowledge.services import ingerir_documento
    from apps.knowledge.tasks import iniciar_ingestao

    resultado = ingerir_documento(arquivo=arquivo, category=categoria, uploaded_by=por)
    documento = resultado.document
    da_pesquisa = _guardar_o_pdf(candidato, documento)
    if not resultado.ja_existia:
        _completar_documento(documento, candidato)
        iniciar_ingestao(documento)
    elif da_pesquisa and documento.markdown_full:
        from apps.knowledge.pesquisa import extrair_do_pdf

        extrair_do_pdf(documento)
    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    if not da_pesquisa:
        candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    return documento
