"""Testes da retomada de trabalhos de varios passos.

A especificacao original previa retomada via `chain()` do Celery, o que nao
funciona: a cadeia viaja no cabecalho da mensagem e, se um elo morre, o
restante e descartado sem mecanismo de retomada — alem de nao ser
inspecionavel, entao o painel nao consegue mostrar "parou no passo 3 de 4".

Aqui o banco e a fonte da verdade. Estes testes verificam exatamente isso.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.ops.models import GenerationJob
from apps.ops.orchestrator import (
    Fluxo,
    Passo,
    PassoAdiado,
    avancar,
    criar_job,
    jobs_para_retomar,
    registrar_fluxo,
)


@pytest.fixture
def tenant_ops(tenant_factory):
    tenant = tenant_factory("operacao")
    with schema_context(tenant.schema_name):
        yield tenant


@pytest.fixture
def fluxo_de_tres_passos():
    """Fluxo de teste que registra quais passos executaram."""
    executados: list[int] = []

    def faz(numero: int):
        def executar(job):
            executados.append(numero)
            return {"passo": numero, "resultado": f"saida-{numero}"}

        return executar

    registrar_fluxo(
        Fluxo(
            kind=GenerationJob.Kind.PILLAR_ARTICLE,
            passos=[Passo(numero=i, nome=f"passo-{i}", executar=faz(i)) for i in range(3)],
        )
    )
    return executados


@pytest.mark.django_db
def test_job_avanca_um_passo_por_chamada(tenant_ops, fluxo_de_tres_passos):
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE)
    assert job.total_steps == 3

    assert avancar(job.pk) == GenerationJob.Status.PENDING
    job.refresh_from_db()
    assert job.current_step == 1
    assert fluxo_de_tres_passos == [0]

    avancar(job.pk)
    avancar(job.pk)
    job.refresh_from_db()

    assert job.status == GenerationJob.Status.DONE
    assert job.current_step == 3
    assert fluxo_de_tres_passos == [0, 1, 2]
    assert job.finished_at is not None


@pytest.mark.django_db
def test_resultado_de_cada_passo_fica_no_banco(tenant_ops, fluxo_de_tres_passos):
    """No banco e nao no Redis: o Redis nao persiste por padrao, descarta
    chaves por evicao em silencio, e o que vive so nele nao esta em backup.
    Perder isso significa refazer inferencia ja paga."""
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE)
    avancar(job.pk)
    avancar(job.pk)
    job.refresh_from_db()

    assert job.resultado_do_passo(0) == {"passo": 0, "resultado": "saida-0"}
    assert job.resultado_do_passo(1) == {"passo": 1, "resultado": "saida-1"}
    assert job.resultado_do_passo(2) is None


@pytest.mark.django_db
def test_retomada_nao_reexecuta_passo_ja_concluido(tenant_ops, fluxo_de_tres_passos):
    """O cenario que a arquitetura promete: o worker cai no meio e o trabalho
    retoma do passo certo, sem desperdicar GPU no que ja foi feito."""
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE)
    avancar(job.pk)
    avancar(job.pk)
    assert fluxo_de_tres_passos == [0, 1]

    # Simula queda do worker no meio do passo 2: reserva presa, situacao
    # RUNNING, nada gravado.
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.RUNNING,
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )

    # O varredor encontra e redespacha.
    assert job.pk in [j.pk for j in jobs_para_retomar()]
    avancar(job.pk)

    job.refresh_from_db()
    assert job.status == GenerationJob.Status.DONE
    # Os passos 0 e 1 NAO foram refeitos.
    assert fluxo_de_tres_passos == [0, 1, 2]


@pytest.mark.django_db
def test_job_reservado_por_outra_execucao_e_ignorado(tenant_ops, fluxo_de_tres_passos):
    """Duas execucoes concorrentes nao podem avancar o mesmo trabalho — seria
    inferencia paga em dobro."""
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE)
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.RUNNING,
        lease_expires_at=timezone.now() + timedelta(hours=1),
    )

    assert avancar(job.pk) == "ignorado"
    assert fluxo_de_tres_passos == []


@pytest.mark.django_db
def test_passo_adiado_nao_gasta_tentativa(tenant_ops):
    """Esperar a GPU ligar nao e falha. Contar como tentativa esgotaria os
    retries de um trabalho que so estava aguardando capacidade."""

    def sem_capacidade(job):
        raise PassoAdiado("nenhuma conexao disponivel", tentar_em_segundos=600)

    registrar_fluxo(
        Fluxo(
            kind=GenerationJob.Kind.QA_ANSWER,
            passos=[Passo(numero=0, nome="precisa-de-gpu", executar=sem_capacidade)],
        )
    )
    job = criar_job(kind=GenerationJob.Kind.QA_ANSWER)

    assert avancar(job.pk) == GenerationJob.Status.WAITING_CAPACITY
    job.refresh_from_db()

    assert job.attempt_count == 0, "adiamento nao deve consumir tentativa"
    assert job.current_step == 0
    assert job.next_attempt_at > timezone.now()
    assert "nenhuma conexao" in job.last_error


@pytest.mark.django_db
def test_job_adiado_volta_para_o_varredor_quando_o_prazo_vence(tenant_ops):
    def sem_capacidade(job):
        raise PassoAdiado("aguardando", tentar_em_segundos=1)

    registrar_fluxo(
        Fluxo(
            kind=GenerationJob.Kind.QA_ANSWER,
            passos=[Passo(numero=0, nome="espera", executar=sem_capacidade)],
        )
    )
    job = criar_job(kind=GenerationJob.Kind.QA_ANSWER)
    avancar(job.pk)

    GenerationJob.objects.filter(pk=job.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    assert job.pk in [j.pk for j in jobs_para_retomar()]


@pytest.mark.django_db
def test_erro_de_verdade_marca_falha_e_guarda_a_causa(tenant_ops):
    def quebra(job):
        raise RuntimeError("o modelo devolveu lixo")

    registrar_fluxo(
        Fluxo(
            kind=GenerationJob.Kind.PDF_INGESTION,
            passos=[Passo(numero=0, nome="converte", executar=quebra)],
        )
    )
    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION)

    assert avancar(job.pk) == GenerationJob.Status.FAILED
    job.refresh_from_db()

    assert "o modelo devolveu lixo" in job.last_error
    assert job.finished_at is not None
    # Falha nao volta sozinha para o varredor.
    assert job.pk not in [j.pk for j in jobs_para_retomar()]


@pytest.mark.django_db
def test_job_concluido_nao_avanca_mais(tenant_ops, fluxo_de_tres_passos):
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE)
    for _ in range(3):
        avancar(job.pk)

    fluxo_de_tres_passos.clear()
    assert avancar(job.pk) == "ignorado"
    assert fluxo_de_tres_passos == []


@pytest.mark.django_db
def test_varredor_nunca_compara_por_igualdade_de_instante(tenant_ops):
    """Um atraso do varredor nao pode fazer um trabalho ser pulado para sempre.
    A comparacao e sempre `<=`, nunca `==`."""

    def nada(job):
        return {}

    registrar_fluxo(Fluxo(kind=GenerationJob.Kind.QA_ANSWER, passos=[Passo(0, "x", nada)]))
    job = criar_job(kind=GenerationJob.Kind.QA_ANSWER)
    GenerationJob.objects.filter(pk=job.pk).update(
        status=GenerationJob.Status.WAITING_CAPACITY,
        next_attempt_at=timezone.now() - timedelta(days=3),
    )

    assert job.pk in [j.pk for j in jobs_para_retomar()]
