"""Sites externos e o registro de tudo que foi trocado com eles.

Um tenant corresponde a um site (ADR-0001), mas a configuracao mora aqui, num
model proprio dentro do schema do tenant, e nao no model `Tenant`. O motivo e
concreto: `Tenant` vive no schema `public`, compartilhado, e a chave de API de
um site de terceiro nao deve ficar numa tabela compartilhada.
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _

from apps.integrations.validators import validar_url_de_site


class Site(models.Model):
    """Um site que recebe conteudo publicado."""

    class Platform(models.TextChoices):
        DJANGO = "django", _("Django")
        WORDPRESS = "wordpress", _("WordPress")
        OTHER = "other", _("Outra")

    class Health(models.TextChoices):
        UNKNOWN = "unknown", _("Desconhecida")
        HEALTHY = "healthy", _("Saudavel")
        DEGRADED = "degraded", _("Degradada")
        DOWN = "down", _("Fora do ar")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("nome"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=80, unique=True)

    base_url = models.URLField(
        _("endereco"),
        max_length=300,
        validators=[validar_url_de_site],
        help_text=_("Somente https. Enderecos internos sao recusados."),
    )
    platform = models.CharField(
        _("plataforma"), max_length=16, choices=Platform.choices, default=Platform.OTHER
    )

    # --- Credencial --------------------------------------------------------
    # Cifrada, porque precisa ser reenviada a cada requisicao e portanto nao
    # pode ser apenas hasheada. A chave de cifra vive fora do banco.
    api_key_ciphertext = models.BinaryField(_("chave (cifrada)"), null=True, blank=True)
    # SHA-256 da chave, para localizar sem decifrar.
    api_key_fingerprint = models.CharField(
        _("impressao da chave"), max_length=64, blank=True, db_index=True
    )
    api_key_last4 = models.CharField(_("ultimos 4"), max_length=4, blank=True)
    api_key_rotated_at = models.DateTimeField(_("rotacionada em"), null=True, blank=True)

    # Segredo separado, usado para assinar o corpo das requisicoes. Distinto da
    # chave de API de proposito: vazar um nao entrega o outro.
    signing_secret_ciphertext = models.BinaryField(
        _("segredo de assinatura (cifrado)"), null=True, blank=True
    )

    # --- Conteudo ----------------------------------------------------------
    content_language = models.CharField(
        _("idioma de publicacao"),
        max_length=10,
        default="pt-br",
        help_text=_("Idioma em que o site publica. Independe do idioma das fontes."),
    )
    # NAO chamar este campo de `timezone`: dentro do corpo da classe ele
    # sombrearia `django.utils.timezone`, e `default=timezone.now` passaria a
    # resolver para o CharField. O erro aparece no import, longe da causa.
    site_timezone = models.CharField(_("fuso horario"), max_length=64, default="America/Sao_Paulo")
    niche = models.CharField(_("nicho"), max_length=200, blank=True)

    is_sensitive = models.BooleanField(
        _("tema sensivel"),
        default=False,
        help_text=_("Exige revisor com credencial tecnica para aprovar conteudo."),
    )
    responsible_professional = models.CharField(
        _("profissional responsavel"),
        max_length=200,
        blank=True,
        help_text=_("Nome, registro no conselho e especialidade."),
    )
    default_author = models.CharField(_("autor padrao"), max_length=150, blank=True)
    default_author_credentials = models.CharField(
        _("credenciais do autor"), max_length=200, blank=True
    )

    # Sobrescreve o modelo de LLM por chave de prompt. Permite que um site em
    # italiano use um modelo melhor em italiano apenas na redacao.
    model_overrides = models.JSONField(_("modelos por prompt"), default=dict, blank=True)

    # --- Contrato ----------------------------------------------------------
    contract_version = models.CharField(_("versao do contrato"), max_length=16, blank=True)
    capabilities = models.JSONField(
        _("recursos suportados"),
        default=list,
        blank=True,
        help_text=_("Declarados pelo site em /api/v1/health/. O SaaS degrada conforme."),
    )

    # --- Operacao ----------------------------------------------------------
    publishing_paused = models.BooleanField(_("publicacao pausada"), default=False)
    max_articles_per_month = models.PositiveSmallIntegerField(
        _("teto mensal de artigos"),
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(200)],
        help_text=_("Volume alto e previsivel e o padrao que buscadores penalizam."),
    )
    health_status = models.CharField(
        _("saude"), max_length=12, choices=Health.choices, default=Health.UNKNOWN
    )
    consecutive_failures = models.PositiveSmallIntegerField(_("falhas seguidas"), default=0)
    circuit_open_until = models.DateTimeField(_("circuito aberto ate"), null=True, blank=True)
    last_success_at = models.DateTimeField(_("ultimo sucesso"), null=True, blank=True)

    created_at = models.DateTimeField(_("criado em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("site")
        verbose_name_plural = _("sites")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def circuito_aberto(self) -> bool:
        return bool(self.circuit_open_until and self.circuit_open_until > django_timezone.now())

    def suporta(self, recurso: str) -> bool:
        """Se o site declarou suportar um recurso opcional do contrato."""
        return recurso in (self.capabilities or [])


class SitePost(models.Model):
    """Espelho local do que ja esta publicado no site.

    Existe para que a deteccao de canibalizacao possa comparar por similaridade
    semantica em vez de comparacao de strings: "Como escolher um consultor de
    dados" e "Guia para contratar consultoria de dados" competem entre si e
    passariam batido numa comparacao literal.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="posts", verbose_name=_("site")
    )
    remote_id = models.CharField(_("id remoto"), max_length=120)
    title = models.CharField(_("titulo"), max_length=300)
    url = models.URLField(_("URL"), max_length=500)
    published_at = models.DateTimeField(_("publicado em"), null=True, blank=True)
    primary_keyword = models.CharField(_("palavra-chave"), max_length=120, blank=True)
    word_count = models.PositiveIntegerField(_("palavras"), default=0)
    synced_at = models.DateTimeField(_("sincronizado em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("publicacao do site")
        verbose_name_plural = _("publicacoes do site")
        ordering = ["-published_at"]
        constraints = [
            models.UniqueConstraint(fields=["site", "remote_id"], name="uniq_post_por_site")
        ]

    def __str__(self) -> str:
        return self.title


class SiteApiCall(models.Model):
    """Auditoria de cada requisicao feita a um site.

    O corpo NUNCA e guardado inteiro: um payload com imagem embutida encheria a
    tabela, e o conteudo ja esta no artigo.
    """

    id = models.BigAutoField(primary_key=True)
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="api_calls", verbose_name=_("site")
    )
    method = models.CharField(_("metodo"), max_length=8)
    path = models.CharField(_("rota"), max_length=200)
    http_status = models.PositiveSmallIntegerField(_("status"), null=True, blank=True)
    error_code = models.CharField(_("codigo do erro"), max_length=40, blank=True)
    latency_ms = models.PositiveIntegerField(_("latencia (ms)"), default=0)
    request_bytes = models.PositiveIntegerField(_("bytes enviados"), default=0)
    idempotency_key = models.UUIDField(_("chave de idempotencia"), null=True, blank=True)
    created_at = models.DateTimeField(_("criada em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("chamada de API")
        verbose_name_plural = _("chamadas de API")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["site", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.method} {self.path} -> {self.http_status}"


class PublishAttempt(models.Model):
    """Cada tentativa de entregar um artigo ou uma resposta.

    Uma tabela para os dois, e nao duas parecidas: as perguntas que se faz aqui
    ("o que falhou ontem", "quantas tentativas ate entrar") sao sobre entrega,
    nao sobre formato do conteudo. Separadas, toda consulta viraria um UNION.
    """

    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(
        "content.Article",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="publish_attempts_log",
        verbose_name=_("artigo"),
    )
    answer = models.ForeignKey(
        "content.Answer",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="publish_attempts_log",
        verbose_name=_("resposta"),
    )
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="publish_attempts", verbose_name=_("site")
    )
    attempt_number = models.PositiveSmallIntegerField(_("tentativa"))

    # Resumo, nunca o payload inteiro: guardar imagem em base64 aqui encheria a
    # tabela sem acrescentar nada que ja nao esteja no artigo.
    payload_summary = models.JSONField(_("resumo do envio"), default=dict, blank=True)

    http_status = models.PositiveSmallIntegerField(_("status"), null=True, blank=True)
    error_code = models.CharField(_("codigo do erro"), max_length=40, blank=True)
    error_message = models.TextField(_("mensagem"), blank=True)
    dry_run = models.BooleanField(
        _("simulacao"),
        default=False,
        help_text=_("Payload montado e registrado, sem envio real."),
    )
    succeeded = models.BooleanField(_("sucesso"), default=False)
    created_at = models.DateTimeField(_("criada em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("tentativa de publicacao")
        verbose_name_plural = _("tentativas de publicacao")
        ordering = ["-created_at"]
        constraints = [
            # Uma tentativa entrega UMA coisa. Sem esta restricao, uma linha com
            # os dois campos nulos seria uma tentativa de publicar nada, e
            # apareceria nos relatorios como se fosse real.
            models.CheckConstraint(
                condition=(
                    models.Q(article__isnull=False, answer__isnull=True)
                    | models.Q(article__isnull=True, answer__isnull=False)
                ),
                name="tentativa_de_artigo_ou_resposta",
            ),
        ]

    def __str__(self) -> str:
        alvo = self.article or self.answer
        return f"{alvo} #{self.attempt_number} -> {self.http_status or 'simulado'}"


class PublicationSchedule(models.Model):
    """Cadencia de publicacao de um site.

    O fuso vem de `Site.site_timezone`, nunca duplicado aqui: dois lugares
    guardando o mesmo fuso divergem, e o sintoma seria publicacao no horario
    errado sem causa aparente.
    """

    class Mode(models.TextChoices):
        WEEKLY_SLOTS = "weekly_slots", _("Dias e horarios fixos")
        INTERVAL_DAYS = "interval_days", _("A cada N dias")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.OneToOneField(
        Site, on_delete=models.CASCADE, related_name="schedule", verbose_name=_("site")
    )
    mode = models.CharField(
        _("modo"), max_length=16, choices=Mode.choices, default=Mode.WEEKLY_SLOTS
    )

    # 0 = segunda, 6 = domingo.
    weekdays = models.JSONField(_("dias da semana"), default=list, blank=True)
    times_of_day = models.JSONField(
        _("horarios"),
        default=list,
        blank=True,
        help_text=_("Lista de 'HH:MM' no fuso do site."),
    )
    interval_days = models.PositiveSmallIntegerField(_("intervalo em dias"), default=7)
    max_per_day = models.PositiveSmallIntegerField(_("maximo por dia"), default=1)

    # Quando o numero de artigos prontos cai abaixo disto, o sistema avisa que
    # e hora de produzir mais.
    buffer_threshold = models.PositiveSmallIntegerField(_("reserva minima"), default=2)

    # Se uma resposta a pergunta ocupa um horario da cadencia. Se nao ocupar, o
    # site publica mais do que o configurado e o calculo da reserva fica errado.
    qa_consumes_slot = models.BooleanField(_("resposta ocupa horario"), default=True)

    is_active = models.BooleanField(_("ativa"), default=True)
    created_at = models.DateTimeField(_("criada em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("cadencia de publicacao")
        verbose_name_plural = _("cadencias de publicacao")

    def __str__(self) -> str:
        return f"Cadencia de {self.site}"


class PublicationSlot(models.Model):
    """Um horario reservado para publicar algo.

    Aponta para um artigo OU para uma resposta, nunca os dois. A restricao fica
    no banco: uma checagem em Python nao impediria dois processos de preencher
    o mesmo horario com coisas diferentes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="slots", verbose_name=_("site")
    )

    # Sempre em UTC. O horario local fica guardado apenas para exibicao — o
    # calculo e a comparacao acontecem em UTC, sempre.
    slot_at = models.DateTimeField(_("momento (UTC)"), db_index=True)
    local_slot_at = models.DateTimeField(_("momento local"), null=True, blank=True)

    article = models.ForeignKey(
        "content.Article",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slots",
        verbose_name=_("artigo"),
    )
    answer = models.ForeignKey(
        "content.Answer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slots",
        verbose_name=_("resposta"),
    )

    filled_at = models.DateTimeField(_("preenchido em"), null=True, blank=True)
    created_at = models.DateTimeField(_("criado em"), default=django_timezone.now)

    class Meta:
        verbose_name = _("horario de publicacao")
        verbose_name_plural = _("horarios de publicacao")
        ordering = ["slot_at"]
        constraints = [
            models.UniqueConstraint(fields=["site", "slot_at"], name="uniq_horario_por_site"),
            models.CheckConstraint(
                condition=(
                    models.Q(article__isnull=False, answer__isnull=True)
                    | models.Q(article__isnull=True, answer__isnull=False)
                    | models.Q(article__isnull=True, answer__isnull=True)
                ),
                name="horario_com_artigo_ou_resposta",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.site} @ {self.slot_at:%d/%m %H:%M}"


class AuthorPhotoDelivery(models.Model):
    """Registro de que a foto de um autor foi entregue a um site.

    O no final e passivo: ele nao conhece o autor antes de receber a primeira
    publicacao, e a foto so viaja quando ele pede. Sem este registro, a unica
    forma de saber se ele ja tem a foto seria perguntar de novo a cada
    publicacao — e a resposta "preciso" chegaria uma vez, mas nada garante que
    o no repita "nao preciso" depois; o custo do engano e um upload por artigo.

    A chave da entrega e o digest do ARQUIVO, nao o autor: trocar a foto
    precisa reenviar, manter a mesma foto nao. Guardar por autor apenas
    esconderia a troca.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pendente")
        SENT = "sent", _("Entregue")
        FAILED = "failed", _("Falhou")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="author_photo_deliveries",
        verbose_name=_("site"),
    )
    author = models.ForeignKey(
        "content.Author",
        on_delete=models.CASCADE,
        related_name="photo_deliveries",
        verbose_name=_("autor"),
    )
    photo_sha256 = models.CharField(_("digest da foto"), max_length=64)

    status = models.CharField(
        _("situacao"), max_length=12, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveIntegerField(_("tentativas"), default=0)
    last_error = models.TextField(_("ultimo erro"), blank=True)

    # Devolvido pela rota de arquivos do no. Ela e assincrona: aceita o arquivo
    # e processa depois, entao a confirmacao de recebimento e tudo o que da
    # para guardar no momento do envio.
    remote_job_id = models.CharField(_("id do processamento remoto"), max_length=120, blank=True)

    created_at = models.DateTimeField(_("criado em"), default=django_timezone.now)
    delivered_at = models.DateTimeField(_("entregue em"), null=True, blank=True)

    class Meta:
        verbose_name = _("entrega de foto de autor")
        verbose_name_plural = _("entregas de foto de autor")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "author", "photo_sha256"], name="uniq_foto_por_site_e_autor"
            )
        ]

    def __str__(self) -> str:
        return f"{self.author} -> {self.site} ({self.get_status_display()})"
