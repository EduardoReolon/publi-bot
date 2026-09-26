"""Fontes achadas na web: candidatas, confianca em dois niveis, aprovacao.

A regra que estes testes guardam: a web FORNECE fonte, nao e fonte. Nada do
que a busca acha entra no acervo sem decisao — a nao ser pelo caminho que a
pessoa marcou, de proposito e com aviso, como "aprovar automaticamente".
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.utils import schema_context

from apps.knowledge.fontes_web import (
    CaminhoRecusado,
    buscar_fontes,
    caminho_de,
    conferir_caminho,
    normalizar_caminho,
)
from apps.knowledge.models import CaminhoConfiavel, CandidatoDeFonte, Document, DocumentCategory
from apps.radar.provedores import ItemDeBusca, ResultadoDeBusca
from tests.test_fontes import PAGINA
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar carregaria o modelo real."""


@pytest.fixture
def tenant(tenant_factory):
    t = tenant_factory("fontesweb")
    with schema_context(t.schema_name):
        yield t


def _categoria(natureza="veiculo"):
    from apps.knowledge.perfis import PERFIS

    return DocumentCategory.objects.create(
        name=natureza, slug=natureza, source_class=natureza, **PERFIS[natureza]
    )


def _buscador(monkeypatch, urls_por_consulta: dict, consultas: list | None = None):
    def buscar(consulta, *, finalidade):
        if consultas is not None:
            consultas.append(consulta)
        urls = urls_por_consulta.get(consulta, urls_por_consulta.get("*", []))
        return ResultadoDeBusca(
            provedor="searxng", resultados=[ItemDeBusca(url=u, titulo=u) for u in urls]
        )

    monkeypatch.setattr("apps.radar.provedores.buscar", buscar)


def _pagina(monkeypatch):
    monkeypatch.setattr(
        "apps.knowledge.entradas.baixar", lambda url: (PAGINA.encode(), url, "text/html")
    )


# ---------------------------------------------------------------------------
# Caminho confiavel
# ---------------------------------------------------------------------------
def test_caminho_e_normalizado():
    assert normalizar_caminho("https://www.gov.br/caixa/sinapi/") == "gov.br/caixa/sinapi"
    assert normalizar_caminho("gov.br/caixa") == "gov.br/caixa"


@pytest.mark.parametrize("prefixo", ["youtube.com", "https://www.medium.com/", "reddit.com"])
def test_plataforma_aberta_nao_aceita_confianca_no_dominio_inteiro(prefixo):
    with pytest.raises(CaminhoRecusado, match="qualquer usuario"):
        conferir_caminho(prefixo, CaminhoConfiavel.Nivel.PREFERIR)


def test_canal_ou_subdominio_proprio_sao_aceitos():
    assert conferir_caminho("youtube.com/@engenhariacivil", "preferir") == (
        "youtube.com/@engenhariacivil"
    )
    assert conferir_caminho("https://fulano.blogspot.com", "aprovar") == "fulano.blogspot.com"


def test_wikipedia_pode_ser_preferida_mas_nunca_aprovada_sozinha():
    assert conferir_caminho("pt.wikipedia.org/wiki", "preferir")
    with pytest.raises(CaminhoRecusado, match="editavel"):
        conferir_caminho("pt.wikipedia.org/wiki", "aprovar")


@pytest.mark.django_db
def test_caminho_casa_por_segmento_e_o_mais_especifico_vence(tenant):
    categoria = _categoria()
    geral = CaminhoConfiavel.objects.create(
        prefixo="gov.br/caixa", nivel="preferir", categoria=categoria
    )
    especifico = CaminhoConfiavel.objects.create(
        prefixo="gov.br/caixa/sinapi", nivel="aprovar", categoria=categoria
    )

    assert caminho_de("https://www.gov.br/caixa/sinapi/tabela") == especifico
    assert caminho_de("https://gov.br/caixa/fgts") == geral
    assert caminho_de("https://gov.br/caixapreta") is None


# ---------------------------------------------------------------------------
# Busca de fontes
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_busca_registra_candidatos_e_nao_repete_o_que_ja_viu(tenant, monkeypatch):
    from apps.content.models import Topic

    pauta = Topic.objects.create(title="Como calcular o BDI")
    CandidatoDeFonte.objects.create(url="https://recusada.com/bdi", situacao="recusado")
    _buscador(monkeypatch, {"*": ["https://recusada.com/bdi", "https://nova.com/bdi"]})

    novos = buscar_fontes(pauta)

    assert [c.url for c in novos] == ["https://nova.com/bdi"]
    assert novos[0].situacao == CandidatoDeFonte.Situacao.PENDENTE
    assert not Document.objects.exists()


@pytest.mark.django_db
def test_caminho_preferido_vira_consulta_restrita_primeiro(tenant, monkeypatch):
    from apps.content.models import Topic

    CaminhoConfiavel.objects.create(
        prefixo="gov.br/caixa/sinapi", nivel="preferir", categoria=_categoria()
    )
    pauta = Topic.objects.create(title="Tabela SINAPI", target_keyword="sinapi setembro")
    consultas = []
    _buscador(
        monkeypatch,
        {"site:gov.br/caixa/sinapi sinapi setembro": ["https://gov.br/caixa/sinapi/set"]},
        consultas,
    )

    novos = buscar_fontes(pauta)

    assert consultas[0] == "site:gov.br/caixa/sinapi sinapi setembro"
    assert novos[0].preferido


@pytest.mark.django_db
def test_caminho_de_aprovacao_automatica_entra_curado(tenant, monkeypatch):
    from apps.content.models import Topic
    from apps.knowledge.flows import passo_converter

    categoria = _categoria()
    CaminhoConfiavel.objects.create(
        prefixo="blog.exemplo.com.br", nivel="aprovar", categoria=categoria
    )
    _buscador(monkeypatch, {"*": ["https://blog.exemplo.com.br/bdi"]})
    _pagina(monkeypatch)
    despachos = []
    monkeypatch.setattr(
        "apps.knowledge.tasks.iniciar_ingestao", lambda documento: despachos.append(documento)
    )

    candidato = buscar_fontes(Topic.objects.create(title="BDI"))[0]

    candidato.refresh_from_db()
    assert candidato.situacao == CandidatoDeFonte.Situacao.APROVADO
    documento = candidato.documento
    assert documento.auto_curate and documento.origin == Document.Origin.WEB

    from types import SimpleNamespace

    passo_converter(SimpleNamespace(target_object_id=str(documento.pk)))
    documento.refresh_from_db()
    assert documento.status == Document.Status.CURATED
    assert documento.chunks.exists()


# ---------------------------------------------------------------------------
# Pauta sem cobertura
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_pauta_sem_fonte_fica_aguardando_e_dispara_a_busca(tenant, monkeypatch):
    from apps.content.flows import passo_recuperar_fontes
    from apps.content.models import Topic
    from apps.content.services import SemFontesSuficientes
    from apps.ops.models import GenerationJob
    from apps.radar.models import ConfiguracaoDoRadar

    ConfiguracaoDoRadar.carregar()
    pauta = Topic.objects.create(title="Tema sem nenhuma fonte", status=Topic.Status.APPROVED)
    despachadas = []
    monkeypatch.setattr(
        "apps.knowledge.tasks.buscar_fontes_da_pauta.delay", lambda pk: despachadas.append(pk)
    )
    monkeypatch.setattr("django.db.transaction.on_commit", lambda funcao, *a, **k: funcao())

    job = GenerationJob(target_object_id=pauta.pk, kind=GenerationJob.Kind.PILLAR_ARTICLE)
    with pytest.raises(SemFontesSuficientes, match="Fontes sugeridas"):
        passo_recuperar_fontes(job)

    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.WAITING_SOURCES
    assert despachadas == [str(pauta.pk)]


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_aprovar_e_recusar_pela_tela(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    _pagina(monkeypatch)
    monkeypatch.setattr("apps.knowledge.tasks.iniciar_ingestao", lambda documento: None)
    categoria = DocumentCategory.objects.first()
    aprovar = CandidatoDeFonte.objects.create(url="https://a.com/bdi", titulo="BDI")
    recusar = CandidatoDeFonte.objects.create(url="https://b.com/spam", titulo="Spam")

    assert (
        client.get(reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants")).status_code
        == 200
    )

    client.post(
        reverse("knowledge:decidir_candidato", args=[aprovar.pk], urlconf="core.urls_tenants"),
        {"decisao": "aprovar", "categoria": str(categoria.pk)},
    )
    client.post(
        reverse("knowledge:decidir_candidato", args=[recusar.pk], urlconf="core.urls_tenants"),
        {"decisao": "recusar"},
    )

    aprovar.refresh_from_db()
    recusar.refresh_from_db()
    assert aprovar.situacao == "aprovado" and aprovar.documento is not None
    assert not aprovar.documento.auto_curate
    assert recusar.situacao == "recusado"


@pytest.mark.django_db
def test_aprovar_automaticamente_exige_confirmar_o_aviso(ambiente):  # noqa: F811
    _, _, client = ambiente
    url = reverse("knowledge:caminhos", urlconf="core.urls_tenants")
    categoria = DocumentCategory.objects.first()
    dados = {"prefixo": "gov.br/caixa/sinapi", "nivel": "aprovar", "categoria": str(categoria.pk)}

    client.post(url, dados)
    assert not CaminhoConfiavel.objects.exists()

    client.post(url, {**dados, "confirmo": "1"})
    caminho = CaminhoConfiavel.objects.get()
    assert caminho.nivel == "aprovar"
    assert "100% criado pelo autor" in client.get(url).content.decode()


@pytest.mark.django_db
def test_tela_recusa_plataforma_aberta(ambiente):  # noqa: F811
    _, _, client = ambiente
    categoria = DocumentCategory.objects.first()

    client.post(
        reverse("knowledge:caminhos", urlconf="core.urls_tenants"),
        {"prefixo": "youtube.com", "nivel": "preferir", "categoria": str(categoria.pk)},
    )

    assert not CaminhoConfiavel.objects.exists()
