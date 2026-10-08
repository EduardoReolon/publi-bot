"""Referencias da pauta: a base primeiro, a busca so para a falta, e tudo dito na tela."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.utils import schema_context

from apps.content.models import Topic
from apps.knowledge import referencias
from apps.knowledge.fontes_web import buscar_fontes
from apps.knowledge.models import CandidatoDeFonte
from apps.radar.provedores import ResultadoDeBusca
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """Indexar carregaria o modelo real."""


@pytest.fixture
def tenant(tenant_factory):
    t = tenant_factory("referencias")
    with schema_context(t.schema_name):
        yield t


@pytest.fixture
def sem_rede(monkeypatch):
    """Nada sai para a rede: a web e o OpenAlex sao registrados, nao chamados."""
    chamadas = {"web": [], "openalex": []}

    def buscar(consulta, *, finalidade):
        chamadas["web"].append(consulta)
        return ResultadoDeBusca(provedor="searxng", resultados=[])

    def openalex(pauta, *, limite, consulta=""):
        chamadas["openalex"].append((consulta, limite))
        return []

    monkeypatch.setattr("apps.radar.provedores.buscar", buscar)
    monkeypatch.setattr("apps.knowledge.academicos.buscar_para_pauta", openalex)
    return chamadas


def _acervo(monkeypatch, artigos=0, suficiente=False):
    monkeypatch.setattr(
        referencias,
        "no_acervo",
        lambda pauta: {
            "pagina": 2,
            "video": 0,
            "artigo": artigos,
            "documento": 0,
            "suficiente": suficiente,
            "por_curar": [],
            "em": "2026-09-30T10:00:00",
        },
    )


def _artigo_pendente(n, pauta=None):
    return CandidatoDeFonte.objects.create(
        url=f"https://doi.org/10.1/{n}",
        tipo=CandidatoDeFonte.Tipo.ARTIGO,
        titulo=f"Estudo {n}",
        pauta=pauta,
    )


def test_com_artigos_suficientes_na_base_nao_busca_no_openalex(tenant, monkeypatch, sem_rede):
    pauta = Topic.objects.create(title="Retencao de clientes", target_keyword="retencao")
    _acervo(monkeypatch, artigos=4)
    _artigo_pendente(1, pauta)

    buscar_fontes(pauta)
    pauta.refresh_from_db()
    assert sem_rede["openalex"] == []
    assert pauta.busca_de_fontes["artigos"]["buscou"] is False
    assert pauta.busca_de_fontes["artigos"]["da_base"] == 5
    assert pauta.busca_de_fontes["paginas"]["em"] and sem_rede["web"]


def test_busca_so_a_falta_e_liga_os_das_sementes(tenant, monkeypatch, sem_rede):
    pauta = Topic.objects.create(title="Retencao de clientes", target_keyword="retencao")
    _acervo(monkeypatch, artigos=0)
    solto = _artigo_pendente(9)
    monkeypatch.setattr(
        referencias,
        "ligar_artigos_da_base",
        lambda p: CandidatoDeFonte.objects.filter(pk=solto.pk).update(pauta=p),
    )

    buscar_fontes(pauta)
    pauta.refresh_from_db()
    # Um ja achado pelas sementes passou a ser da pauta: faltam 4.
    assert sem_rede["openalex"] == [("retencao", 4)]
    assert pauta.busca_de_fontes["artigos"]["buscou"] is True


def test_ignorar_a_falta_nao_busca_artigos(tenant, monkeypatch, sem_rede):
    pauta = Topic.objects.create(title="Retencao", busca_de_fontes={"artigos": {"ignorado": True}})
    _acervo(monkeypatch, artigos=0)
    buscar_fontes(pauta)
    assert sem_rede["openalex"] == []


def test_buscar_de_novo_usa_palavras_ainda_nao_usadas(tenant, monkeypatch, sem_rede):
    from apps.radar.models import GrupoDeDemanda, SinalDeDemanda

    pauta = Topic.objects.create(title="Retencao de clientes", target_keyword="retencao")
    grupo = GrupoDeDemanda.objects.create(rotulo="retencao", pauta=pauta)
    SinalDeDemanda.objects.create(texto="cliente nao volta a comprar", fonte="paa", grupo=grupo)
    _acervo(monkeypatch, artigos=0)
    buscar_fontes(pauta)
    sem_rede["web"].clear()
    sem_rede["openalex"].clear()

    pauta.refresh_from_db()
    buscar_fontes(pauta, variar=True)
    # Sem modelo configurado: as frases do tema do radar, nao repetidas.
    assert "cliente nao volta a comprar" in sem_rede["web"]
    assert "retencao" not in sem_rede["web"]
    pauta.refresh_from_db()
    assert pauta.busca_de_fontes["paginas"]["variada"] is True


def test_conferir_libera_a_pauta_que_esperava(tenant, monkeypatch):
    pauta = Topic.objects.create(title="Retencao", status=Topic.Status.WAITING_SOURCES)
    _acervo(monkeypatch, suficiente=True)
    assert referencias.conferir_as_que_esperam() == 1
    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.APPROVED
    assert pauta.busca_de_fontes["acervo"]["pagina"] == 2


def test_painel_na_tela_de_pautas(ambiente, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    pauta = Topic.objects.create(
        title="Retencao",
        status=Topic.Status.WAITING_SOURCES,
        busca_de_fontes={
            "acervo": {"pagina": 2, "video": 1, "artigo": 0, "documento": 0, "suficiente": False},
            "paginas": {"em": "2026-09-30T10:00:00", "novos": 0},
            "artigos": {"em": "2026-09-30T10:00:00", "buscou": True, "novos": 2},
        },
    )
    _artigo_pendente(1, pauta)
    html = client.get(
        reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "2 paginas de veiculos e sites" in html and "1 videos" in html
    assert "ainda nao sustenta" in html and "nada novo encontrado" in html
    assert "1 sugestao(oes) para decidir" in html and "Verificar referencias de novo" in html
    assert "Decidir as fontes sugeridas" in html
    assert "Ignorar falta de artigos" in html

    _acervo(monkeypatch, artigos=3, suficiente=True)
    client.post(
        reverse("content:conferir_referencias", args=[pauta.pk], urlconf="core.urls_tenants")
    )
    pauta.refresh_from_db()
    assert pauta.status == Topic.Status.APPROVED

    client.post(
        reverse("content:ignorar_falta_de_artigos", args=[pauta.pk], urlconf="core.urls_tenants")
    )
    pauta.refresh_from_db()
    assert pauta.busca_de_fontes["artigos"]["ignorado"] is True


def test_artigo_sem_pdf_aprovado_diz_onde_ficou_e_aceita_pdf_junto(ambiente, monkeypatch):  # noqa: F811
    from apps.knowledge.models import DocumentCategory

    _, _, client = ambiente
    monkeypatch.setattr("apps.knowledge.academicos.pdf_pelo_unpaywall", lambda doi: "")
    categoria = DocumentCategory.objects.create(name="Cientifico", slug="cientifico")
    sem_pdf = _artigo_pendente(1)
    url = reverse("knowledge:decidir_candidato", args=[sem_pdf.pk], urlconf="core.urls_tenants")

    resposta = client.post(url, {"decisao": "aprovar", "categoria": categoria.pk}, follow=True)
    sem_pdf.refresh_from_db()
    assert sem_pdf.situacao == CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    html = resposta.content.decode()
    assert "ainda nao foi para o acervo" in html and 'id="aguardando-pdf"' in html
    # A secao dos que esperam o PDF vem antes das sugestoes.
    assert html.index('id="aguardando-pdf"') < html.index("Sugestoes esperando decisao")

    enviados = []
    monkeypatch.setattr(
        "apps.knowledge.academicos.receber_pdf",
        lambda candidato, arquivo, **kw: enviados.append(candidato.pk),
    )
    from django.core.files.uploadedfile import SimpleUploadedFile

    com_pdf = _artigo_pendente(2)
    client.post(
        reverse("knowledge:decidir_candidato", args=[com_pdf.pk], urlconf="core.urls_tenants"),
        {
            "decisao": "aprovar",
            "categoria": categoria.pk,
            "pdf": SimpleUploadedFile("a.pdf", b"%PDF-1.4", content_type="application/pdf"),
        },
    )
    assert enviados == [com_pdf.pk]


def test_desfazer_a_aprovacao_de_quem_espera_o_pdf(ambiente):  # noqa: F811
    _, _, client = ambiente
    candidato = _artigo_pendente(1)
    candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    candidato.motivo = "Sem PDF de acesso aberto."
    candidato.save()
    url = reverse("knowledge:voltar_a_sugestao", args=[candidato.pk], urlconf="core.urls_tenants")

    html = client.get(
        reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants")
    ).content.decode()
    assert "Desfazer aprovacao" in html

    client.post(url)
    candidato.refresh_from_db()
    assert candidato.situacao == CandidatoDeFonte.Situacao.PENDENTE and not candidato.motivo

    candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_PDF
    candidato.save()
    client.post(url, {"recusar": "1"})
    candidato.refresh_from_db()
    assert candidato.situacao == CandidatoDeFonte.Situacao.RECUSADO


def test_recusado_por_engano_volta_para_as_sugestoes(ambiente):  # noqa: F811
    import datetime

    from django.utils import timezone

    _, _, client = ambiente
    antigo, novo = _artigo_pendente(1), _artigo_pendente(2)
    agora = timezone.now()
    for candidato, quando in ((antigo, agora - datetime.timedelta(days=2)), (novo, agora)):
        candidato.situacao = CandidatoDeFonte.Situacao.RECUSADO
        candidato.decidido_em = quando
        candidato.save()

    lista = reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants")
    html = client.get(lista + "?recusados=1").content.decode()
    assert html.index("Estudo 2") < html.index("Estudo 1")
    assert "Voltar para as sugestoes" in html

    client.post(reverse("knowledge:voltar_a_sugestao", args=[novo.pk], urlconf="core.urls_tenants"))
    novo.refresh_from_db()
    assert novo.situacao == CandidatoDeFonte.Situacao.PENDENTE and novo.decidido_em is None


def test_curadoria_na_fila_mostra_processando(ambiente, monkeypatch, settings):  # noqa: F811
    from apps.knowledge import tasks
    from apps.knowledge.models import Document

    _, _, client = ambiente
    settings.PUBLIBOT_INDEXAR_NA_HORA = False
    from tests.test_interface import _documento_curado

    documento = _documento_curado()
    Document.objects.filter(pk=documento.pk).update(status=Document.Status.PENDING_CURATION)
    despachados = []
    monkeypatch.setattr(tasks.indexar_documento, "delay", lambda pk: despachados.append(pk))

    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda funcao: funcao())
    tasks.pedir_indexacao(documento, blocos={0, 2}, concluir=True, por=None)
    documento.refresh_from_db()
    assert documento.indexacao_pedida["blocos"] == [0, 2] and despachados == [str(documento.pk)]

    html = client.get(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Processando" in html and "disabled" in html
    html = client.get(reverse("knowledge:documentos", urlconf="core.urls_tenants")).content.decode()
    assert "processando" in html


CAPA_DA_SAGE = """http://jsr.sagepub.com/
Journal of Service Research
The online version of this article can be found at:
DOI: 10.1177/109467050032002
Published by:
http://www.sagepublications.com
at St Petersburg State University on November 15, 2013 jsr.sagepub.com Downloaded from
"""


def test_capa_de_download_nao_engana_o_cabecalho_com_doi(monkeypatch):
    from apps.knowledge import academicos
    from apps.knowledge.flows import completar_pelo_doi, sugerir_metadados

    sugestoes = sugerir_metadados(CAPA_DA_SAGE, e_markdown=False)
    assert sugestoes["doi"] == "10.1177/109467050032002"
    monkeypatch.setattr(
        academicos,
        "por_doi",
        lambda doi: academicos.Trabalho(
            titulo="An Empirical Investigation of Customer Satisfaction after Service "
            "Failure and Recovery",
            doi=doi,
            ano=2000,
            autores=["Michael A. McCollough", "Leonard L. Berry", "Manjit S. Yadav"],
        ),
    )
    certo = completar_pelo_doi(sugestoes)
    assert certo["title"].startswith("An Empirical Investigation")
    assert certo["authors"].startswith("Michael A. McCollough") and certo["year"] == 2000
    assert certo["fonte"] == "openalex"


def test_gerar_busca_videos_uma_vez_e_depois_segue(
    ambiente,  # noqa: F811
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from apps.content import tasks
    from apps.knowledge import referencias as ref

    fila = []
    monkeypatch.setattr(tasks.gerar_a_depois_dos_videos, "delay", fila.append)

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Retencao", status=Topic.Status.APPROVED)
    _acervo(monkeypatch, artigos=5, suficiente=True)
    monkeypatch.setattr(ref, "youtube_ligado", lambda: True)
    achados = []

    def buscar(p, *, falta):
        achados.append(falta)
        return [object(), object()]

    monkeypatch.setattr("apps.radar.youtube.buscar_para_pauta", buscar)
    gerados = []
    monkeypatch.setattr(
        "apps.content.tasks.gerar_artigo",
        lambda p, **kw: gerados.append(p) or type("J", (), {"pk": "x" * 8})(),
    )
    url = reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants")

    # O clique so dispara: a busca no YouTube vai para a fila.
    with django_capture_on_commit_callbacks(execute=True):
        resposta = client.post(url, follow=True)
    assert "procurando videos" in resposta.content.decode() and not achados
    assert fila == [str(pauta.pk)]
    assert tasks.gerar_a_depois_dos_videos(str(pauta.pk)) is False  # achou: para
    assert achados == [3] and not gerados
    # Segunda vez: segue sem buscar de novo.
    client.post(url)
    assert gerados and achados == [3]


def test_gerar_sem_recarregar_responde_json_sem_mensagem(
    ambiente,  # noqa: F811
    monkeypatch,
):
    from apps.content import fluxos

    _, _, client = ambiente
    pauta = Topic.objects.create(title="Retencao", status=Topic.Status.APPROVED)
    monkeypatch.setattr(fluxos, "disparar", lambda p, fluxo: ("success", "A: geracao iniciada."))
    url = reverse("content:gerar", args=[pauta.pk], urlconf="core.urls_tenants")
    resposta = client.post(url, {"fluxo": "ligados"}, HTTP_X_EM_SEGUNDO_PLANO="1")
    assert resposta.status_code == 200 and "geracao iniciada" in resposta.json()["detalhe"]
    # Nenhuma mensagem fica pendurada para a proxima pagina.
    pagina = client.get(reverse("content:pautas", urlconf="core.urls_tenants")).content.decode()
    assert "geracao iniciada" not in pagina
    tela = client.get(reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants"))
    assert "data-em-segundo-plano" in tela.content.decode()


def test_aprovada_esperando_curadoria_leva_aos_documentos(ambiente):  # noqa: F811
    from apps.knowledge.models import Document, DocumentCategory

    _, _, client = ambiente
    pauta = Topic.objects.create(
        title="Retencao",
        busca_de_fontes={"acervo": {"pagina": 1, "video": 0, "artigo": 0, "documento": 0}},
    )
    categoria = DocumentCategory.objects.first() or DocumentCategory.objects.create(
        name="Veiculos", slug="veiculos"
    )
    documento = Document.objects.create(
        category=categoria,
        file_sha256="d" * 64,
        title="Pagina aprovada",
        status=Document.Status.PENDING_CURATION,
    )
    CandidatoDeFonte.objects.create(
        url="https://site.com/p",
        titulo="Pagina aprovada",
        pauta=pauta,
        situacao=CandidatoDeFonte.Situacao.APROVADO,
        documento=documento,
    )
    html = client.get(
        reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    ).content.decode()
    documentos = reverse("knowledge:documentos", urlconf="core.urls_tenants")
    assert "1 aprovada(s), documento esperando curadoria" in html
    assert f"{documentos}?pauta={pauta.pk}" in html and "Curar os documentos aprovados" in html
    assert "Decidir as fontes sugeridas" not in html

    lista = client.get(f"{documentos}?pauta={pauta.pk}").content.decode()
    assert "esperam a curadoria" in lista and "Pagina aprovada" in lista
    sugeridas = client.get(
        reverse("knowledge:fontes_sugeridas", urlconf="core.urls_tenants") + f"?pauta={pauta.pk}"
    ).content.decode()
    assert "Nenhuma fonte sugerida esperando decisao" in sugeridas
    assert "ja aprovada espera a curadoria do documento" in sugeridas


def test_nao_usar_nesta_pauta_tira_so_dela(ambiente, monkeypatch):  # noqa: F811
    import types

    from apps.knowledge.models import Document, DocumentCategory

    _, _, client = ambiente
    categoria = DocumentCategory.objects.first() or DocumentCategory.objects.create(
        name="Veiculos", slug="veiculos"
    )
    docs = [
        Document.objects.create(
            category=categoria,
            file_sha256=c * 64,
            title=t,
            status=Document.Status.PENDING_CURATION,
        )
        for c, t in (("e", "meu metodo de 5 prompts"), ("f", "Relatorio bom"))
    ]
    pedidos = []

    def recuperar(**kw):
        pedidos.append(kw.get("excluir_documentos"))
        fora = {str(x) for x in kw.get("excluir_documentos") or []}
        trechos = [
            types.SimpleNamespace(
                chunk=types.SimpleNamespace(
                    pk=n, document_id=d.pk, document=d, supports_central_idea=True
                ),
                distancia=0.1,
            )
            for n, d in enumerate(docs)
            if str(d.pk) not in fora
        ]
        return None, trechos

    monkeypatch.setattr("apps.knowledge.services.recuperar", recuperar)
    pauta = Topic.objects.create(title="O mito do operador de IA")
    acervo = referencias.conferir(pauta)
    assert {d["titulo"] for d in acervo["por_curar"]} == {
        "meu metodo de 5 prompts",
        "Relatorio bom",
    }

    url_da_pauta = reverse("content:pauta", args=[pauta.pk], urlconf="core.urls_tenants")
    assert "Nao usar nesta pauta" in client.get(url_da_pauta).content.decode()
    client.post(
        reverse("content:nao_usar_na_pauta", args=[pauta.pk], urlconf="core.urls_tenants"),
        {"documento": str(docs[0].pk)},
    )
    pauta.refresh_from_db()
    assert [d["titulo"] for d in pauta.busca_de_fontes["acervo"]["por_curar"]] == ["Relatorio bom"]
    assert pedidos[-1] == [str(docs[0].pk)]
    tela = client.get(url_da_pauta).content.decode()
    assert "1 fonte tirada desta pauta" in tela and "Voltar a usar" in tela
    # Documentos da pauta: inclui a que o artigo usaria (veio do acervo, nao das frentes).
    documentos = reverse("knowledge:documentos", urlconf="core.urls_tenants")
    lista = client.get(f"{documentos}?pauta={pauta.pk}").content.decode()
    assert "Relatorio bom" in lista and "meu metodo de 5 prompts" not in lista

    # Outra pauta nao e afetada; e da para devolver.
    outra = Topic.objects.create(title="Outra")
    assert len(referencias.conferir(outra)["por_curar"]) == 2
    client.post(
        reverse("content:nao_usar_na_pauta", args=[pauta.pk], urlconf="core.urls_tenants"),
        {"documento": str(docs[0].pk), "voltar_a_usar": "1"},
    )
    pauta.refresh_from_db()
    assert len(pauta.busca_de_fontes["acervo"]["por_curar"]) == 2
