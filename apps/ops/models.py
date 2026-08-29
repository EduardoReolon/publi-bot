"""Trabalhos de geracao e registro de inferencia, por tenant.

O banco e a fonte da verdade; o broker e so transporte. O `GenerationJob`
guarda em que passo cada trabalho esta e o que cada passo produziu — o que
torna a retomada apos interrupcao implementavel de verdade e, o que importa
tanto quanto, visivel no painel: "job 47, passo 3 de 4, aguardando capacidade
desde as 14h".
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class GenerationJob(models.Model):
    """Um trabalho de varios passos, com o estado no banco.

    A especificacao original previa retomada via `chain()` do Celery, o que nao
    funciona: a cadeia e materializada no despacho e viaja no cabecalho da
    mensagem — se um elo esgota as tentativas ou o worker morre, o restante e
    descartado sem mecanismo de retomada. E a cadeia nao e inspecionavel, entao
    o painel nao conseguiria mostrar "parou no passo 3 de 4".

    Aqui cada passo grava seu resultado em `step_payloads` e reentra pelo
    banco. Os passos sao **dados**, nao codigo — o que tambem abre caminho para
    o plano de longo prazo (gerar paragrafo a paragrafo, revisar, contraprovar)
    sem refazer a estrutura.
    """

    class Kind(models.TextChoices):
        PILLAR_ARTICLE = "pillar_article", _("Artigo pilar")
        # Refazer parte do artigo, na revisao. Sao dois trabalhos diferentes de
        # proposito: um custa uma chamada por secao marcada e preserva o resto;
        # o outro joga fora o esqueleto e recomeca. Confundi-los na interface
        # faria alguem perder cinco secoes boas querendo consertar uma.
        ARTICLE_REDRAFT = "article_redraft", _("Refazer secoes do artigo")
        ARTICLE_REPLAN = "article_replan", _("Replanejar o artigo")
        QA_ANSWER = "qa_answer", _("Resposta a pergunta")
        PDF_INGESTION = "pdf_ingestion", _("Conversao de PDF")

    class Status(models.TextChoices):
        PENDING = "pending", _("Aguardando")
        RUNNING = "running", _("Executando")
        WAITING_CAPACITY = "waiting_capacity", _("Aguardando capacidade")
        WAITING_HUMAN = "waiting_human", _("Aguardando revisao humana")
        DONE = "done", _("Concluido")
        FAILED = "failed", _("Falhou")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    kind = models.CharField(_("tipo"), max_length=20, choices=Kind.choices)
    target_object_id = models.UUIDField(_("objeto alvo"), null=True, blank=True)

    current_step = models.PositiveSmallIntegerField(_("passo atual"), default=0)
    total_steps = models.PositiveSmallIntegerField(_("total de passos"), default=0)
    status = models.CharField(
        _("situacao"), max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Resultado de cada passo concluido, indexado pelo numero do passo.
    # No banco, e nao no Redis: o Redis nao persiste por padrao, descarta
    # chaves por evicao em silencio, e o que vive so nele nao esta em backup
    # nenhum. Perder isso significa refazer inferencia ja paga.
    step_payloads = models.JSONField(_("resultados dos passos"), default=dict, blank=True)

    attempt_count = models.PositiveIntegerField(_("tentativas"), default=0)
    last_error = models.TextField(_("ultimo erro"), blank=True)
    next_attempt_at = models.DateTimeField(_("proxima tentativa"), null=True, blank=True)

    # A reserva impede que duas execucoes avancem o mesmo job. Expira sozinha,
    # para que a queda de um worker nao trave o trabalho para sempre.
    lease_token = models.UUIDField(_("token da reserva"), null=True, blank=True)
    lease_expires_at = models.DateTimeField(_("reserva expira em"), null=True, blank=True)

    idempotency_key = models.UUIDField(_("chave de idempotencia"), default=uuid.uuid4, unique=True)

    created_at = models.DateTimeField(_("criado em"), default=timezone.now)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)
    finished_at = models.DateTimeField(_("concluido em"), null=True, blank=True)

    class Meta:
        verbose_name = _("trabalho de geracao")
        verbose_name_plural = _("trabalhos de geracao")
        ordering = ["-created_at"]
        indexes = [
            # A consulta que o varredor roda a cada poucos minutos.
            models.Index(fields=["status", "next_attempt_at"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self) -> str:
        progresso = f"{self.current_step}/{self.total_steps}"
        return f"{self.get_kind_display()} #{str(self.pk)[:8]} ({progresso})"

    @property
    def reserva_valida(self) -> bool:
        return bool(self.lease_expires_at and self.lease_expires_at > timezone.now())

    def resultado_do_passo(self, passo: int):
        return (self.step_payloads or {}).get(str(passo))


class InferenceLog(models.Model):
    """Registro de cada chamada de inferencia.

    Sustenta duas perguntas que nao tem resposta sem ele: quanto custa produzir
    um artigo, e se uma versao de prompt e melhor que outra.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        "inference.InferenceConnection",
        on_delete=models.SET_NULL,
        null=True,
        related_name="logs",
        verbose_name=_("conexao"),
    )
    job = models.ForeignKey(
        GenerationJob,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="logs",
        verbose_name=_("trabalho"),
    )
    model_name = models.CharField(_("modelo"), max_length=120)
    workload = models.CharField(_("carga"), max_length=20)
    input_tokens = models.PositiveIntegerField(_("tokens de entrada"), default=0)
    output_tokens = models.PositiveIntegerField(_("tokens de saida"), default=0)
    latency_ms = models.PositiveIntegerField(_("latencia (ms)"), default=0)
    cost_brl = models.DecimalField(_("custo (BRL)"), max_digits=10, decimal_places=6, default=0)
    succeeded = models.BooleanField(_("sucesso"), default=True)
    error = models.TextField(_("erro"), blank=True)
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("registro de inferencia")
        verbose_name_plural = _("registros de inferencia")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"]), models.Index(fields=["model_name"])]

    def __str__(self) -> str:
        return f"{self.model_name} {self.output_tokens}t {self.latency_ms}ms"
