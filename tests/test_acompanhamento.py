"""Tarefas avulsas (as das redes) na tela de Operacao: aparecem desde o
clique e mostram o andamento; o varredor do orquestrador nao as toca."""

from __future__ import annotations

import pytest
from celery.exceptions import Retry
from django_tenants.utils import schema_context

from apps.ops import acompanhamento
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import jobs_para_retomar


class TarefaFalsa:
    name = "apps.social.tasks.gerar_video"

    def __init__(self):
        self.chamadas = []

    def delay(self, *args, **kwargs):
        self.chamadas.append((args, kwargs))


@pytest.mark.django_db
def test_na_fila_registra_e_despacha_com_o_id(tenant_factory):
    tenant_factory("cliente_a")
    with schema_context("cliente_a"):
        tarefa = TarefaFalsa()
        trabalho = acompanhamento.na_fila(tarefa, "post-1", apenas=2, rotulo="Video do post")
        job = GenerationJob.objects.get(pk=trabalho)
        assert job.kind == "tarefa" and job.status == "pending" and job.rotulo == "Video do post"
        assert tarefa.chamadas == [(("post-1",), {"apenas": 2, "trabalho": trabalho})]
        assert job not in jobs_para_retomar()  # o varredor nao mexe nela


@pytest.mark.django_db
def test_acompanhar_marca_cada_desfecho(tenant_factory):
    tenant_factory("cliente_b")
    with schema_context("cliente_b"):

        def novo():
            return acompanhamento.na_fila(TarefaFalsa(), "x", rotulo="Teste")

        ok = novo()
        with acompanhamento.acompanhar(ok):
            assert GenerationJob.objects.get(pk=ok).status == "running"
        assert GenerationJob.objects.get(pk=ok).status == "done"

        espera = novo()
        with pytest.raises(Retry), acompanhamento.acompanhar(espera):
            raise Retry("placa ocupada", when=600)
        job = GenerationJob.objects.get(pk=espera)
        assert job.status == "waiting_capacity" and job.next_attempt_at is not None

        quebrou = novo()
        with pytest.raises(ValueError), acompanhamento.acompanhar(quebrou):
            raise ValueError("chave errada")
        assert "chave errada" in GenerationJob.objects.get(pk=quebrou).last_error

        aviso = novo()
        with acompanhamento.acompanhar(aviso) as andamento:
            andamento.falhou("video nao gerado: sem voz")
        job = GenerationJob.objects.get(pk=aviso)
        assert job.status == "failed" and job.last_error == "video nao gerado: sem voz"

        with acompanhamento.acompanhar(None):  # sem registro: nao faz nada
            pass


@pytest.mark.django_db
def test_redespachar_manda_a_mesma_tarefa(tenant_factory, monkeypatch):
    tenant_factory("cliente_c")
    with schema_context("cliente_c"):
        from celery import current_app

        enviadas = []
        monkeypatch.setattr(
            current_app,
            "send_task",
            lambda nome, args, kwargs: enviadas.append((nome, args, kwargs)),
        )
        trabalho = acompanhamento.na_fila(TarefaFalsa(), "post-9", rotulo="Video do post")
        GenerationJob.objects.filter(pk=trabalho).update(status="failed", last_error="x")
        acompanhamento.redespachar(GenerationJob.objects.get(pk=trabalho))
        assert enviadas == [("apps.social.tasks.gerar_video", ["post-9"], {"trabalho": trabalho})]
        assert GenerationJob.objects.get(pk=trabalho).status == "pending"
