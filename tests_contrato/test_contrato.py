"""Testa o contrato exercitando os DOIS lados de verdade.

O cliente do PubliBot fala com a implementacao de referencia por HTTP real,
com assinatura real. Testar so um lado deixaria passar exatamente a classe de
defeito que mais importa aqui: os dois lados calcularem a assinatura de forma
diferente, ou discordarem sobre o que e idempotencia.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest

pytestmark = pytest.mark.django_db

SEGREDO = "segredo-de-assinatura-do-teste"
CHAVE = "chave-de-api-do-teste"


def _assinar(
    corpo: bytes, *, segredo: str = SEGREDO, timestamp: str | None = None, nonce: str | None = None
) -> dict[str, str]:
    """Reproduz a assinatura do lado do cliente."""
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or str(uuid.uuid4())
    digest = hashlib.sha256(corpo).hexdigest()
    base = f"{timestamp}.{nonce}.{digest}"
    assinatura = hmac.new(
        SEGREDO.encode() if segredo is SEGREDO else segredo.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "HTTP_X_API_KEY": CHAVE,
        "HTTP_X_TIMESTAMP": timestamp,
        "HTTP_X_NONCE": nonce,
        "HTTP_X_SIGNATURE": f"v1={assinatura}",
    }


class ClienteSeguro:
    """Cliente de teste que sempre fala HTTPS.

    O contrato EXIGE TLS, e a implementacao de referencia responde 403 a
    requisicao em texto claro. Desligar essa checagem para os testes passarem
    esconderia justamente a defesa que se quer verificar.

    Precisa ser um involucro e nao `client.defaults`: o `generic()` do Django
    fixa `wsgi.url_scheme` a partir do parametro `secure`, sobrescrevendo
    qualquer default.
    """

    def __init__(self, client):
        self._client = client

    def get(self, path, **extra):
        return self._client.get(path, secure=True, **extra)

    def post(self, path, data=None, content_type=None, **extra):
        return self._client.post(path, data=data, content_type=content_type, secure=True, **extra)


@pytest.fixture
def no_receptor(client):
    """Cliente HTTP falando com o no de referencia.

    As credenciais e o caminho do app vem de `core.settings.test_contract`.
    """
    return ClienteSeguro(client)


def _publicar(cliente, payload: dict, *, chave_idem: str | None = None, **extra):
    corpo = json.dumps(payload).encode()
    chave_idem = chave_idem or str(uuid.uuid4())
    cabecalhos = _assinar(corpo, **extra)
    cabecalhos["HTTP_IDEMPOTENCY_KEY"] = chave_idem
    return cliente.post(
        "/api/v1/publish/", data=corpo, content_type="application/json", **cabecalhos
    ), chave_idem


PAYLOAD = {
    "type": "article",
    "title": "Monitoramento na gestacao",
    "slug": "monitoramento-na-gestacao",
    "html_content": (
        "<h2>Achados</h2><p>Conforme <a href='https://pubmed.gov/1'>Silva, 2024</a>.</p>"
    ),
    "meta_description": "Resumo do artigo",
    "author": {"name": "Ana Souza", "credentials": "COREN-SP 123456"},
    "reviewed_by": "Ana Souza",
    "content_disclosure": "Produzido com apoio de IA e revisado por Ana Souza.",
    "status": "published",
}


# ---------------------------------------------------------------------------
# Assinatura, dos dois lados
# ---------------------------------------------------------------------------


def test_requisicao_assinada_e_aceita(no_receptor):
    resposta, _ = _publicar(no_receptor, PAYLOAD)
    assert resposta.status_code == 201, resposta.content
    dados = resposta.json()
    assert dados["status"] == "success"
    assert dados["remote_id"]
    assert dados["url"].startswith("https://exemplo.com.br/blog/")


def test_sem_assinatura_e_recusada(no_receptor):
    resposta = no_receptor.post(
        "/api/v1/publish/",
        data=json.dumps(PAYLOAD),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert resposta.status_code == 401


def test_corpo_adulterado_apos_assinar_e_recusado(no_receptor):
    """A assinatura cobre o corpo. Um intermediario que alterasse o conteudo
    trocaria o que e publicado sem que nada detectasse."""
    cabecalhos = _assinar(json.dumps(PAYLOAD).encode())
    cabecalhos["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())

    adulterado = {**PAYLOAD, "html_content": "<p>conteudo trocado</p>"}
    resposta = no_receptor.post(
        "/api/v1/publish/",
        data=json.dumps(adulterado).encode(),
        content_type="application/json",
        **cabecalhos,
    )
    assert resposta.status_code == 401


def test_requisicao_antiga_e_recusada(no_receptor):
    """Sem instante na assinatura, uma requisicao capturada hoje continuaria
    valida daqui a um ano."""
    antigo = str(int(time.time()) - 400)
    resposta, _ = _publicar(no_receptor, PAYLOAD, timestamp=antigo)
    assert resposta.status_code == 401


def test_nonce_repetido_e_recusado(no_receptor):
    """Impede reexecutar uma requisicao interceptada dentro da janela."""
    nonce = str(uuid.uuid4())
    primeira, _ = _publicar(no_receptor, PAYLOAD, nonce=nonce)
    assert primeira.status_code == 201

    segunda, _ = _publicar(no_receptor, {**PAYLOAD, "title": "Outro"}, nonce=nonce)
    assert segunda.status_code == 401


def test_erro_de_credencial_devolve_sempre_o_mesmo_corpo(no_receptor):
    """Mensagens diferentes vazariam a mesma informacao por outro canal: quem
    tenta descobriria pelo texto se a chave existe."""
    corpo = json.dumps(PAYLOAD).encode()
    base = _assinar(corpo)

    casos = [
        {**base, "HTTP_X_API_KEY": "chave-errada"},
        {**base, "HTTP_X_SIGNATURE": "v1=deadbeef"},
        {k: v for k, v in base.items() if k != "HTTP_X_API_KEY"},
    ]
    respostas = []
    for cabecalhos in casos:
        cabecalhos["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
        r = no_receptor.post(
            "/api/v1/publish/", data=corpo, content_type="application/json", **cabecalhos
        )
        respostas.append((r.status_code, r.content))

    assert len(set(respostas)) == 1, "as respostas de negacao diferem entre si"


# ---------------------------------------------------------------------------
# Idempotencia — a garantia central
# ---------------------------------------------------------------------------


def test_mesma_chave_nao_publica_duas_vezes(no_receptor):
    """O cenario classico: o site grava e responde 201, a resposta se perde na
    rede, e o PubliBot repete. Sem idempotencia, o mesmo conteudo e publicado
    duas vezes — exatamente o problema que o produto existe para evitar."""
    from publibot_node.models import ReceivedPublication

    chave = str(uuid.uuid4())

    primeira, _ = _publicar(no_receptor, PAYLOAD, chave_idem=chave)
    assert primeira.status_code == 201
    assert primeira.json()["status"] == "success"

    segunda, _ = _publicar(no_receptor, PAYLOAD, chave_idem=chave)
    assert segunda.status_code == 200, "chave repetida deve devolver 200, nao criar outro"
    assert segunda.json()["status"] == "already_exists"

    assert primeira.json()["remote_id"] == segunda.json()["remote_id"]
    assert ReceivedPublication.objects.filter(idempotency_key=chave).count() == 1


def test_chaves_diferentes_criam_publicacoes_diferentes(no_receptor):
    primeira, _ = _publicar(no_receptor, PAYLOAD)
    segunda, _ = _publicar(no_receptor, {**PAYLOAD, "title": "Outro artigo"})

    assert primeira.status_code == 201
    assert segunda.status_code == 201
    assert primeira.json()["remote_id"] != segunda.json()["remote_id"]


def test_sem_chave_de_idempotencia_e_recusado(no_receptor):
    corpo = json.dumps(PAYLOAD).encode()
    resposta = no_receptor.post(
        "/api/v1/publish/", data=corpo, content_type="application/json", **_assinar(corpo)
    )
    assert resposta.status_code == 400
    assert resposta.json()["error"]["code"] == "invalid_payload"


def test_reconciliacao_encontra_publicacao_anterior(no_receptor):
    """Chamado antes de repetir apos timeout: o conteudo pode ter sido gravado
    e apenas a resposta ter se perdido."""
    chave = str(uuid.uuid4())
    _publicar(no_receptor, PAYLOAD, chave_idem=chave)

    cabecalhos = _assinar(b"")
    resposta = no_receptor.get(f"/api/v1/publications/?idempotency_key={chave}", **cabecalhos)

    assert resposta.status_code == 200
    resultados = resposta.json()["results"]
    assert len(resultados) == 1
    assert resultados[0]["idempotency_key"] == chave


# ---------------------------------------------------------------------------
# Sanitizacao no lado receptor
# ---------------------------------------------------------------------------


def test_script_no_conteudo_e_recusado_com_422(no_receptor):
    """Quem grava e o responsavel final pelo que sai na propria pagina: um
    cliente nao deve depender da correcao de um sistema de terceiro para nao
    servir script aos proprios visitantes."""
    malicioso = {**PAYLOAD, "html_content": "<p>ok</p><script>roubar()</script>"}
    resposta, _ = _publicar(no_receptor, malicioso)

    assert resposta.status_code == 422
    assert resposta.json()["error"]["code"] == "content_rejected"


def test_atributo_de_evento_e_removido(no_receptor):
    from publibot_node.models import ReceivedPublication

    com_evento = {**PAYLOAD, "html_content": '<p onclick="x()">texto</p>'}
    resposta, chave = _publicar(no_receptor, com_evento)

    assert resposta.status_code == 201
    gravado = ReceivedPublication.objects.get(idempotency_key=chave)
    assert "onclick" not in gravado.html_content
    assert "texto" in gravado.html_content


def test_titulo_tambem_e_sanitizado(no_receptor):
    from publibot_node.models import ReceivedPublication

    _, chave = _publicar(no_receptor, {**PAYLOAD, "title": "<b>Titulo</b> com marcacao"})
    gravado = ReceivedPublication.objects.get(idempotency_key=chave)
    assert "<b>" not in gravado.title


# ---------------------------------------------------------------------------
# Demais rotas
# ---------------------------------------------------------------------------


def test_health_declara_versao_e_recursos(no_receptor):
    resposta = no_receptor.get("/api/v1/health/", **_assinar(b""))
    assert resposta.status_code == 200

    dados = resposta.json()
    assert "v1" in dados["contract_versions"]
    assert "idempotency" in dados["capabilities"]
    assert "hmac_signature" in dados["capabilities"]


def test_seo_context_lista_o_que_ja_foi_publicado(no_receptor):
    _publicar(no_receptor, PAYLOAD)
    resposta = no_receptor.get("/api/v1/seo-context/", **_assinar(b""))
    assert resposta.status_code == 200

    dados = resposta.json()
    assert dados["site_title"] == "Site de teste"
    assert len(dados["published_posts"]) == 1
    assert dados["published_posts"][0]["title"] == PAYLOAD["title"]


def test_perguntas_confirmadas_nao_voltam(no_receptor):
    """A unica coisa que remove uma pergunta do estado pendente e a publicacao,
    que so acontece apos revisao humana. Sem confirmacao, cada ciclo
    reimportaria as mesmas perguntas e geraria a mesma resposta repetidamente."""
    from publibot_node.models import VisitorQuestion

    q1 = VisitorQuestion.objects.create(question_text="Primeira duvida")
    VisitorQuestion.objects.create(question_text="Segunda duvida")

    primeira = no_receptor.get("/api/v1/pending-questions/", **_assinar(b""))
    assert len(primeira.json()["pending_questions"]) == 2

    corpo = json.dumps({"ids": [str(q1.id)]}).encode()
    ack = no_receptor.post(
        "/api/v1/pending-questions/ack/",
        data=corpo,
        content_type="application/json",
        **_assinar(corpo),
    )
    assert ack.json()["acknowledged"] == 1

    segunda = no_receptor.get("/api/v1/pending-questions/", **_assinar(b""))
    restantes = segunda.json()["pending_questions"]
    assert len(restantes) == 1
    assert restantes[0]["question_text"] == "Segunda duvida"


def test_nome_do_visitante_so_sai_com_consentimento(no_receptor):
    """O nome nao e necessario para produzir o conteudo."""
    from publibot_node.models import VisitorQuestion

    VisitorQuestion.objects.create(question_text="Duvida", author_name="Joao Silva")

    resposta = no_receptor.get("/api/v1/pending-questions/", **_assinar(b""))
    pergunta = resposta.json()["pending_questions"][0]

    assert pergunta["author_name"] == "", "nome sem consentimento nao deve ser enviado"
    assert pergunta["question_text"] == "Duvida"
