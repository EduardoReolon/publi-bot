"""Testes da conferencia da extracao.

O que se protege aqui e a capacidade de calibrar sem quebrar o que ja
funcionava. As heuristicas de extracao foram ajustadas a mao, artigo por artigo,
e esse e o risco conhecido desse metodo: consertar um documento quebra outro em
silencio. A conferencia e o que torna isso visivel.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.knowledge.conferencia import (
    carregar_esperado,
    comparar_com_a_curadoria,
    comparar_com_o_esperado,
    gravar_esperado,
    taxa_de_acerto,
)
from apps.knowledge.models import Document, DocumentCategory


@pytest.fixture
def tenant_de_conferencia(tenant_factory):
    tenant = tenant_factory("conferencia")
    with schema_context(tenant.schema_name):
        DocumentCategory.objects.create(name="Artigo", slug="artigo")
        yield tenant


def _documento(**extra) -> Document:
    semente = extra.pop("semente", "a")
    return Document.objects.create(
        category=DocumentCategory.objects.first(),
        file_sha256=hashlib.sha256(semente.encode()).hexdigest(),
        original_file=ContentFile(b"pdf", name=f"{semente}.pdf"),
        **extra,
    )


# ---------------------------------------------------------------------------
# O acervo como gabarito
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_curadoria_e_o_gabarito(tenant_de_conferencia):
    """Nao precisa de botao "esse errou": quando a pessoa corrige o campo, a
    correcao E o rotulo."""
    with schema_context(tenant_de_conferencia.schema_name):
        documento = _documento(
            title="Titulo correto e completo do artigo",
            authors="Kim, Y. et al.",
            year=2017,
            metadata_suggested={
                "title": "Springer Science+Business Media B.V . 2017",
                "authors": "Kim, Y. et al.",
                "year": 2017,
                "doi": "",
            },
            metadata_confidence=Document.MetadataConfidence.MANUAL,
            reviewed_at=timezone.now(),
        )

        divergencias = comparar_com_a_curadoria(documento)

        assert [d.campo for d in divergencias] == ["title"]
        assert divergencias[0].sugerido.startswith("Springer")
        assert divergencias[0].corrigido.startswith("Titulo correto")


@pytest.mark.django_db
def test_documento_nao_conferido_nao_conta(tenant_de_conferencia):
    """Antes da curadoria os campos SAO a sugestao. Compara-los diria sempre
    "acertou", e a taxa de acerto viraria propaganda."""
    with schema_context(tenant_de_conferencia.schema_name):
        _documento(
            title="Titulo qualquer",
            metadata_suggested={"title": "Titulo qualquer", "authors": "", "year": None, "doi": ""},
            metadata_confidence=Document.MetadataConfidence.AUTO,
        )

        assert taxa_de_acerto()["documentos"] == 0


@pytest.mark.django_db
def test_vazio_e_nulo_sao_a_mesma_ausencia(tenant_de_conferencia):
    """`""` no campo e `None` na sugestao nao sao uma correcao humana."""
    with schema_context(tenant_de_conferencia.schema_name):
        documento = _documento(
            title="T",
            doi="",
            metadata_suggested={"title": "T", "authors": "", "year": None, "doi": None},
            metadata_confidence=Document.MetadataConfidence.MANUAL,
        )

        assert comparar_com_a_curadoria(documento) == []


@pytest.mark.django_db
def test_taxa_de_acerto_conta_campos(tenant_de_conferencia):
    with schema_context(tenant_de_conferencia.schema_name):
        _documento(
            semente="b",
            title="Certo",
            authors="Errado corrigido",
            year=2020,
            doi="10.1/x",
            metadata_suggested={
                "title": "Certo",
                "authors": "O que a extracao propos",
                "year": 2020,
                "doi": "10.1/x",
            },
            metadata_confidence=Document.MetadataConfidence.MANUAL,
        )

        resumo = taxa_de_acerto()

        assert resumo["documentos"] == 1
        assert resumo["campos"] == 4
        assert resumo["acertos"] == 3
        assert resumo["percentual"] == 75


# ---------------------------------------------------------------------------
# Regressao contra arquivo esperado
# ---------------------------------------------------------------------------
def test_esperado_ida_e_volta(tmp_path):
    alvo = tmp_path / "artigo.json"
    resultado = {"title": "T", "authors": "A", "year": 2020, "doi": "", "blocos": ["", "Abstract"]}

    gravar_esperado(alvo, resultado)

    assert carregar_esperado(alvo) == resultado
    assert json.loads(alvo.read_text(encoding="utf-8")) == resultado


def test_esperado_ausente_devolve_none(tmp_path):
    assert carregar_esperado(tmp_path / "nao_existe.json") is None


def test_comparacao_aponta_o_campo_que_mudou():
    """A diferenca precisa dizer QUAL campo e o que mudou nele. "diferente"
    sozinho obrigaria a pessoa a caçar a mudanca no olho."""
    esperado = {"title": "T", "authors": "A", "year": 2020, "doi": "", "blocos": ["Abstract"]}
    obtido = {**esperado, "blocos": ["Abstract", "INTRODUCTION"]}

    diferencas = comparar_com_o_esperado(obtido, esperado)

    assert len(diferencas) == 1
    assert diferencas[0].startswith("blocos:")
    assert "INTRODUCTION" in diferencas[0]


def test_sem_diferenca_a_lista_e_vazia():
    esperado = {"title": "T", "authors": "A", "year": 2020, "doi": "", "blocos": []}

    assert comparar_com_o_esperado(dict(esperado), esperado) == []


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_comando_exige_um_modo(tenant_de_conferencia):
    from django.core.management.base import CommandError

    with schema_context(tenant_de_conferencia.schema_name), pytest.raises(CommandError):
        call_command("conferir_extracao")


@pytest.mark.django_db
def test_comando_do_acervo_lista_as_correcoes(tenant_de_conferencia, capsys):
    with schema_context(tenant_de_conferencia.schema_name):
        _documento(
            title="Titulo correto",
            authors="Kim, Y. et al.",
            metadata_suggested={
                "title": "jawr_027 346..358",
                "authors": "Kim, Y. et al.",
                "year": None,
                "doi": "",
            },
            metadata_confidence=Document.MetadataConfidence.MANUAL,
            reviewed_at=timezone.now(),
        )

        call_command("conferir_extracao", acervo=True)

    saida = capsys.readouterr().out
    assert "jawr_027" in saida
    assert "Titulo correto" in saida


@pytest.mark.django_db
def test_comando_do_acervo_sem_documento_curado_explica(tenant_de_conferencia, capsys):
    with schema_context(tenant_de_conferencia.schema_name):
        call_command("conferir_extracao", acervo=True)

    assert "precisa da conferencia humana" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Marcacao humana e exportacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_marcacao_registra_o_que_a_comparacao_nao_ve(tenant_de_conferencia):
    """Divisao de blocos errada nao muda campo nenhum de metadado.

    Sem a marcacao, esse caso — que e o pior de todos — passaria por acerto na
    comparacao automatica.
    """
    from django.conf import settings as configuracao
    from django.test import Client
    from django.urls import reverse

    from apps.accounts.models import TenantMembership, User

    with schema_context(tenant_de_conferencia.schema_name):
        documento = _documento(
            title="Titulo certo",
            authors="Silva, A.",
            metadata_suggested={
                "title": "Titulo certo",
                "authors": "Silva, A.",
                "year": None,
                "doi": "",
            },
            metadata_confidence=Document.MetadataConfidence.MANUAL,
        )
        # A extracao acertou todo campo de metadado deste documento.
        assert comparar_com_a_curadoria(documento) == []

        usuario = User.objects.create_user(
            email="dev@exemplo.com", password="uma-senha-longa-de-teste", full_name="Dev"
        )
        TenantMembership.objects.create(tenant=tenant_de_conferencia, user=usuario, is_active=True)
        cliente = Client()
        cliente.force_login(usuario)
        cliente.defaults["HTTP_HOST"] = f"{tenant_de_conferencia.slug}.{configuracao.ROOT_DOMAIN}"

        cliente.post(
            reverse("knowledge:marcar_extracao", args=[documento.pk], urlconf="core.urls_tenants"),
            {"problema": "blocos", "observacao": "juntou conclusao com referencias"},
        )

        documento.refresh_from_db()
        assert documento.extracao_marcada
        assert documento.extraction_problem == Document.ProblemaDeExtracao.BLOCOS
        assert documento.extraction_flagged_by == usuario
        assert "referencias" in documento.extraction_note


@pytest.mark.django_db
def test_desmarcar_limpa_tudo(tenant_de_conferencia):
    from django.conf import settings as configuracao
    from django.test import Client
    from django.urls import reverse

    from apps.accounts.models import TenantMembership, User

    with schema_context(tenant_de_conferencia.schema_name):
        documento = _documento(
            title="T",
            extraction_flagged_at=timezone.now(),
            extraction_problem=Document.ProblemaDeExtracao.TEXTO,
            extraction_note="algo",
        )
        usuario = User.objects.create_user(
            email="dev2@exemplo.com", password="uma-senha-longa-de-teste", full_name="Dev"
        )
        TenantMembership.objects.create(tenant=tenant_de_conferencia, user=usuario, is_active=True)
        cliente = Client()
        cliente.force_login(usuario)
        cliente.defaults["HTTP_HOST"] = f"{tenant_de_conferencia.slug}.{configuracao.ROOT_DOMAIN}"

        cliente.post(
            reverse("knowledge:marcar_extracao", args=[documento.pk], urlconf="core.urls_tenants"),
            {"acao": "desmarcar"},
        )

        documento.refresh_from_db()
        assert not documento.extracao_marcada
        assert documento.extraction_problem == ""


@pytest.mark.django_db
def test_exportar_gera_pdf_e_gabarito(tenant_de_conferencia, tmp_path):
    """O servidor e deploy, nao clone: o caso precisa sair de la em arquivo.

    O JSON e o que torna o PDF util — sozinho ele nao diz o que era para ter
    saido.
    """
    with schema_context(tenant_de_conferencia.schema_name):
        _documento(
            title="Titulo conferido",
            authors="Silva, A.",
            markdown_full=(
                "1 Introducao\nTexto do corpo com palavras suficientes para virar prosa.\n"
            ),
            extraction_method=Document.ExtractionMethod.PYPDF,
            extraction_flagged_at=timezone.now(),
            extraction_problem=Document.ProblemaDeExtracao.BLOCOS,
            metadata_suggested={
                "title": "jawr_027 346..358",
                "authors": "Silva, A.",
                "year": None,
                "doi": "",
            },
            metadata_confidence=Document.MetadataConfidence.MANUAL,
        )

        call_command("exportar_casos", destino=str(tmp_path))

    pdfs = list(tmp_path.glob("*.pdf"))
    jsons = list(tmp_path.glob("*.json"))
    assert len(pdfs) == 1
    assert len(jsons) == 1
    # Mesmo nome nos dois: e o que amarra o gabarito ao arquivo.
    assert pdfs[0].stem == jsons[0].stem

    caso = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert caso["problema"] == "blocos"
    assert caso["extraiu"]["title"] == "jawr_027 346..358"
    assert caso["conferido"]["title"] == "Titulo conferido"
    assert caso["corrigidos"] == ["title"]


@pytest.mark.django_db
def test_exportar_sem_pdf_quando_o_arquivo_nao_pode_sair(tenant_de_conferencia, tmp_path):
    with schema_context(tenant_de_conferencia.schema_name):
        _documento(
            title="T",
            extraction_flagged_at=timezone.now(),
            extraction_problem=Document.ProblemaDeExtracao.TEXTO,
        )

        call_command("exportar_casos", destino=str(tmp_path), sem_pdf=True)

    assert list(tmp_path.glob("*.pdf")) == []
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.django_db
def test_exportar_sem_nada_marcado_explica(tenant_de_conferencia, tmp_path, capsys):
    with schema_context(tenant_de_conferencia.schema_name):
        call_command("exportar_casos", destino=str(tmp_path))

    assert "Marque a extracao" in capsys.readouterr().out
