"""Testes do contrato com os sites externos.

Concentrados nas garantias cujo defeito so apareceria no site de um cliente:
idempotencia, assinatura, classificacao de erro e recusa de destino interno.
"""

from __future__ import annotations

import time

import httpx
import pytest
from django.core.exceptions import ValidationError
from django_tenants.utils import schema_context

from apps.inference.security import cifrar
from apps.integrations.errors import (
    SiteAuthError,
    SitePermanentError,
    SiteTransientError,
    classificar,
)
from apps.integrations.models import PublishAttempt, Site, SiteApiCall
from apps.integrations.signing import (
    JANELA_DE_TEMPO_SEGUNDOS,
    AssinaturaHttpx,
    assinar,
    conferir,
    impressao_da_chave,
)
from apps.integrations.validators import validar_url_de_site


@pytest.fixture
def tenant_integracoes(tenant_factory):
    tenant = tenant_factory("integracoes")
    with schema_context(tenant.schema_name):
        yield tenant


@pytest.fixture
def site(tenant_integracoes):
    return Site.objects.create(
        name="Site do cliente",
        slug="cliente",
        base_url="https://exemplo.com.br",
        api_key_ciphertext=cifrar("chave-de-api-do-site"),
        signing_secret_ciphertext=cifrar("segredo-de-assinatura"),
        default_author="Ana Enfermeira",
        default_author_credentials="COREN-SP 123456",
    )


# ---------------------------------------------------------------------------
# Recusa de destino interno (SSRF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://exemplo.com.br",  # sem TLS
        "https://localhost/api",
        "https://127.0.0.1/api",
        "https://10.0.0.5/api",
        "https://192.168.1.1/api",
        "https://169.254.169.254/",  # metadados de nuvem
        "https://[::1]/api",
    ],
)
def test_endereco_perigoso_e_recusado(url):
    """A URL vem de quem cadastra o site, e o servidor faz requisicoes para ela.
    Sem restricao, o proprio sistema viraria intermediario para alcancar a rede
    interna ou o servico de metadados da nuvem."""
    with pytest.raises(ValidationError):
        validar_url_de_site(url)


def test_endereco_publico_com_tls_e_aceito():
    validar_url_de_site("https://exemplo.com.br/blog")


def test_nome_que_nao_resolve_nao_bloqueia_cadastro():
    """Um DNS temporariamente fora do ar nao pode impedir o cadastro de um site
    legitimo."""
    validar_url_de_site("https://dominio-que-nao-existe-mesmo-12345.com.br")


# ---------------------------------------------------------------------------
# Assinatura
# ---------------------------------------------------------------------------


def test_assinatura_valida_e_aceita():
    agora = str(int(time.time()))
    corpo = b'{"titulo":"x"}'
    assinatura = assinar("segredo", agora, "nonce-1", corpo)
    assert conferir("segredo", agora, "nonce-1", corpo, assinatura)


def test_corpo_alterado_invalida_a_assinatura():
    """Um intermediario que altere o corpo trocaria o conteudo publicado sem
    que nada detectasse."""
    agora = str(int(time.time()))
    assinatura = assinar("segredo", agora, "n", b'{"titulo":"original"}')
    assert not conferir("segredo", agora, "n", b'{"titulo":"adulterado"}', assinatura)


def test_requisicao_antiga_e_recusada():
    """Sem instante na assinatura, uma requisicao capturada hoje continuaria
    valida daqui a um ano."""
    antigo = str(int(time.time()) - JANELA_DE_TEMPO_SEGUNDOS - 60)
    assinatura = assinar("segredo", antigo, "n", b"{}")
    assert not conferir("segredo", antigo, "n", b"{}", assinatura)


def test_segredo_errado_invalida():
    agora = str(int(time.time()))
    assinatura = assinar("segredo-certo", agora, "n", b"{}")
    assert not conferir("outro-segredo", agora, "n", b"{}", assinatura)


def test_assinatura_ausente_ou_malformada_nao_quebra():
    agora = str(int(time.time()))
    assert not conferir("segredo", agora, "n", b"{}", "")
    assert not conferir("segredo", "nao-e-numero", "n", b"{}", "v1=x")


def test_impressao_permite_localizar_sem_decifrar():
    assert impressao_da_chave("abc") == impressao_da_chave("abc")
    assert impressao_da_chave("abc") != impressao_da_chave("abd")
    assert "abc" not in impressao_da_chave("abc")


def test_auth_do_httpx_assina_toda_requisicao():
    """Implementado como httpx.Auth justamente para que nao exista requisicao
    esquecida sem assinatura."""
    auth = AssinaturaHttpx(api_key="chave", signing_secret="segredo")
    req = httpx.Request("POST", "https://exemplo.com.br/api/v1/publish/", json={"a": 1})
    assinada = next(auth.auth_flow(req))

    for cabecalho in ("X-API-KEY", "X-Timestamp", "X-Nonce", "X-Signature"):
        assert cabecalho in assinada.headers

    assert conferir(
        "segredo",
        assinada.headers["X-Timestamp"],
        assinada.headers["X-Nonce"],
        assinada.content,
        assinada.headers["X-Signature"],
    )


# ---------------------------------------------------------------------------
# Classificacao de erro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
def test_erro_retentavel(status):
    assert isinstance(classificar(status), SiteTransientError)


@pytest.mark.parametrize("status", [400, 404, 409, 413, 422])
def test_erro_terminal(status):
    """Um 400 por payload invalido seria retentado para sempre sem esta
    distincao."""
    erro = classificar(status)
    assert isinstance(erro, SitePermanentError)
    assert not isinstance(erro, SiteTransientError)


@pytest.mark.parametrize("status", [401, 403])
def test_credencial_recusada_pede_intervencao(status):
    """Uma chave rotacionada geraria tentativas infinitas em vez de alerta."""
    assert isinstance(classificar(status), SiteAuthError)


def test_retry_after_do_site_e_preservado():
    """O site sabe melhor que o SaaS quando volta a aceitar requisicoes."""
    erro = classificar(429, retry_after=120)
    assert erro.retry_after == 120


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_payload_inclui_autoria_e_divulgacao(site, user):
    """A especificacao original nao tinha campo de autor nenhum: os artigos
    sairiam sem assinatura, o pior formato possivel para conteudo tematico."""
    from django.utils import timezone

    from apps.content.models import Article
    from apps.integrations.publishing import montar_payload_de_artigo

    artigo = Article.objects.create(
        title="Titulo",
        slug="titulo",
        body_html="<p>x</p>",
        reviewed_by=user,
        reviewed_at=timezone.now(),
    )
    payload = montar_payload_de_artigo(artigo, site)

    assert payload["author"]["name"] == "Ana Enfermeira"
    assert payload["author"]["credentials"] == "COREN-SP 123456"
    assert payload["reviewed_by"]
    assert payload["reviewed_at"]
    assert "inteligencia artificial" in payload["content_disclosure"]
    assert "idempotency_key" in payload


@pytest.mark.django_db
def test_payload_nao_carrega_imagem_embutida(site):
    """Base64 infla o corpo em 33%, e o limite padrao do Nginx e 1 MB: o envio
    falharia com 413 antes de chegar a aplicacao, e o SaaS reenviaria megabytes
    indefinidamente interpretando como falha transitoria."""
    from apps.content.models import Article
    from apps.integrations.publishing import montar_payload_de_artigo

    artigo = Article.objects.create(title="T", body_html="<p>x</p>")
    payload = montar_payload_de_artigo(artigo, site)

    assert not any("base64" in chave for chave in payload)
    assert "cover_image_base64" not in payload


@pytest.mark.django_db
def test_resumo_guarda_digest_e_nao_o_corpo(site):
    from apps.integrations.publishing import resumir_payload

    resumo = resumir_payload({"title": "Titulo", "html_content": "<p>" + "x" * 10_000 + "</p>"})
    assert resumo["bytes"] > 10_000
    assert len(resumo["sha256"]) == 64
    assert "x" * 100 not in str(resumo)


# ---------------------------------------------------------------------------
# Simulacao e interruptores
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_modo_simulacao_registra_sem_enviar(site, settings, user):
    """Unico jeito seguro de conferir o contrato contra um site real durante o
    desenvolvimento."""
    from apps.content.models import Article
    from apps.integrations.publishing import publicar_artigo

    settings.PUBLISH_DRY_RUN = True
    artigo = Article.objects.create(
        title="T",
        body_html="<p>x</p>",
        author_name="Ana",
        status=Article.Status.APPROVED_SCHEDULED,
    )

    publicar_artigo(artigo, site)
    artigo.refresh_from_db()

    tentativa = PublishAttempt.objects.get(article=artigo)
    assert tentativa.dry_run is True
    assert tentativa.succeeded is True
    assert tentativa.payload_summary["bytes"] > 0
    # Nada saiu para a rede.
    assert SiteApiCall.objects.count() == 0
    assert artigo.status == Article.Status.APPROVED_SCHEDULED


@pytest.mark.django_db
def test_interruptor_geral_impede_envio(site, settings):
    from apps.integrations.client import SiteClient

    settings.PUBLISHING_ENABLED = False
    settings.PUBLISH_DRY_RUN = False

    with pytest.raises(SitePermanentError, match="desligada"):
        SiteClient(site).publish({}, idempotency_key="x")


@pytest.mark.django_db
def test_site_pausado_nao_recebe(site, settings):
    from apps.integrations.client import SiteClient

    settings.PUBLISHING_ENABLED = True
    settings.PUBLISH_DRY_RUN = False
    site.publishing_paused = True
    site.save()

    with pytest.raises(SitePermanentError, match="pausada"):
        SiteClient(site).publish({}, idempotency_key="x")


@pytest.mark.django_db
def test_artigo_nao_aprovado_nao_e_publicado(site, settings):
    from apps.content.models import Article
    from apps.integrations.publishing import PublicacaoBloqueada, publicar_artigo

    settings.PUBLISH_DRY_RUN = True
    artigo = Article.objects.create(title="T", status=Article.Status.DRAFTING)

    with pytest.raises(PublicacaoBloqueada):
        publicar_artigo(artigo, site)


# ---------------------------------------------------------------------------
# Recursos declarados pelo site
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_site_declara_recursos_suportados(site):
    """Sem esse aperto de mao, adicionar um campo obrigatorio quebraria todos os
    sites instalados, sem forma de saber qual versao cada um fala."""
    site.capabilities = ["idempotency", "hmac_signature", "cursor_pagination"]
    site.save()

    assert site.suporta("idempotency")
    assert not site.suporta("image_by_url")
