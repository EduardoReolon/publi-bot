"""Reserva de capacidade nas conexoes de inferencia.

Este modulo existe por causa de um modo de falha silencioso do hardware alvo:
numa placa de 8 GB, duas inferencias simultaneas estouram a VRAM e o Ollama
**cai para CPU sem emitir erro**. A geracao passa de dezenas de tokens por
segundo para poucos, e nada indica o motivo. Serializar nao e ajuste de
desempenho, e requisito de corretude.

A contagem de reservas fica no banco, e nao no Redis, por dois motivos: o
estado sobrevive a um restart do cache, e a reserva pode ser conferida na mesma
transacao que atualiza o trabalho — sem janela entre "reservei" e "registrei
que reservei".
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.inference.models import InferenceConnection, InferenceLease


class SemCapacidade(RuntimeError):
    """Nenhuma vaga disponivel agora.

    Nao e erro: e o sinal para o trabalho voltar ao banco e tentar depois, em
    vez de segurar um processo esperando.
    """


def _leases_ativas(connection: InferenceConnection):
    agora = timezone.now()
    return InferenceLease.objects.filter(
        connection=connection, released_at__isnull=True, expires_at__gt=agora
    )


def vizinhas_de_hardware(connection: InferenceConnection) -> list[InferenceConnection]:
    """As conexoes ativas que apontam para a MESMA maquina, incluindo esta.

    O Ollama em `:11434` e o Docling em `:8100` sao duas linhas no banco e uma
    placa so. Contar reservas por conexao deixaria as duas ativas ao mesmo
    tempo — uma gerando texto, outra convertendo PDF — e numa placa de 8 GB
    isso estoura a VRAM.

    O que acontece entao nao e um erro: o processo cai para CPU em silencio, o
    trabalho continua e fica dezenas de vezes mais lento. Nao ha log, nao ha
    excecao, nao ha nada no painel. O sintoma e alguem dizer "hoje esta lento".
    """
    if not connection.maquina:
        return [connection]

    return [
        candidata
        for candidata in InferenceConnection.objects.filter(is_active=True)
        if candidata.maquina == connection.maquina
    ]


def _capacidade_da_maquina(vizinhas: list[InferenceConnection]) -> int:
    """Quantas execucoes simultaneas a maquina inteira aguenta.

    O MENOR limite entre as conexoes que a dividem, e nao a soma. Cada uma
    declara quanto aquele hardware suporta; a mais conservadora e a que sabe de
    alguma restricao que as outras nao sabem, e somar limites seria concluir
    que duas conexoes de 1 aguentam 2 — a conclusao exata que o comentario de
    `max_concurrency` desaconselha.
    """
    return min(candidata.max_concurrency for candidata in vizinhas)


def liberar_expiradas(connection: InferenceConnection | None = None) -> int:
    """Fecha reservas que passaram do prazo.

    Sem isto, a queda de um worker segurando uma reserva deixaria a conexao
    inutilizavel para sempre.
    """
    filtro = Q(released_at__isnull=True, expires_at__lte=timezone.now())
    if connection is not None:
        filtro &= Q(connection=connection)
    return InferenceLease.objects.filter(filtro).update(released_at=timezone.now())


def adquirir(
    connection: InferenceConnection, *, owner_key: str, model_name: str = ""
) -> InferenceLease:
    """Toma uma vaga, ou levanta `SemCapacidade`."""
    if not connection.is_active:
        raise SemCapacidade(f"conexao {connection.name!r} esta inativa")
    if connection.circuito_aberto:
        raise SemCapacidade(
            f"circuito aberto para {connection.name!r} ate {connection.circuit_open_until}"
        )

    with transaction.atomic():
        # `select_for_update` na conexao serializa os candidatos: sem o
        # bloqueio, duas transacoes contariam as mesmas reservas ativas e as
        # duas concluiriam que ha vaga.
        travada = InferenceConnection.objects.select_for_update().get(pk=connection.pk)

        liberar_expiradas(travada)

        # A conta e por MAQUINA, nao por conexao. Duas conexoes que apontam
        # para o mesmo host dividem a mesma placa (ver `vizinhas_de_hardware`).
        vizinhas = vizinhas_de_hardware(travada)
        liberar_expiradas()
        capacidade = _capacidade_da_maquina(vizinhas)

        agora = timezone.now()
        ocupadas = InferenceLease.objects.filter(
            connection__in=vizinhas, released_at__isnull=True, expires_at__gt=agora
        ).count()

        if ocupadas >= capacidade:
            if len(vizinhas) > 1:
                nomes = ", ".join(sorted(c.name for c in vizinhas if c.pk != travada.pk))
                raise SemCapacidade(
                    f"a maquina {travada.maquina!r} ja esta com {capacidade} "
                    f"execucao(oes) simultanea(s). Ela e dividida com: {nomes}."
                )
            raise SemCapacidade(
                f"{travada.name!r} ja esta com {capacidade} execucao(oes) simultanea(s)"
            )

        return InferenceLease.objects.create(
            connection=travada,
            owner_key=owner_key,
            model_name=model_name,
            expires_at=timezone.now() + timedelta(seconds=travada.lease_seconds),
        )


def liberar(lease: InferenceLease) -> None:
    InferenceLease.objects.filter(pk=lease.pk, released_at__isnull=True).update(
        released_at=timezone.now()
    )


@contextmanager
def reserva(connection: InferenceConnection, *, owner_key: str, model_name: str = ""):
    """Uso normal: `with reserva(conexao, owner_key=...) as lease:`.

    A liberacao acontece mesmo se a chamada levantar excecao — sem isso, uma
    falha de rede deixaria a vaga ocupada ate a expiracao.
    """
    lease = adquirir(connection, owner_key=owner_key, model_name=model_name)
    try:
        yield lease
    finally:
        liberar(lease)


def escolher_conexao(
    *, workload: str, tenant=None, model_name: str = ""
) -> InferenceConnection | None:
    """Escolhe a melhor conexao disponivel para uma carga de trabalho.

    Ordem de preferencia:

    1. Conexao exclusiva do tenant, se houver — o cliente que traz a propria
       chave nao deve disputar capacidade com os demais.
    2. Entre as elegiveis, a que **ja esta com o modelo pedido carregado**.
       Trocar de modelo na VRAM custa de 10 a 60 segundos; agrupar trabalho do
       mesmo modelo evita pagar isso a cada tarefa.
    3. A que tiver mais folga.
    """
    candidatas = list(
        InferenceConnection.objects.filter(is_active=True).filter(
            Q(tenant=tenant) | Q(tenant__isnull=True)
        )
    )
    candidatas = [c for c in candidatas if c.atende(workload) and not c.circuito_aberto]
    if not candidatas:
        return None

    liberar_expiradas()

    def folga(c: InferenceConnection) -> int:
        """A vaga que sobra na MAQUINA, nao na conexao.

        Precisa ser a mesma conta de `adquirir`. Enquanto esta funcao contava
        por conexao e aquela por maquina, a escolha entregava uma conexao que
        a reserva ia recusar em seguida — o trabalho fazia a viagem inteira
        para levar `SemCapacidade` e ser adiado, duas vezes por tentativa.
        """
        vizinhas = vizinhas_de_hardware(c)
        agora = timezone.now()
        ocupadas = InferenceLease.objects.filter(
            connection__in=vizinhas, released_at__isnull=True, expires_at__gt=agora
        ).count()
        return _capacidade_da_maquina(vizinhas) - ocupadas

    com_vaga = [c for c in candidatas if folga(c) > 0]
    if not com_vaga:
        return None

    def prioridade(c: InferenceConnection) -> tuple:
        exclusiva = 0 if c.tenant_id else 1
        # Menor e melhor: conexao ja com o modelo carregado vem primeiro.
        modelo_carregado = (
            0 if model_name and _leases_ativas(c).filter(model_name=model_name).exists() else 1
        )
        return (exclusiva, modelo_carregado, -folga(c))

    return sorted(com_vaga, key=prioridade)[0]


def descrever_ocupacao(*, workload: str = "", tenant=None) -> str:
    """Quem esta segurando a capacidade agora, em uma frase.

    "Todas as conexoes estao ocupadas" e verdadeiro e inutil: nao diz qual
    conexao, nem desde quando, nem se alguem ainda esta do outro lado. Um
    processo que morreu segurando a reserva produz exatamente a mesma frase de
    uma inferencia saudavel em curso, e a diferenca entre as duas e a diferenca
    entre esperar e agir.
    """
    agora = timezone.now()
    ativas = (
        InferenceLease.objects.filter(released_at__isnull=True, expires_at__gt=agora)
        .select_related("connection")
        .order_by("acquired_at")
    )

    partes = []
    for lease in ativas:
        minutos = int((agora - lease.acquired_at).total_seconds() // 60)
        partes.append(f"{lease.connection.name!r} ha {minutos} min")

    if not partes:
        # Sem reserva ativa e sem vaga: sobrou o disjuntor.
        return (
            "nenhuma conexao esta disponivel. Nao ha reserva em curso, entao o "
            "motivo e o disjuntor aberto apos falhas seguidas — confira em "
            "Configuracao > Inferencia."
        )

    return (
        "todas as conexoes de inferencia estao ocupadas: "
        + "; ".join(partes)
        + ". Se algum desses processos ja morreu, solte com "
        "`manage.py reservas --liberar`."
    )


def registrar_falha(connection: InferenceConnection, *, limite: int = 5, minutos: int = 15) -> None:
    """Conta a falha e abre o circuito ao atingir o limite.

    O disjuntor evita que uma conexao fora do ar consuma tentativa apos
    tentativa de todos os trabalhos da fila.
    """
    connection.consecutive_failures += 1
    campos = ["consecutive_failures", "health_status"]
    connection.health_status = InferenceConnection.Health.DEGRADED

    if connection.consecutive_failures >= limite:
        connection.circuit_open_until = timezone.now() + timedelta(minutes=minutos)
        connection.health_status = InferenceConnection.Health.DOWN
        campos.append("circuit_open_until")

    connection.save(update_fields=campos)


def registrar_sucesso(connection: InferenceConnection) -> None:
    InferenceConnection.objects.filter(pk=connection.pk).update(
        consecutive_failures=0,
        circuit_open_until=None,
        health_status=InferenceConnection.Health.HEALTHY,
        last_success_at=timezone.now(),
    )


def gerar_owner_key() -> str:
    return str(uuid.uuid4())
