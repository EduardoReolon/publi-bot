"""Tabelas da implementacao de referencia."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ReceivedPublication(models.Model):
    """Conteudo recebido do PubliBot.

    O indice UNICO em `idempotency_key` e o que impede publicacao duplicada.
    Nao e otimizacao: sem ele, o cenario classico de timeout de leitura — o site
    grava e responde, a resposta se perde, o PubliBot repete — publica o mesmo
    conteudo duas vezes.

    A restricao fica no BANCO, e nao numa checagem em Python, porque duas
    requisicoes simultaneas com a mesma chave passariam por qualquer verificacao
    feita antes do INSERT.
    """

    class Kind(models.TextChoices):
        ARTICLE = "article", "Artigo"
        QA = "qa", "Resposta"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.UUIDField(unique=True, db_index=True)

    kind = models.CharField(max_length=10, choices=Kind.choices)
    title = models.CharField(max_length=300, blank=True)
    slug = models.SlugField(max_length=300, blank=True)
    html_content = models.TextField()
    excerpt = models.TextField(blank=True)
    meta_description = models.CharField(max_length=160, blank=True)
    focus_keyword = models.CharField(max_length=120, blank=True)
    language = models.CharField(max_length=10, default="pt-br")

    author_name = models.CharField(max_length=150, blank=True)
    author_credentials = models.CharField(max_length=200, blank=True)
    # Identidade estavel do autor no PubliBot. Liga a publicacao a foto
    # recebida depois, pela rota de arquivos.
    author_reference = models.UUIDField(null=True, blank=True, db_index=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    content_disclosure = models.TextField(blank=True)

    canonical_source = models.URLField(max_length=500, blank=True)
    cover_image_url = models.URLField(max_length=500, blank=True)
    cover_image_alt = models.CharField(max_length=300, blank=True)

    question_id = models.CharField(max_length=120, blank=True, db_index=True)

    post_status = models.CharField(max_length=20, default="published")
    publish_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "publicacao recebida"
        verbose_name_plural = "publicacoes recebidas"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or f"{self.kind} {self.pk}"

    @property
    def url(self) -> str:
        from django.conf import settings

        base = getattr(settings, "PUBLIBOT_NODE_PUBLIC_URL", "").rstrip("/")
        if self.kind == self.Kind.QA:
            return f"{base}/perguntas/{self.question_id}/"
        return f"{base}/blog/{self.slug or self.pk}/"


class VisitorQuestion(models.Model):
    """Pergunta deixada por um visitante.

    `acknowledged_at` existe porque a publicacao da resposta pode demorar dias:
    ela so acontece apos revisao humana. Sem a confirmacao, cada ciclo de coleta
    reimportaria as mesmas perguntas e o sistema geraria a mesma resposta
    repetidamente.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question_text = models.TextField(max_length=500)
    author_name = models.CharField(max_length=150, blank=True)

    # Sem consentimento registrado, o nome nao e enviado. Ele nao e necessario
    # para produzir o conteudo.
    consent_at = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "pergunta de visitante"
        verbose_name_plural = "perguntas de visitantes"
        ordering = ["submitted_at"]
        indexes = [models.Index(fields=["acknowledged_at", "answered_at"])]

    def __str__(self) -> str:
        return self.question_text[:60]


class AuthorPhoto(models.Model):
    """Foto de perfil recebida pela rota de arquivos.

    Guardada pela `author_reference` que o PubliBot envia, e nao pelo nome do
    autor: o nome muda e a referencia nao. Sem uma chave estavel, renomear um
    autor criaria um segundo registro e a caixa de autor ficaria sem foto.

    `sha256` e o que permite responder `author_photo_required: false` na
    proxima publicacao — e tambem detectar que a foto foi TROCADA, caso em que
    o digest muda e o arquivo novo chega.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author_reference = models.UUIDField(unique=True, db_index=True)
    sha256 = models.CharField(max_length=64)
    image = models.ImageField(upload_to="autores/")

    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "foto de autor"
        verbose_name_plural = "fotos de autor"
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return str(self.author_reference)
