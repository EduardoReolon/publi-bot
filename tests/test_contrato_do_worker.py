"""Os adaptadores deste projeto leem o que o worker-gpu devolve.

Antes da separacao dos repositorios, este papel era de um teste que rodava o
cliente de verdade contra o app do worker, no mesmo processo. Ele nao pode
mais existir — e sem substituto, os dois lados voltariam a poder divergir num
nome de campo com as duas suites verdes.

O substituto sao os exemplos em `tests/contrato_do_worker/`, copiados de la.
O worker confere que as respostas DELE tem aquela forma; aqui se confere que
os adaptadores DAQUI leem aquela forma. Uma mudanca num lado so quebra
alguem.

O que isto nao pega e a copia envelhecer, e por isso ela tem versao. O
`README.md` ao lado diz como conferir.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

CONTRATO = Path(__file__).resolve().parent / "contrato_do_worker"
VERSAO_ESPERADA = "2.4"


def _exemplo(nome: str) -> dict:
    dados = json.loads((CONTRATO / nome).read_text(encoding="utf-8"))
    # As chaves com `_` sao anotacao para quem le, e nao parte da resposta.
    return {chave: valor for chave, valor in dados.items() if not chave.startswith("_")}


def _cliente_falso(monkeypatch, corpo: dict, status: int = 200):
    """Faz o `httpx.Client` deste processo devolver o exemplo, sem rede."""

    class ClienteFalso:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            return httpx.Response(status, json=corpo, request=httpx.Request("POST", url))

        def get(self, url, **kwargs):
            return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: ClienteFalso())


def test_todos_os_exemplos_declaram_a_mesma_versao():
    """Se a copia for atualizada pela metade, isto acusa antes de o adaptador
    encontrar um campo que mudou."""
    versoes = {
        json.loads(arquivo.read_text(encoding="utf-8"))["_contrato_versao"]
        for arquivo in CONTRATO.glob("*.json")
    }

    assert versoes == {VERSAO_ESPERADA}


def test_o_adaptador_de_texto_le_a_resposta_do_worker(monkeypatch):
    from apps.inference.providers.openai_compatible import OpenAICompatibleClient

    _cliente_falso(monkeypatch, _exemplo("texto-resposta.json"))

    resposta = OpenAICompatibleClient(base_url="http://worker", api_key="x").chat(
        model="qwen2.5:7b-instruct", system="s", user="u"
    )

    assert resposta.text == "O texto gerado."
    assert resposta.input_tokens == 120
    assert resposta.output_tokens == 340


def test_o_adaptador_de_imagem_le_a_resposta_do_worker(monkeypatch):
    import base64

    from apps.inference.providers.openai_compatible import OpenAICompatibleImageClient

    exemplo = _exemplo("imagem-resposta.json")
    _cliente_falso(monkeypatch, exemplo)

    geradas = OpenAICompatibleImageClient(base_url="http://worker", api_key="x").generate(
        model="sdxl", prompt="um gato", quantidade=1
    )

    assert geradas[0].conteudo == base64.b64decode(exemplo["data"][0]["b64_json"])
    assert geradas[0].prompt_revisado == "o prompt usado"


def _documento_de_teste():
    """Um PDF minusculo gravado num tenant, que e o que a extracao recebe."""
    import hashlib

    from django.core.files.base import ContentFile

    from apps.knowledge.models import Document, DocumentCategory

    bruto = b"%PDF-1.4 conteudo qualquer"
    categoria, _ = DocumentCategory.objects.get_or_create(name="Artigo", slug="artigo")
    return Document.objects.create(
        category=categoria,
        original_file=ContentFile(bruto, name="estudo.pdf"),
        file_sha256=hashlib.sha256(bruto).hexdigest(),
        file_size_bytes=len(bruto),
        status=Document.Status.UPLOADED,
    )


def _conexao_de_conversao():
    from apps.inference.models import InferenceConnection
    from apps.inference.security import guardar_chave

    conexao = InferenceConnection(
        name="Worker",
        kind=InferenceConnection.Kind.DOCLING,
        base_url="http://worker:8090",
        is_active=True,
    )
    guardar_chave(conexao, "segredo")
    conexao.save()
    return conexao


def _resposta_falsa(monkeypatch, corpo: dict, status: int = 200):
    """A conversao usa `httpx.post` direto, nao um `Client`."""
    import httpx

    def post(url, **kwargs):
        return httpx.Response(status, json=corpo, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)


@pytest.mark.django_db
def test_a_extracao_le_a_resposta_de_conversao(monkeypatch, tenant_factory):
    """Aqui roda o adaptador de verdade, e nao so uma conferencia sobre o
    arquivo de exemplo: e a leitura de `markdown` e `duration_ms` que quebraria
    se o worker renomeasse um campo."""
    from django_tenants.utils import schema_context

    from apps.knowledge.extraction import _extrair_com_docling

    exemplo = _exemplo("conversao-resposta.json")
    _resposta_falsa(monkeypatch, exemplo)

    tenant = tenant_factory("contrato")
    with schema_context(tenant.schema_name):
        resultado = _extrair_com_docling(_documento_de_teste(), _conexao_de_conversao(), timeout=5)

    assert resultado.markdown == exemplo["markdown"]
    assert resultado.markdown.startswith("#")
    assert resultado.metodo == "docling"
    assert resultado.duracao_ms == exemplo["duration_ms"]


@pytest.mark.django_db
def test_o_503_na_conversao_adia_em_vez_de_falhar(monkeypatch, tenant_factory):
    """Mesmo contrato do texto e da imagem, no caminho do PDF: um 503 e a placa
    ocupada, nao um documento ruim. Tratado como falha, ele gastaria as
    tentativas de um envio que so precisava da vez."""
    from django_tenants.utils import schema_context

    from apps.knowledge.extraction import ConversorOcupado, _extrair_com_docling

    _resposta_falsa(monkeypatch, _exemplo("ocupada-resposta.json"), status=503)

    tenant = tenant_factory("contrato-503")
    with schema_context(tenant.schema_name):
        with pytest.raises(ConversorOcupado):
            _extrair_com_docling(_documento_de_teste(), _conexao_de_conversao(), timeout=5)


def test_o_503_do_worker_tem_os_campos_que_o_cliente_le():
    """`error.code` e o que decide entre esperar e desistir. Os quatro codigos
    estao documentados no INTEGRACAO.md do worker; este teste guarda os nomes
    contra uma renomeacao silenciosa."""
    exemplo = _exemplo("ocupada-resposta.json")

    assert exemplo["error"]["code"] == "gpu_ocupada"
    assert "message" in exemplo["error"]


def test_o_cliente_trata_503_como_transitorio():
    """`STATUS_TERMINAIS` decide o que nao adianta repetir. Um 503 fora dessa
    lista e o que faz o trabalho ser ADIADO em vez de falhar — e adiar nao
    gasta tentativa."""
    from apps.inference.providers.openai_compatible import STATUS_TERMINAIS

    assert 503 not in STATUS_TERMINAIS


# ---------------------------------------------------------------------------
# O `/health/` do worker tambem e contrato
# ---------------------------------------------------------------------------
# Ele nao estava aqui, e essa ausencia custou caro: quando o worker virou um
# processo so, o estado de cada rota passou a vir ANINHADO (`imagem`,
# `conversao`), e os dois comandos de configuracao continuaram lendo as chaves
# na raiz. Nao deu erro nenhum — `dict.get` devolveu `None`, o comando imprimiu
# "dispositivo=None" e os dois avisos que justificam o `--testar` (pesos nao
# baixados, configurado para CPU) simplesmente pararam de aparecer.
def _saude() -> dict:
    return _exemplo("saude-resposta.json")


def _health_falso(monkeypatch, corpo: dict, status: int = 200):
    import httpx

    def get(url, **kwargs):
        return httpx.Response(status, json=corpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


@pytest.fixture
def semente_de_imagem(settings):
    settings.IMAGEM_NOME = "Geracao de imagem"
    settings.IMAGEM_BASE_URL = "http://worker:8090"
    settings.IMAGEM_MODELO = "stabilityai/stable-diffusion-xl-base-1.0"
    settings.IMAGEM_SEGREDO = "segredo"
    return settings


@pytest.fixture
def semente_de_conversao(settings):
    settings.CONVERSAO_NOME = "Conversao de PDF"
    settings.CONVERSAO_BASE_URL = "http://worker:8090"
    settings.CONVERSAO_SEGREDO = "segredo"
    return settings


@pytest.mark.django_db
def test_configurar_imagem_le_a_secao_aninhada(monkeypatch, semente_de_imagem, capsys):
    from django.core.management import call_command

    _health_falso(monkeypatch, _saude())
    call_command("configurar_imagem", "--testar")

    saida = capsys.readouterr().out
    assert "modelo=stabilityai/stable-diffusion-xl-base-1.0" in saida
    assert "dispositivo=auto" in saida
    assert "None" not in saida, "leu as chaves na raiz e nao achou nada"


@pytest.mark.django_db
def test_o_aviso_de_pesos_nao_baixados_sobrevive(monkeypatch, semente_de_imagem, capsys):
    """E o aviso mais util do comando: sem ele, a primeira geracao baixa 7 GB
    dentro da requisicao e a tela parece travada ate esgotar o tempo."""
    from django.core.management import call_command

    corpo = _saude()
    corpo["imagem"] = {**corpo["imagem"], "baixado": False}
    _health_falso(monkeypatch, corpo)

    call_command("configurar_imagem", "--testar")

    assert "baixar_modelo.py" in capsys.readouterr().out


@pytest.mark.django_db
def test_rota_de_imagem_desligada_e_dito_com_todas_as_letras(monkeypatch, semente_de_imagem):
    """`IMAGEM_ATIVA=nao` no worker some com a secao. Sem este ramo, o comando
    diria "respondeu 200" e a pessoa procuraria o defeito aqui."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    corpo = _saude()
    del corpo["imagem"]
    corpo["rotas"] = {**corpo["rotas"], "imagem": False}
    _health_falso(monkeypatch, corpo)

    with pytest.raises(CommandError, match="IMAGEM_ATIVA"):
        call_command("configurar_imagem", "--testar")


@pytest.mark.django_db
def test_configurar_conversao_le_a_secao_aninhada(monkeypatch, semente_de_conversao, capsys):
    from django.core.management import call_command

    _health_falso(monkeypatch, _saude())
    call_command("configurar_conversao", "--testar")

    saida = capsys.readouterr().out
    assert "dispositivo=auto" in saida
    assert "ocr=False" in saida


@pytest.mark.django_db
def test_rota_de_conversao_desligada_e_dito_com_todas_as_letras(monkeypatch, semente_de_conversao):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    corpo = _saude()
    del corpo["conversao"]
    corpo["rotas"] = {**corpo["rotas"], "conversao": False}
    _health_falso(monkeypatch, corpo)

    with pytest.raises(CommandError, match="CONVERSAO_ATIVA"):
        call_command("configurar_conversao", "--testar")


# ---------------------------------------------------------------------------
# O 503 do worker tem codigo, e cada codigo pede uma coisa diferente
# ---------------------------------------------------------------------------
def _recusa(codigo: str, *, retry_after: int | None = 40):
    """Um 503 como o worker manda: corpo com `error.code`, cabecalho quando ha."""
    cabecalhos = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return httpx.Response(
        503,
        json={"error": {"code": codigo, "message": f"a GPU esta em uso ({codigo})"}},
        headers=cabecalhos,
        request=httpx.Request("POST", "http://worker/v1/chat/completions"),
    )


def _levantar(resposta):
    from apps.inference.providers.openai_compatible import OpenAICompatibleClient

    OpenAICompatibleClient._levantar_se_erro(resposta)


def test_o_retry_after_do_worker_chega_ao_adiamento():
    """Ele e CALCULADO a partir do que esta rodando. Descartar e voltar cedo
    (outra recusa garantida) ou tarde (placa parada)."""
    from apps.inference.providers.base import ProviderTransientError

    with pytest.raises(ProviderTransientError) as erro:
        _levantar(_recusa("gpu_ocupada", retry_after=40))

    assert erro.value.retry_after == 40
    assert erro.value.code == "gpu_ocupada"


def test_o_codigo_timeout_nao_e_para_repetir_igual():
    """O worker OMITE o `Retry-After` neste codigo de proposito. Como adiar
    nao gasta tentativa, trata-lo como transitorio reagendaria o mesmo pedido
    para sempre."""
    from apps.inference.providers.base import ProviderPermanentError

    with pytest.raises(ProviderPermanentError) as erro:
        _levantar(_recusa("timeout", retry_after=None))

    assert "Reduza o trabalho" in str(erro.value)


def test_codigo_desconhecido_e_adiavel():
    """Regra publicada do worker. Sem ela, a lista de codigos nunca mais
    poderia crescer: qualquer codigo novo quebraria todo cliente existente."""
    from apps.inference.providers.base import ProviderTransientError

    with pytest.raises(ProviderTransientError) as erro:
        _levantar(_recusa("codigo_que_ainda_nao_existe"))

    assert erro.value.code == "codigo_que_ainda_nao_existe"


def test_baixando_modelo_e_adiavel_com_a_espera_do_download():
    from apps.inference.providers.base import ProviderTransientError

    with pytest.raises(ProviderTransientError) as erro:
        _levantar(_recusa("baixando_modelo", retry_after=95))

    assert erro.value.retry_after == 95


def test_o_404_de_download_falho_faz_desistir():
    """Depois de um download que falhou, o worker devolve 404 em vez de 503,
    justamente para o cliente parar de reagendar."""
    from apps.inference.providers.base import ProviderPermanentError

    resposta = httpx.Response(
        404,
        json={"error": {"code": "modelo_indisponivel", "message": "o download falhou"}},
        request=httpx.Request("POST", "http://worker/v1/chat/completions"),
    )

    with pytest.raises(ProviderPermanentError):
        _levantar(resposta)


def test_um_provedor_que_nao_fala_esse_dialeto_nao_quebra_a_leitura():
    """Um 502 de proxy devolve HTML. Descobrir isso nao pode transformar um
    erro ja identificado noutro erro, dentro do tratamento de erro."""
    from apps.inference.providers.base import ProviderTransientError

    resposta = httpx.Response(
        502,
        text="<html><body>Bad Gateway</body></html>",
        request=httpx.Request("POST", "http://worker/v1/chat/completions"),
    )

    with pytest.raises(ProviderTransientError) as erro:
        _levantar(resposta)

    assert erro.value.code == ""
    assert erro.value.retry_after is None


def test_retry_after_em_formato_de_data_nao_vira_espera_absurda():
    """O HTTP permite data em vez de segundos. Nenhum provedor daqui a usa, e
    `int("Wed, 21 Oct 2026...")` levantaria dentro do tratamento de erro."""
    from apps.inference.providers.base import ProviderTransientError

    resposta = httpx.Response(
        503,
        json={"error": {"code": "gpu_ocupada", "message": "x"}},
        headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        request=httpx.Request("POST", "http://worker/v1/chat/completions"),
    )

    with pytest.raises(ProviderTransientError) as erro:
        _levantar(resposta)

    assert erro.value.retry_after is None
