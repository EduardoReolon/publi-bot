"""As paginas que as redes exigem (privacidade, termos, exclusao), o callback
de exclusao de dados da Meta e o retorno unico das conexoes OAuth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django_tenants.utils import schema_context

from apps.accounts.models import PedidoDeExclusao

U = "core.urls_public"
SEGREDO = "segredo-do-app"


def _signed_request(dados: dict, segredo: str = SEGREDO) -> str:
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    carga = b64(json.dumps(dados).encode())
    assinatura = hmac.new(segredo.encode(), carga.encode(), hashlib.sha256).digest()
    return f"{b64(assinatura)}.{carga}"


@pytest.fixture
def raiz(client):
    client.defaults["HTTP_HOST"] = settings.ROOT_DOMAIN
    return client


@pytest.mark.django_db
def test_paginas_publicas_com_o_responsavel(raiz, public_tenant, settings):
    settings.OPERADOR_NOME = "Ekron Consultoria"
    settings.PRIVACIDADE_EMAIL = "privacidade@ekron.ia.br"
    privacidade = raiz.get(reverse("accounts:privacidade", urlconf=U)).content.decode()
    assert "Ekron Consultoria" in privacidade and "privacidade@ekron.ia.br" in privacidade
    assert "Uso Limitado" in privacidade  # exigencia do Google
    assert "Termos de serviço" in raiz.get(reverse("accounts:termos", urlconf=U)).content.decode()
    exclusao = raiz.get(reverse("accounts:exclusao_de_dados", urlconf=U)).content.decode()
    assert "Como pedir" in exclusao
    assert "Politica de privacidade" in raiz.get("/").content.decode()  # rodape da inicial


@pytest.mark.django_db
def test_meta_pede_exclusao_e_cada_cliente_apaga(raiz, tenant_factory, settings):
    from apps.social.models import Comentario, Destino, Post

    settings.SOCIAL_META_APP_SECRET = SEGREDO
    tenant = tenant_factory("cliente_exclusao")
    with schema_context(tenant.schema_name):
        destino = Destino.objects.create(
            rede="instagram", nome="Insta", conta_id="178", usuario_remoto="999"
        )
        destino.gravar_credenciais({"access_token": "tok"})
        destino.save()
        antigo = Post.objects.create(
            destino=destino, motivo=Post.Motivo.HISTORICO, situacao=Post.Situacao.PUBLICADO
        )
        Comentario.objects.create(post=antigo, id_remoto="c1", texto="oi")
        nosso = Post.objects.create(destino=destino, motivo=Post.Motivo.NOVO)
    connection.set_schema_to_public()

    url = reverse("accounts:meta_exclusao", urlconf=U)
    falso = raiz.post(url, {"signed_request": _signed_request({"user_id": "999"}, "outro")})
    assert falso.status_code == 400

    pedido = {"algorithm": "HMAC-SHA256", "user_id": "999", "issued_at": 1}
    resposta = raiz.post(url, {"signed_request": _signed_request(pedido)})
    assert resposta.status_code == 200
    corpo = resposta.json()
    registro = PedidoDeExclusao.objects.get(codigo=corpo["confirmation_code"])
    assert registro.situacao == PedidoDeExclusao.Situacao.FEITO
    assert corpo["url"].endswith(f"?codigo={registro.codigo}")

    with schema_context(tenant.schema_name):
        destino.refresh_from_db()
        assert not destino.conectado and destino.usuario_remoto == ""
        assert not Post.objects.filter(pk=antigo.pk).exists()
        assert not Comentario.objects.exists()
        assert Post.objects.filter(pk=nosso.pk).exists()  # o que o cliente escreveu fica
    connection.set_schema_to_public()

    status = raiz.get(reverse("accounts:exclusao_de_dados", urlconf=U), {"codigo": registro.codigo})
    assert "Concluido" in status.content.decode()


@pytest.mark.django_db
def test_retorno_unico_segue_para_o_cliente(raiz, tenant_factory):
    from core.retorno_oauth import assinar, endereco_de_retorno

    tenant = tenant_factory("cliente_retorno")
    connection.set_schema_to_public()
    assert endereco_de_retorno().endswith(f"{settings.ROOT_DOMAIN}/redes/retorno/")
    estado = assinar(tenant.schema_name, "/redes/conectar/retorno/", destino="x")
    resposta = raiz.get("/redes/retorno/", {"state": estado, "code": "abc"})
    assert resposta.status_code == 302
    destino = resposta["Location"]
    assert destino.startswith(f"https://cliente-retorno.{settings.ROOT_DOMAIN}/redes/conectar/")
    assert "code=abc" in destino and "state=" in destino

    forjado = raiz.get("/redes/retorno/", {"state": "x", "code": "abc"})
    assert forjado.status_code == 302 and "cliente-retorno" not in forjado["Location"]
