"""Testes do que acontece quando ninguem consome a fila.

Vem de dois relatos reais, com o mesmo desfecho — a tela "criando ambiente..."
girando ate desistir, sem nenhum erro em lugar nenhum:

1. Broker fora do ar. O `.delay()` bloqueou 19,5s dentro do `on_commit` do
   cadastro e terminou em RuntimeError. O tenant ficou em "provisionando" para
   sempre, sem rastro da causa.

2. Broker de pe, worker nenhum. O despacho funcionou. A mensagem entrou na
   fila e ficou la. Nada falhou — e por isso nada apareceu.

O segundo caso e o mais traicoeiro: nao ha erro a capturar. A unica evidencia
e a fila continuar cheia enquanto o trabalho nao anda.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.accounts import views
from apps.accounts.models import Tenant, TenantMembership, User
from apps.accounts.tasks import despachar_provisionamento

URLCONF_PUBLICO = "core.urls_public"


@pytest.fixture
def tenant_provisionando(public_tenant) -> Tenant:
    return Tenant.objects.create(
        schema_name="em_espera",
        name="Em Espera",
        slug="em-espera",
        status=Tenant.Status.PROVISIONING,
    )


@pytest.fixture
def dono(db, tenant_provisionando) -> User:
    usuario = User.objects.create_user(
        email="dono@exemplo.com", password="uma-senha-longa-de-teste", full_name="Dono"
    )
    TenantMembership.objects.create(tenant=tenant_provisionando, user=usuario, is_active=True)
    return usuario


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_broker_fora_do_ar_grava_o_motivo_no_tenant(tenant_provisionando, monkeypatch):
    """Falhar no despacho nao pode virar 500 nem silencio.

    O `on_commit` roda depois do COMMIT: o tenant e o usuario ja existem, entao
    deixar a excecao subir apenas quebra a pagina de quem acabou de se
    cadastrar e abandona o registro em "provisionando".
    """
    from kombu.exceptions import OperationalError

    def explodir(*args, **kwargs):
        raise OperationalError("Error 111 connecting to 127.0.0.1:6379. Connection refused.")

    monkeypatch.setattr("apps.accounts.tasks.provision_tenant.delay", explodir)

    despachar_provisionamento(str(tenant_provisionando.pk), tenant_provisionando.schema_name)

    tenant_provisionando.refresh_from_db()
    assert tenant_provisionando.status == Tenant.Status.FAILED
    assert "Connection refused" in tenant_provisionando.provisioning_error


@pytest.mark.django_db
def test_provision_tenant_nao_pede_resultado():
    """`ignore_result` e o que impede o despacho de travar a request.

    Em `Celery.send_task`, `backend.on_task_call` so e chamado quando o
    resultado importa — e e ele que, com o Redis fora do ar, entra num laco de
    20 tentativas de 1s dentro da thread da request.
    """
    from apps.accounts.tasks import provision_tenant

    assert provision_tenant.ignore_result is True


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------
@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_fila_com_mensagem_parada_acusa_worker_ausente(tenant_provisionando, monkeypatch):
    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", lambda *a, **k: 1)

    texto = views._diagnosticar_provisionamento(tenant_provisionando)

    assert texto is not None
    assert "celery -A core worker" in texto


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_fila_vazia_nao_acusa_worker_ausente(tenant_provisionando, monkeypatch):
    """Fila vazia significa que ALGUEM consumiu.

    Culpar o worker aqui mandaria a pessoa para o terminal errado: o problema
    passa a ser lentidao ou queda no meio do trabalho, e a resposta esta no log
    do worker, nao na fila.
    """
    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", lambda *a, **k: 0)

    assert views._diagnosticar_provisionamento(tenant_provisionando) is None


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_fila_ilegivel_aponta_o_broker(tenant_provisionando, monkeypatch):
    """`None` e "nao sei", nunca "esta vazia" — a diferenca muda a resposta."""
    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", lambda *a, **k: None)

    texto = views._diagnosticar_provisionamento(tenant_provisionando)

    assert texto is not None
    assert "broker" in texto.lower()


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_fora_do_debug_o_diagnostico_nao_vai_para_a_tela(tenant_provisionando, monkeypatch):
    """A instrucao e para quem opera, nao para quem visita.

    Em producao ela vai para o log; dizer a um visitante qual processo esta
    faltando descreve a infraestrutura para quem nao deveria ve-la.
    """
    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", lambda *a, **k: 3)

    assert views._diagnosticar_provisionamento(tenant_provisionando) is None


# ---------------------------------------------------------------------------
# Endpoint de status
# ---------------------------------------------------------------------------
@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_status_so_diagnostica_quando_perguntado(client, dono, tenant_provisionando, monkeypatch):
    """Sem o parametro, nenhuma ida ao broker.

    A tela consulta a cada 1,5s; inspecionar a fila em toda consulta seria
    trabalho constante para responder algo que so faz sentido depois de a
    demora deixar de ser normal.
    """
    chamadas = []

    def espiao(*a, **k):
        chamadas.append(1)
        return 1

    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", espiao)
    client.force_login(dono)
    client.defaults["HTTP_HOST"] = settings.ROOT_DOMAIN
    url = reverse(
        "accounts:provisioning_status", args=[tenant_provisionando.slug], urlconf=URLCONF_PUBLICO
    )

    corpo = client.get(url).json()
    assert "diagnostico" not in corpo
    assert chamadas == []

    corpo = client.get(url + "?diagnostico=1").json()
    assert "celery -A core worker" in corpo["diagnostico"]
    assert len(chamadas) == 1


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_tenant_ja_ativo_nao_diagnostica(client, dono, tenant_provisionando, monkeypatch):
    """Um tenant pronto nao tem o que diagnosticar, mesmo com fila cheia.

    A fila pode ter mensagens de outro trabalho qualquer; nao ha por que
    apontar um problema para quem ja pode entrar.
    """
    monkeypatch.setattr("apps.ops.broker.mensagens_pendentes", lambda *a, **k: 5)
    Tenant.objects.filter(pk=tenant_provisionando.pk).update(status=Tenant.Status.ACTIVE)

    client.force_login(dono)
    client.defaults["HTTP_HOST"] = settings.ROOT_DOMAIN
    url = reverse(
        "accounts:provisioning_status", args=[tenant_provisionando.slug], urlconf=URLCONF_PUBLICO
    )

    corpo = client.get(url + "?diagnostico=1").json()
    assert corpo["pronto"] is True
    assert "diagnostico" not in corpo
