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
    "abstract_inverted_index,open_access,best_oa_location,primary_location,authorships"
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
    )


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
    return CandidatoDeFonte.objects.filter(situacao=CandidatoDeFonte.Situacao.PENDENTE).count()


def registrar(trabalho: Trabalho, *, consulta: str, origem: str, pauta=None):
    """O trabalho como candidato a fonte. None se ja conhecido ou bloqueado."""
    from apps.knowledge.fontes_web import _ja_conhecida, bloqueada

    url = trabalho.url[:500]
    if not url or _ja_conhecida(url) or bloqueada(trabalho.pagina_url or url):
        return None
    if trabalho.doi and CandidatoDeFonte.objects.filter(doi=trabalho.doi).exists():
        return None
    return CandidatoDeFonte.objects.create(
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
    )


def buscar_para_pauta(pauta, *, limite: int = 5) -> list:
    """Artigos para a pauta sem fonte. Gratuito; o limite e so da fila."""
    consulta = pauta.target_keyword or pauta.title
    novos = []
    for trabalho in buscar_openalex(consulta, quantos=limite * 2):
        if len(novos) >= limite:
            break
        candidato = registrar(
            trabalho, consulta=consulta, origem=CandidatoDeFonte.Origem.OPENALEX, pauta=pauta
        )
        if candidato is not None:
            novos.append(candidato)
    return novos


def sugerir_pelo_radar(rodada, *, cota: int) -> int:
    """Artigos para os temas da rodada que o acervo ainda nao cobre."""
    from apps.radar.fontes import _coberta
    from apps.radar.models import ResultadoOrganico

    cota = min(cota, MAXIMO_DE_PENDENTES - _pendentes())
    if cota <= 0:
        return 0
    consultas = list(
        dict.fromkeys(
            ResultadoOrganico.objects.filter(rodada=rodada)
            .order_by("posicao")
            .values_list("consulta", flat=True)
        )
    )
    criados = 0
    for consulta in consultas:
        if criados >= cota:
            break
        if _coberta(consulta):
            continue
        for trabalho in buscar_openalex(consulta, quantos=2):
            if criados >= cota:
                break
            if registrar(trabalho, consulta=consulta, origem=CandidatoDeFonte.Origem.OPENALEX):
                criados += 1
    return criados


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
    if not resultado.ja_existia:
        _completar_documento(documento, candidato)
        if automatico:
            Document.objects.filter(pk=documento.pk).update(auto_curate=True)
        iniciar_ingestao(documento)
    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.motivo = ""
    candidato.save()
    return candidato


def receber_pdf(candidato, arquivo, *, categoria, por=None) -> Document:
    """O PDF que a pessoa baixou: vira documento e segue para a curadoria."""
    from apps.knowledge.services import ingerir_documento
    from apps.knowledge.tasks import iniciar_ingestao

    resultado = ingerir_documento(arquivo=arquivo, category=categoria, uploaded_by=por)
    documento = resultado.document
    if not resultado.ja_existia:
        _completar_documento(documento, candidato)
        iniciar_ingestao(documento)
    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    return documento
