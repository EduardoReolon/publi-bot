"""O comando que cadastra a conexao de conversao de PDF.

Sem essa linha no banco, o sistema NAO quebra — e esse e o problema. Ele cai no
extrator local, que devolve a camada de texto do PDF sem interpretar a pagina.
O texto continua parecendo correto, e um artigo de coluna dupla pode chegar com
as frases das duas colunas intercaladas.

Por isso o comando existe: um estado tao silencioso nao pode depender de
alguem lembrar de abrir o admin.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.inference.models import InferenceConnection
from apps.inference.security import decifrar_chave

pytestmark = pytest.mark.django_db

CONFIG = {
    "CONVERSAO_NOME": "Conversao de PDF",
    "CONVERSAO_BASE_URL": "http://127.0.0.1:8100",
    "CONVERSAO_SEGREDO": "segredo-do-worker",
}


def test_cria_a_conexao(capsys):
    with override_settings(**CONFIG):
        call_command("configurar_conversao")

    conexao = InferenceConnection.objects.get(name="Conversao de PDF")
    assert conexao.kind == InferenceConnection.Kind.DOCLING
    assert conexao.base_url == "http://127.0.0.1:8100"
    assert conexao.workloads == [InferenceConnection.Workload.VISION_PARSE]
    # Uma conversao por vez: a maquina que converte e a mesma que gera texto, e
    # duas simultaneas estouram a VRAM.
    assert conexao.max_concurrency == 1
    assert decifrar_chave(conexao) == "segredo-do-worker"


def test_a_conexao_criada_e_a_que_a_extracao_encontra():
    """O teste que realmente importa: nao basta gravar a linha, ela precisa ser
    a linha que `extrair_markdown` procura antes de cair no extrator local."""
    from apps.knowledge.extraction import conexao_de_conversao

    with override_settings(**CONFIG):
        call_command("configurar_conversao")

    encontrada = conexao_de_conversao()
    assert encontrada is not None
    assert encontrada.base_url == "http://127.0.0.1:8100"


def test_e_idempotente_e_preserva_o_que_esta_no_banco(capsys):
    """Trocar o endereco do worker pela tela nao pode ser desfeito pelo proximo
    deploy."""
    with override_settings(**CONFIG):
        call_command("configurar_conversao")

    conexao = InferenceConnection.objects.get(name="Conversao de PDF")
    conexao.base_url = "http://100.64.0.9:8100"
    conexao.save()

    with override_settings(**CONFIG):
        call_command("configurar_conversao")

    conexao.refresh_from_db()
    assert conexao.base_url == "http://100.64.0.9:8100"
    assert "preservada" in capsys.readouterr().out
    assert InferenceConnection.objects.filter(name="Conversao de PDF").count() == 1


def test_atualizar_sobrescreve():
    with override_settings(**CONFIG):
        call_command("configurar_conversao")
        InferenceConnection.objects.filter(name="Conversao de PDF").update(
            base_url="http://errado:1"
        )
        call_command("configurar_conversao", atualizar=True)

    conexao = InferenceConnection.objects.get(name="Conversao de PDF")
    assert conexao.base_url == "http://127.0.0.1:8100"


def test_atualizar_reabre_o_disjuntor():
    """Reconfigurar uma conexao que passou a tarde fora do ar nao pode deixar o
    circuito fechado por mais 15 minutos: a pessoa concluiria que o comando nao
    funcionou."""
    from django.utils import timezone

    with override_settings(**CONFIG):
        call_command("configurar_conversao")
        InferenceConnection.objects.filter(name="Conversao de PDF").update(
            consecutive_failures=9, circuit_open_until=timezone.now() + timezone.timedelta(hours=1)
        )
        call_command("configurar_conversao", atualizar=True)

    conexao = InferenceConnection.objects.get(name="Conversao de PDF")
    assert conexao.consecutive_failures == 0
    assert conexao.circuit_open_until is None


def test_sem_endereco_o_erro_diz_o_que_preencher():
    with override_settings(**{**CONFIG, "CONVERSAO_BASE_URL": ""}):
        with pytest.raises(CommandError, match="CONVERSAO_BASE_URL"):
            call_command("configurar_conversao")


def test_sem_segredo_o_erro_explica_de_onde_ele_vem():
    """Esquecer o segredo produz 401 no worker, e a mensagem do 401 nao diz que
    o problema e credencial."""
    with override_settings(**{**CONFIG, "CONVERSAO_SEGREDO": ""}):
        with pytest.raises(CommandError, match="WORKER_SHARED_SECRET"):
            call_command("configurar_conversao")


def test_opcional_sem_endereco_nao_derruba_a_implantacao(capsys):
    """E assim que o release.sh chama. Uma instalacao sem worker de conversao e
    um estado legitimo — o resto do sistema funciona."""
    with override_settings(**{**CONFIG, "CONVERSAO_BASE_URL": ""}):
        call_command("configurar_conversao", opcional=True)

    assert not InferenceConnection.objects.filter(kind=InferenceConnection.Kind.DOCLING).exists()
    saida = capsys.readouterr().out
    assert "extrator local" in saida


def test_cadastrar_tira_o_pdf_do_extrator_local(tenant_factory, monkeypatch):
    """A mudanca que a pessoa esta comprando, verificada de ponta a ponta.

    ANTES: o mesmo PDF sai por `pypdf`, texto puro, sem analise de layout.
    DEPOIS: sai por `docling`, Markdown, com a estrutura da pagina.

    Sao dois caminhos de codigo diferentes, e o unico sinal que os distingue no
    banco e o campo `metodo` — que e o que a tela de curadoria le para decidir
    se mostra o aviso de extracao sem layout.
    """
    import hashlib

    import httpx
    from django.core.files.base import ContentFile
    from django_tenants.utils import schema_context

    from apps.knowledge.extraction import extrair_markdown
    from apps.knowledge.models import Document, DocumentCategory

    tenant = tenant_factory("conversao")

    with override_settings(**CONFIG), schema_context(tenant.schema_name):
        DocumentCategory.objects.create(name="Artigo", slug="artigo")
        bruto = _pdf_de_uma_pagina()
        documento = Document.objects.create(
            category=DocumentCategory.objects.first(),
            original_file=ContentFile(bruto, name="estudo.pdf"),
            file_sha256=hashlib.sha256(bruto).hexdigest(),
            file_size_bytes=len(bruto),
            status=Document.Status.UPLOADED,
        )

        antes = extrair_markdown(documento)
        assert antes.metodo == "pypdf"

        call_command("configurar_conversao")

        monkeypatch.setattr(
            httpx,
            "post",
            lambda *a, **k: httpx.Response(
                200, json={"markdown": "# Titulo\n\n## Metodos", "duration_ms": 1200}
            ),
        )

        depois = extrair_markdown(documento)

    assert depois.metodo == "docling"
    assert depois.markdown.startswith("# ")


def _pdf_de_uma_pagina() -> bytes:
    """Um PDF valido, com UMA linha de texto de verdade.

    Escrito pelo proprio pypdf e nao fixado como literal porque um PDF valido
    exige tabela de referencia cruzada e `startxref` coerentes; um literal
    montado a mao falha com `startxref not found`, e o teste passaria a medir a
    minha habilidade de escrever PDF em vez do caminho da extracao.

    E precisa ter texto: numa pagina em branco o extrator local recusa com
    "o PDF nao tem camada de texto", que e o comportamento certo e o caminho
    errado para este teste.
    """
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    escritor = PdfWriter()
    pagina = escritor.add_blank_page(width=612, height=792)

    fluxo = DecodedStreamObject()
    fluxo.set_data(b"BT /F1 12 Tf 72 700 Td (1 Introducao) Tj ET")
    pagina[NameObject("/Contents")] = escritor._add_object(fluxo)

    fonte = DictionaryObject()
    fonte.update(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    recursos = DictionaryObject()
    recursos[NameObject("/Font")] = DictionaryObject(
        {NameObject("/F1"): escritor._add_object(fonte)}
    )
    pagina[NameObject("/Resources")] = recursos

    buffer = io.BytesIO()
    escritor.write(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# --testar, contra um worker de mentira que responde como o de verdade
# ---------------------------------------------------------------------------
# O estado da conversao vem ANINHADO em `conversao`: o worker publica tres
# rotas no mesmo `/health/`. A forma canonica esta em
# `tests/contrato_do_worker/saude-resposta.json`.
def _saude(**conversao) -> dict:
    return {
        "status": "ok",
        "service": "worker-gpu",
        "ocupada": False,
        "rotas": {"texto": True, "imagem": True, "conversao": True},
        "conversao": {"dispositivo": "cuda", "ocr": False, **conversao},
    }


def test_testar_relata_o_dispositivo(capsys, monkeypatch):
    """O dispositivo na resposta nao e enfeite: trocar `DOCLING_DEVICE` e
    esquecer de reiniciar o worker nao gera erro nenhum — so deixa a conversao
    lenta, e a conclusao natural e "o Docling e lento mesmo"."""
    _fingir_health(monkeypatch, _saude(dispositivo="cuda"))

    with override_settings(**CONFIG):
        call_command("configurar_conversao", testar=True)

    assert "dispositivo=cuda" in capsys.readouterr().out


def test_testar_em_cpu_explica_como_trocar(capsys, monkeypatch):
    _fingir_health(monkeypatch, _saude(dispositivo="cpu"))

    with override_settings(**CONFIG):
        call_command("configurar_conversao", testar=True)

    saida = capsys.readouterr().out
    assert "o resultado e o MESMO" in saida
    assert "DOCLING_DEVICE=cuda" in saida


def test_worker_inalcancavel_aponta_rede_e_tailscale(monkeypatch):
    """A falha mais comum aqui nao e configuracao, e rede."""
    import httpx

    def recusar(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", recusar)

    with override_settings(**CONFIG):
        with pytest.raises(CommandError, match="Tailscale"):
            call_command("configurar_conversao", testar=True)


def test_endereco_que_responde_outra_coisa_nao_passa_por_worker(monkeypatch):
    """Apontar para o Ollama por engano (11434 em vez de 8100) responde, mas
    nao e o servico de conversao."""
    _fingir_health(monkeypatch, {}, status=404)

    with override_settings(**CONFIG):
        with pytest.raises(CommandError, match="nao e o servico de conversao"):
            call_command("configurar_conversao", testar=True)


def _fingir_health(monkeypatch, corpo: dict, status: int = 200) -> None:
    import httpx

    def get(url, **kwargs):
        return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
