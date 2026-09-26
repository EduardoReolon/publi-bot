"""Search Console: acesso por conta de servico, retratos e o "quase la".

* o token sai de um JWT assinado com a chave da conta de servico — conferido
  aqui com a chave publica de verdade, e nao so "tem tres partes";
* sem acesso a propriedade, a mensagem diz qual e-mail adicionar;
* o retrato para 3 dias antes de hoje (os ultimos dias sao provisorios);
* "quase la" (posicao 8 a 20, com impressoes) vira sinal com volume = impressoes;
* o artigo publicado e comparado com ele mesmo no retrato anterior.
"""

from __future__ import annotations

import base64
import datetime
import json
import re

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.radar import search_console
from apps.radar.models import ColetaDoConsole, ConfiguracaoDoRadar, LinhaDoConsole, SinalDeDemanda
from tests.test_interface import ambiente  # noqa: F401


@pytest.fixture
def conta(tmp_path, settings):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = chave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    arquivo = tmp_path / "conta.json"
    arquivo.write_text(
        json.dumps(
            {
                "client_email": "publibot@projeto.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    settings.GSC_CONTA_DE_SERVICO_ARQUIVO = str(arquivo)
    search_console.conta_de_servico.cache_clear()
    search_console._token.update(valor="", expira=0.0)
    yield chave.public_key()
    search_console.conta_de_servico.cache_clear()
    search_console._token.update(valor="", expira=0.0)


@pytest.fixture
def tenant(tenant_factory):
    t = tenant_factory("console")
    with schema_context(t.schema_name):
        config = ConfiguracaoDoRadar.carregar()
        config.propriedade_search_console = "sc-domain:exemplo.com.br"
        config.save()
        yield t


LINHAS = [
    {
        "keys": ["como calcular bdi", "https://exemplo.com.br/bdi"],
        "clicks": 3,
        "impressions": 400,
        "ctr": 0.0075,
        "position": 11.2,
    },
    {
        "keys": ["bdi", "https://exemplo.com.br/bdi"],
        "clicks": 30,
        "impressions": 900,
        "ctr": 0.03,
        "position": 4.1,
    },
    {
        "keys": ["planilha orcamento", "https://exemplo.com.br/"],
        "clicks": 0,
        "impressions": 5,
        "ctr": 0,
        "position": 15.0,
    },
]


def _google_responde(monkeypatch, *, linhas=LINHAS, status_consulta=200, pedidos=None):
    def post(url, data=None, json=None, headers=None, timeout=None):
        if pedidos is not None:
            pedidos.append({"url": url, "data": data, "json": json, "headers": headers})
        if "oauth2" in url:
            corpo = {"access_token": "token-de-acesso", "expires_in": 3600}
            return httpx.Response(200, json=corpo, request=httpx.Request("POST", url))
        return httpx.Response(
            status_consulta, json={"rows": linhas}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)


def _decodificar(parte: str) -> bytes:
    return base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4))


@pytest.mark.django_db
def test_o_token_sai_de_um_jwt_assinado_de_verdade(conta, tenant, monkeypatch):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    pedidos = []
    _google_responde(monkeypatch, pedidos=pedidos)

    search_console.coletar()

    assinatura_jwt = pedidos[0]["data"]["assertion"]
    cabecalho, corpo, assinatura = assinatura_jwt.split(".")
    conta.verify(
        _decodificar(assinatura),
        f"{cabecalho}.{corpo}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    reivindicacoes = json.loads(_decodificar(corpo))
    assert reivindicacoes["iss"] == "publibot@projeto.iam.gserviceaccount.com"
    assert reivindicacoes["scope"].endswith("webmasters.readonly")
    assert pedidos[1]["headers"]["Authorization"] == "Bearer token-de-acesso"
    assert "sc-domain%3Aexemplo.com.br" in pedidos[1]["url"]


@pytest.mark.django_db
def test_retrato_para_tres_dias_antes_de_hoje(conta, tenant, monkeypatch):
    pedidos = []
    _google_responde(monkeypatch, pedidos=pedidos)

    coleta = search_console.coletar()

    assert coleta.fim == timezone.localdate() - datetime.timedelta(days=3)
    assert (coleta.fim - coleta.inicio).days == 27
    assert pedidos[1]["json"]["dimensions"] == ["query", "page"]
    assert coleta.linhas_set.count() == 3


@pytest.mark.django_db
def test_sem_acesso_diz_qual_email_adicionar(conta, tenant, monkeypatch):
    from apps.radar.provedores import ProvedorIndisponivel

    _google_responde(monkeypatch, status_consulta=403)

    with pytest.raises(
        ProvedorIndisponivel, match=re.escape("publibot@projeto.iam.gserviceaccount.com")
    ):
        search_console.coletar()


@pytest.mark.django_db
def test_sem_conta_de_servico_a_integracao_fica_desligada(tenant, settings):
    from apps.radar.provedores import ProvedorIndisponivel

    settings.GSC_CONTA_DE_SERVICO_ARQUIVO = ""
    search_console.conta_de_servico.cache_clear()

    assert search_console.email_da_conta() == ""
    with pytest.raises(ProvedorIndisponivel, match="GSC_CONTA_DE_SERVICO_ARQUIVO"):
        search_console.coletar()


@pytest.mark.django_db
def test_quase_la_vira_sinal_com_as_impressoes_como_volume(conta, tenant, monkeypatch):
    _google_responde(monkeypatch)

    sinais = search_console.colher_sinais(rodada=None)

    # Posicao 11,2 com 400 impressoes entra; a de posicao 4 esta na frente, e
    # a de 5 impressoes e ruido.
    assert [s.texto for s in sinais] == ["como calcular bdi"]
    assert sinais[0].volume == 400
    assert sinais[0].fonte == SinalDeDemanda.Fonte.SEARCH_CONSOLE
    assert sinais[0].extra["posicao"] == 11.2


@pytest.mark.django_db
def test_artigo_publicado_e_comparado_com_o_retrato_anterior(conta, tenant):
    from apps.content.models import Article

    artigo = Article.objects.create(
        title="Como calcular o BDI",
        published_url="https://exemplo.com.br/bdi",
        published_at=timezone.now(),
    )
    for dias_atras, posicao in ((30, 14.0), (0, 9.5)):
        coleta = ColetaDoConsole.objects.create(
            propriedade="sc-domain:exemplo.com.br",
            inicio=timezone.localdate(),
            fim=timezone.localdate(),
            coletada_em=timezone.now() - datetime.timedelta(days=dias_atras),
        )
        LinhaDoConsole.objects.create(
            coleta=coleta,
            consulta="bdi",
            pagina=artigo.published_url,
            cliques=5,
            impressoes=100,
            posicao=posicao,
        )

    [item] = search_console.desempenho_dos_artigos()

    assert item["artigo"] == artigo
    assert item["posicao"] == pytest.approx(9.5)
    assert item["posicao_anterior"] == pytest.approx(14.0)


@pytest.mark.django_db
def test_tela_mostra_o_email_e_coleta(ambiente, conta, monkeypatch):  # noqa: F811
    _, _, client = ambiente
    config = ConfiguracaoDoRadar.carregar()
    config.propriedade_search_console = "sc-domain:exemplo.com.br"
    config.save()
    _google_responde(monkeypatch)

    pagina = client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    assert "publibot@projeto.iam.gserviceaccount.com" in pagina

    client.post(reverse("radar:coletar_console", urlconf="core.urls_tenants"))
    assert ColetaDoConsole.objects.count() == 1
    assert (
        "como calcular bdi"
        in client.get(reverse("radar:radar", urlconf="core.urls_tenants")).content.decode()
    )
