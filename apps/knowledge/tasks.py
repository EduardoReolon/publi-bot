"""Pontos de entrada da ingestao de documentos."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.knowledge.models import Document
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job
from apps.ops.tasks import advance_generation_job

logger = logging.getLogger("publibot.knowledge")


def iniciar_ingestao(document: Document) -> GenerationJob:
    """Coloca o documento na fila de conversao.

    Marcar como QUEUED antes de despachar e o que faz a lista de documentos
    dizer a verdade no intervalo entre o clique e o worker pegar o trabalho.
    """
    Document.objects.filter(pk=document.pk).update(status=Document.Status.QUEUED, failure_reason="")

    # Os trechos ja indexados foram recortados do texto ANTIGO. Reconverter
    # troca o texto — e o motivo mais comum de reconverter e justamente o texto
    # anterior estar errado, lido sem analise de layout. Deixa-los ativos
    # manteria no indice um recorte que nao corresponde mais ao documento, e a
    # busca continuaria devolvendo o conteudo ruim.
    #
    # Desativar, e nao apagar: o texto que a pessoa selecionou continua visivel,
    # para ela comparar com a nova conversao.
    desativados = document.chunks.filter(is_active=True).update(is_active=False)
    if desativados:
        logger.info(
            "Documento %s: %s trecho(s) saem do indice ate a nova curadoria.",
            document.pk,
            desativados,
        )

    job = criar_job(kind=GenerationJob.Kind.PDF_INGESTION, target_object_id=str(document.pk))
    transaction.on_commit(lambda: advance_generation_job.delay(str(job.pk)))
    logger.info("Documento %s enfileirado para conversao (job %s).", document.pk, job.pk)
    return job


@shared_task
def buscar_fontes_da_pauta(topic_id: str, variar: bool = False) -> int:
    """Busca candidatos a fonte para a pauta. Despachada de dentro do tenant.

    `variar`: a busca de novo, com palavras ainda nao usadas."""
    from apps.content.models import Topic
    from apps.knowledge.fontes_web import buscar_fontes
    from apps.radar.custos import TetoAtingido
    from apps.radar.provedores import ProvedorIndisponivel

    pauta = Topic.objects.filter(pk=topic_id).first()
    if pauta is None:
        return 0
    try:
        return len(buscar_fontes(pauta, variar=variar))
    except (ProvedorIndisponivel, TetoAtingido) as exc:
        logger.warning("Busca de fontes da pauta %s nao completou: %s", topic_id, exc)
        return 0


def pauta_sem_fontes(topic, *, marcar: bool = True) -> None:
    """Marca a pauta como aguardando fontes e, se ligado, busca candidatas.

    Chamado quando a geracao para por falta de embasamento. A busca vai para
    a fila: a geracao ja falhou e nao deve esperar por ela.

    `marcar=False` quando o artigo ja existe (a falta apareceu no
    planejamento): ali o caminho de volta e replanejar o artigo, e a pauta
    continua "usada".
    """
    from apps.content.models import Topic
    from apps.radar.models import ConfiguracaoDoRadar

    if marcar:
        Topic.objects.filter(pk=topic.pk).update(status=Topic.Status.WAITING_SOURCES)
    if ConfiguracaoDoRadar.carregar().buscar_fontes:
        transaction.on_commit(lambda: buscar_fontes_da_pauta.delay(str(topic.pk)))


@shared_task
def conferir_pautas_que_esperam() -> int:
    """Depois de uma curadoria: as pautas aguardando fontes sao conferidas de novo."""
    from apps.knowledge.referencias import conferir_as_que_esperam

    return conferir_as_que_esperam()


def ao_concluir_curadoria() -> None:
    """A fonte nova pode ser a que faltava: confere as pautas que esperam, na fila."""

    def despachar():
        try:
            conferir_pautas_que_esperam.delay()
        except Exception:
            logger.exception("Nao foi possivel despachar a conferencia das pautas.")

    transaction.on_commit(despachar)


# Quanto esperar para tentar o worker de novo quando ele nao diz (Retry-After).
ESPERA_DO_WORKER = 300


@shared_task
def indexar_documento(document_id: str) -> int:
    """Vetoriza os blocos marcados na curadoria e, se pedido, conclui.

    Fora da requisicao: um vetor por paragrafo leva tempo demais para a pessoa
    esperar a tela. Vai ao worker da placa quando ha um marcado para vetorizar;
    se ele pedir para esperar (ocupado, desligado, baixando o modelo), o pedido
    continua de pe e volta para a fila — nao falha. "Vetorizar agora no
    servidor" (`local` no pedido) faz aqui mesmo. Despachada de dentro do tenant.
    """
    from django.contrib.auth import get_user_model

    from apps.knowledge.embeddings import VetorizacaoAdiada
    from apps.knowledge.services import indexar_blocos, marcar_curado

    documento = Document.objects.filter(pk=document_id).first()
    if documento is None or not documento.indexacao_pedida:
        return 0
    pedido = documento.indexacao_pedida
    erro = ""
    try:
        criados = indexar_blocos(
            document=documento,
            blocos_marcados=set(pedido.get("blocos", [])),
            local=bool(pedido.get("local")),
        )
        if pedido.get("concluir"):
            if criados:
                por = get_user_model().objects.filter(pk=pedido.get("por")).first()
                marcar_curado(document=documento, revisado_por=por)
            else:
                erro = "Nenhum trecho foi para o indice: marque ao menos um bloco com texto."
    except VetorizacaoAdiada as exc:
        from django.utils import timezone

        Document.objects.filter(pk=documento.pk).update(
            indexacao_pedida={
                **pedido,
                "aguardando_worker": str(exc)[:300],
                "em": timezone.now().isoformat(),
            }
        )
        indexar_documento.apply_async((document_id,), countdown=exc.retry_after or ESPERA_DO_WORKER)
        return 0
    except Exception as exc:
        logger.exception("Falha ao indexar o documento %s", document_id)
        erro = f"Nao foi possivel vetorizar: {str(exc)[:300]}"
        criados = 0
    Document.objects.filter(pk=documento.pk).update(indexacao_pedida={}, indexacao_erro=erro)
    return criados


def pedir_indexacao(
    documento: Document, *, blocos: set[int], concluir: bool, por, local: bool = False
) -> None:
    """Registra o pedido (a tela passa a mostrar "processando") e o poe na fila."""
    from django.utils import timezone

    Document.objects.filter(pk=documento.pk).update(
        indexacao_pedida={
            "blocos": sorted(blocos),
            "concluir": concluir,
            "local": local,
            "por": str(por.pk) if getattr(por, "pk", None) else None,
            "em": timezone.now().isoformat(),
        },
        indexacao_erro="",
    )
    # Nos testes (e em quem roda sem worker), na hora.
    if getattr(settings, "PUBLIBOT_INDEXAR_NA_HORA", False):
        indexar_documento(str(documento.pk))
        return
    transaction.on_commit(lambda: indexar_documento.delay(str(documento.pk)))


def vetorizar_no_servidor(documento: Document) -> bool:
    """O pedido que espera o worker passa a ser feito no servidor."""
    pedido = documento.indexacao_pedida
    if not pedido:
        return False
    Document.objects.filter(pk=documento.pk).update(indexacao_pedida={**pedido, "local": True})
    if getattr(settings, "PUBLIBOT_INDEXAR_NA_HORA", False):
        indexar_documento(str(documento.pk))
    else:
        transaction.on_commit(lambda: indexar_documento.delay(str(documento.pk)))
    return True


# Sem sinal de vida ha tanto tempo, o pedido perdeu a tarefa.
PEDIDO_PARADO_MINUTOS = 20


def reenfileirar_parados() -> int:
    """Os pedidos parados voltam para a fila. Devolve quantos.

    Parado: sem tentativa ha mais de `PEDIDO_PARADO_MINUTOS` (o "em" do pedido,
    atualizado a cada vez que o worker manda esperar). Acontece quando a tarefa
    foi despachada a um worker do Celery que ainda rodava o codigo antigo e a
    descartou. Repetir e seguro: indexar o mesmo pedido duas vezes da o mesmo
    indice.
    """
    import datetime

    from django.utils import timezone

    limite = timezone.now() - datetime.timedelta(minutes=PEDIDO_PARADO_MINUTOS)
    reenviados = 0
    for documento in Document.objects.exclude(indexacao_pedida={}):
        em = documento.indexacao_pedida.get("em") or ""
        try:
            parado = datetime.datetime.fromisoformat(em) < limite
        except ValueError:
            parado = True
        if parado:
            Document.objects.filter(pk=documento.pk).update(
                indexacao_pedida={**documento.indexacao_pedida, "em": timezone.now().isoformat()}
            )
            indexar_documento.delay(str(documento.pk))
            reenviados += 1
    return reenviados


@shared_task
def reenfileirar_vetorizacoes() -> int:
    from apps.accounts.varredura import para_cada_tenant

    return para_cada_tenant(reenfileirar_parados, "reenfileirar_vetorizacoes")
