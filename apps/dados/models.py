"""Dados publicos: o catalogo de series (IBGE, Banco Central, DATASUS...).

Mora no schema `public`: sao fatos iguais para todos os clientes, buscados e
vetorizados uma vez so. No tenant fica so a escolha (`content.DadoDaPauta`) e
o valor usado em cada artigo (`Article.dados_usados`). Ver docs/DADOS_PUBLICOS.md.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pgvector.django import VectorField


class Instituicao(models.Model):
    """Quem publica os dados. A estrutura muda por instituicao, nao por serie:
    um adaptador por instituicao cobre todas as series dela."""

    class Adaptador(models.TextChoices):
        NENHUM = "", _("sem adaptador")
        IBGE = "ibge", _("IBGE (SIDRA)")
        BCB = "bcb", _("Banco Central (SGS)")
        OMS = "oms", _("OMS (Global Health Observatory)")

    sigla = models.CharField(_("sigla"), max_length=40, unique=True)
    nome = models.CharField(_("nome"), max_length=200)
    site = models.URLField(_("site"), max_length=300, blank=True)
    # Dominios (ou dominio/caminho) de onde vem o dado: "sidra.ibge.gov.br",
    # "gov.br/saude". Com `confiavel`, link desses lugares e aceito sem curadoria.
    dominios = models.JSONField(_("dominios"), default=list, blank=True)
    nichos = models.JSONField(_("nichos"), default=list, blank=True)
    adaptador = models.CharField(
        _("adaptador"), max_length=20, choices=Adaptador.choices, blank=True
    )
    confiavel = models.BooleanField(_("confiavel"), default=False)
    notas = models.TextField(_("o que tem e como chegar"), blank=True)
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sigla"]
        verbose_name = _("instituicao")
        verbose_name_plural = _("instituicoes")

    def __str__(self) -> str:
        return self.sigla


class Serie(models.Model):
    """Um indicador: o que e, em que unidade, por onde se recorta."""

    class Situacao(models.TextChoices):
        SUGERIDA = "sugerida", _("Sugerida")
        APROVADA = "aprovada", _("Aprovada")
        RECUSADA = "recusada", _("Recusada")

    class Origem(models.TextChoices):
        SEMENTE = "semente", _("Semente")
        CATALOGO = "catalogo", _("Catalogo da instituicao")
        ACERVO = "acervo", _("Citada no acervo")
        MANUAL = "manual", _("Cadastrada a mao")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instituicao = models.ForeignKey(Instituicao, on_delete=models.CASCADE, related_name="series")
    # O identificador na instituicao (tabela do SIDRA, codigo do SGS). A mao: um apelido.
    codigo = models.CharField(_("codigo"), max_length=120)
    titulo = models.CharField(_("titulo"), max_length=300)
    descricao = models.TextField(_("descricao"), blank=True)
    unidade = models.CharField(_("unidade"), max_length=60, blank=True)
    recortes = models.JSONField(_("recortes"), default=list, blank=True)
    periodicidade = models.CharField(_("periodicidade"), max_length=40, blank=True)
    url = models.URLField(_("endereco"), max_length=500, blank=True)
    nichos = models.JSONField(_("nichos"), default=list, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.SUGERIDA
    )
    origem = models.CharField(_("origem"), max_length=10, choices=Origem.choices)
    # Quantas fontes do acervo (de todos os clientes) citam esta serie.
    citacoes = models.PositiveIntegerField(_("citacoes no acervo"), default=0)
    vetor = VectorField(_("vetor"), dimensions=settings.EMBEDDING_DIM, null=True, blank=True)
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["instituicao__sigla", "titulo"]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "codigo"], name="uniq_serie_por_codigo")
        ]
        verbose_name = _("serie")
        verbose_name_plural = _("series")

    def __str__(self) -> str:
        return f"{self.instituicao.sigla} — {self.titulo}"

    @property
    def texto_para_busca(self) -> str:
        partes = [self.titulo, self.descricao, self.unidade, self.instituicao.nome]
        return ". ".join(p for p in partes if p)


class Valor(models.Model):
    """Um valor buscado (ou digitado): cache, e o que o artigo cita."""

    serie = models.ForeignKey(Serie, on_delete=models.CASCADE, related_name="valores")
    local = models.CharField(_("local"), max_length=120, default="Brasil")
    periodo = models.CharField(_("periodo"), max_length=40)
    valor = models.DecimalField(_("valor"), max_digits=24, decimal_places=6)
    nota = models.CharField(_("nota"), max_length=300, blank=True)
    buscado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-periodo"]
        constraints = [
            models.UniqueConstraint(
                fields=["serie", "local", "periodo"], name="uniq_valor_por_periodo"
            )
        ]

    def __str__(self) -> str:
        return f"{self.serie_id}: {self.valor} ({self.local}, {self.periodo})"


class PedidoDeAdaptador(models.Model):
    """Um lugar de dados que as fontes citam e que ainda nao tem adaptador.
    A tela copia o pedido para o desenvolvedor escrever o adaptador."""

    class Situacao(models.TextChoices):
        ABERTO = "aberto", _("Aberto")
        FEITO = "feito", _("Feito")
        DESCARTADO = "descartado", _("Descartado")

    dominio = models.CharField(_("dominio"), max_length=200, unique=True)
    exemplos = models.JSONField(_("links de exemplo"), default=list, blank=True)
    citacoes = models.PositiveIntegerField(_("citacoes"), default=0)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.ABERTO
    )
    notas = models.TextField(_("notas"), blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-citacoes"]

    def __str__(self) -> str:
        return self.dominio
