"""Conexoes de inferencia e controle de concorrencia.

Estes models vivem no schema `public`, e nao no de cada tenant, porque uma
conexao pode ser **compartilhada**: a GPU do proprio operador atende todos os
tenants. Um cliente que traga a chave de API dele ganha uma conexao propria,
com limite proprio, sem disputar capacidade com os demais — e ai o campo
`tenant` e preenchido.

Tudo que e pesado e um endpoint HTTP (ADR-0012). A GPU local, uma API paga e um
servico de conversao de PDF sao a mesma coisa do ponto de vista do sistema: URL,
credencial e um limite de concorrencia. O argumento decisivo e que APIs
hospedadas *sao* endpoints e nao podem virar worker de fila — se a GPU local
fosse um worker e as APIs fossem endpoints, existiriam dois caminhos de codigo
para a mesma coisa, duas formas de contar concorrencia e dois lugares para o
mesmo defeito.
"""

from __future__ import annotations

import uuid

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class InferenceConnection(models.Model):
    """Um endpoint capaz de executar trabalho pesado.

    Vive no schema `public`: uma conexao pode ser compartilhada por todos os
    tenants (a GPU do proprio operador) ou pertencer a um deles (o cliente que
    traz a chave de API dele, com limite proprio, sem disputar com os demais).
    """

    class Kind(models.TextChoices):
        # Ollama, Together, Groq, OpenAI, DeepSeek, vLLM e LM Studio falam
        # todos o mesmo /v1/chat/completions. Um adaptador cobre todos.
        OPENAI_COMPATIBLE = "openai_compatible", _("Compativel com OpenAI")
        ANTHROPIC = "anthropic", _("Anthropic")
        DOCLING = "docling", _("Docling (conversao de PDF)")
        IMAGE = "image", _("Geracao de imagem")

    class Workload(models.TextChoices):
        TEXT = "text", _("Geracao de texto")
        VISION_PARSE = "vision_parse", _("Conversao de documento")
        IMAGE = "image", _("Geracao de imagem")

    class Health(models.TextChoices):
        UNKNOWN = "unknown", _("Desconhecida")
        HEALTHY = "healthy", _("Saudavel")
        DEGRADED = "degraded", _("Degradada")
        DOWN = "down", _("Fora do ar")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Nulo = compartilhada pelo sistema. Preenchido = exclusiva daquele tenant.
    tenant = models.ForeignKey(
        "accounts.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inference_connections",
        verbose_name=_("tenant"),
        help_text=_("Vazio: conexao compartilhada. Preenchido: exclusiva do tenant."),
    )

    name = models.CharField(_("nome"), max_length=80)
    kind = models.CharField(_("tipo"), max_length=24, choices=Kind.choices)
    base_url = models.URLField(_("URL base"), max_length=300)

    # Cifrada com Fernet. A chave de cifra vive fora do banco, em
    # NODE_KEY_ENCRYPTION_KEY: um dump do banco sozinho nao entrega credencial
    # de terceiro.
    api_key_ciphertext = models.BinaryField(_("chave (cifrada)"), null=True, blank=True)
    api_key_last4 = models.CharField(_("ultimos 4"), max_length=4, blank=True)

    workloads = models.JSONField(
        _("cargas atendidas"),
        default=list,
        help_text=_("Lista de Workload que esta conexao sabe executar."),
    )

    # Para a GPU local isto e 1, sempre. Nao e ajuste fino de desempenho: numa
    # placa de 8 GB, duas inferencias simultaneas estouram a VRAM e o Ollama
    # cai SILENCIOSAMENTE para CPU — dezenas de tokens por segundo viram
    # poucos, sem erro nenhum.
    max_concurrency = models.PositiveSmallIntegerField(
        _("concorrencia maxima"), default=1, validators=[MinValueValidator(1)]
    )

    # Quanto tempo uma reserva sobrevive sem renovacao. Precisa ser maior que a
    # inferencia mais longa esperada, senao duas tarefas disputam a mesma placa.
    lease_seconds = models.PositiveIntegerField(_("duracao da reserva"), default=3600)

    default_model = models.CharField(_("modelo padrao"), max_length=120, blank=True)

    is_active = models.BooleanField(_("ativa"), default=True)
    health_status = models.CharField(
        _("saude"), max_length=12, choices=Health.choices, default=Health.UNKNOWN
    )
    consecutive_failures = models.PositiveSmallIntegerField(_("falhas seguidas"), default=0)
    circuit_open_until = models.DateTimeField(
        _("circuito aberto ate"),
        null=True,
        blank=True,
        help_text=_("Enquanto no futuro, nenhuma tarefa tenta usar esta conexao."),
    )
    last_success_at = models.DateTimeField(_("ultimo sucesso"), null=True, blank=True)

    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("conexao de inferencia")
        verbose_name_plural = _("conexoes de inferencia")
        ordering = ["name"]
        indexes = [models.Index(fields=["is_active", "kind"])]

    def __str__(self) -> str:
        escopo = self.tenant.slug if self.tenant_id else "compartilhada"
        return f"{self.name} ({escopo})"

    @property
    def circuito_aberto(self) -> bool:
        """Se a conexao esta em quarentena apos falhas seguidas."""
        return bool(self.circuit_open_until and self.circuit_open_until > timezone.now())

    def atende(self, workload: str) -> bool:
        return workload in (self.workloads or [])


class InferenceLease(models.Model):
    """Uma vaga ocupada numa conexao.

    A contagem de reservas ativas e o que limita a concorrencia. Fica no banco,
    e nao no Redis, por dois motivos: o estado sobrevive a um restart do cache,
    e a reserva pode ser conferida na mesma transacao que atualiza o job — sem
    janela entre "reservei" e "registrei que reservei".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        InferenceConnection,
        on_delete=models.CASCADE,
        related_name="leases",
        verbose_name=_("conexao"),
    )
    owner_key = models.CharField(
        _("dono"), max_length=120, help_text=_("Identificador de quem reservou.")
    )
    model_name = models.CharField(
        _("modelo"),
        max_length=120,
        blank=True,
        help_text=_("Qual modelo esta carregado. Guia o agrupamento do despachante."),
    )
    acquired_at = models.DateTimeField(_("obtida em"), default=timezone.now)
    expires_at = models.DateTimeField(_("expira em"))
    released_at = models.DateTimeField(_("liberada em"), null=True, blank=True)

    class Meta:
        verbose_name = _("reserva de inferencia")
        verbose_name_plural = _("reservas de inferencia")
        indexes = [models.Index(fields=["connection", "released_at", "expires_at"])]

    def __str__(self) -> str:
        return f"{self.connection} <- {self.owner_key}"
