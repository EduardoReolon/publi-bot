"""Estudos de mercado: uma empresa, uma area (cidade e raio) e rodadas com IA.

Cada rodada e uma volta do dialogo com a outra IA: o PubliBot monta o pedido
(a descricao da empresa, a area, o que as rodadas anteriores acharam), a IA
devolve o que buscar (palavras, sementes, modo, cidades), o PubliBot busca na
DataForSEO e guarda tudo. O historico inteiro fica, para voltar a qualquer
rodada.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _


class Estudo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(_("nome"), max_length=200)
    descricao = models.TextField(
        _("descricao"),
        blank=True,
        help_text=_(
            "A empresa, o que ela vende, para quem, a meta e os objetivos, o orcamento. "
            "Vai inteira no pedido para a outra IA."
        ),
    )
    # A cidade do centro (codigo do IBGE) e o raio: as cidades da area.
    cidade = models.PositiveIntegerField(_("cidade do centro (IBGE)"), null=True, blank=True)
    raio_km = models.PositiveIntegerField(_("raio (km)"), default=50)
    # As cidades da area que ficam de fora (ex.: do outro lado da fronteira).
    fora_da_area = models.JSONField(_("cidades fora da area"), default=list, blank=True)
    # O funil, para estimar clientes a partir das buscas.
    ctr = models.DecimalField(
        _("cliques por busca (%)"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("5"),
        help_text=_("Do total de buscas, quantas viram clique no seu anuncio."),
    )
    conversao = models.DecimalField(
        _("contatos por clique (%)"), max_digits=5, decimal_places=2, default=Decimal("8")
    )
    fechamento = models.DecimalField(
        _("clientes por contato (%)"), max_digits=5, decimal_places=2, default=Decimal("10")
    )
    valor_do_cliente = models.DecimalField(
        _("valor de um cliente (R$)"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("O que um cliente deixa para a empresa (ex.: a comissao). Opcional."),
    )
    cotacao_do_dolar = models.DecimalField(
        _("cotacao do dolar (R$)"),
        max_digits=6,
        decimal_places=2,
        default=Decimal("5.50"),
        help_text=_("O Google informa o custo por clique em dolar."),
    )
    # As palavras que entram na campanha (marcadas na tela ou sugeridas pela IA).
    campanha = models.JSONField(_("palavras da campanha"), default=list, blank=True)
    negativas = models.JSONField(_("palavras negativas"), default=list, blank=True)
    # O tenant criado a partir deste estudo (o schema), quando virou cliente.
    tenant_criado = models.CharField(_("tenant criado"), max_length=63, blank=True)
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)
    atualizado_em = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("estudo")
        verbose_name_plural = _("estudos")
        ordering = ["-atualizado_em"]

    def __str__(self) -> str:
        return self.nome


class Rodada(models.Model):
    class Modo(models.TextChoices):
        ESTIMATIVA = "estimativa", _("Estimativa (estado x populacao)")
        REAL = "real", _("Valor real (cidade a cidade)")

    class Situacao(models.TextChoices):
        RASCUNHO = "rascunho", _("Conferir antes de buscar")
        NA_FILA = "na_fila", _("Buscando")
        PRONTA = "pronta", _("Pronta")
        FALHOU = "falhou", _("Falhou")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    estudo = models.ForeignKey(Estudo, on_delete=models.CASCADE, related_name="rodadas")
    numero = models.PositiveIntegerField(_("numero"))
    pedido = models.TextField(_("pedido copiado"), blank=True)
    resposta = models.TextField(_("resposta colada"), blank=True)
    analise = models.TextField(_("analise da IA"), blank=True)
    modo = models.CharField(_("modo"), max_length=12, choices=Modo.choices, default=Modo.ESTIMATIVA)
    palavras = models.JSONField(_("palavras"), default=list, blank=True)
    sementes = models.JSONField(_("sementes de ideias"), default=list, blank=True)
    site = models.CharField(_("site para ideias"), max_length=200, blank=True)
    # Codigos do IBGE das cidades buscadas uma a uma (modo real).
    cidades = models.JSONField(_("cidades"), default=list, blank=True)
    # Fila padrao da DataForSEO (mais barata, volta em minutos) ou ao vivo.
    fila = models.BooleanField(_("fila padrao (mais barata)"), default=True)
    # O passo em que a rodada esta na fila (ver coleta.avancar_na_fila).
    etapa = models.JSONField(_("etapa na fila"), default=dict, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.RASCUNHO
    )
    erro = models.TextField(_("erro"), blank=True)
    avisos = models.JSONField(_("avisos"), default=list, blank=True)
    # {palavra: {volume_estado, volume_area, cidades: {ibge: n}, reais: {ibge: n},
    #  cpc, competicao, meses, origem}}
    resultado = models.JSONField(_("resultado"), default=dict, blank=True)
    custo_usd = models.DecimalField(_("custo (US$)"), max_digits=10, decimal_places=4, default=0)
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)
    feita_em = models.DateTimeField(_("feita em"), null=True, blank=True)

    class Meta:
        verbose_name = _("rodada")
        verbose_name_plural = _("rodadas")
        ordering = ["-numero"]
        constraints = [
            models.UniqueConstraint(fields=["estudo", "numero"], name="rodada_unica_por_estudo")
        ]

    def __str__(self) -> str:
        return f"{self.estudo} #{self.numero}"


class Populacao(models.Model):
    """A populacao de cada municipio (Censo do IBGE), lida uma vez por estado."""

    codigo = models.PositiveIntegerField(_("codigo do IBGE"), primary_key=True)
    populacao = models.PositiveIntegerField(_("populacao"))
    ano = models.PositiveSmallIntegerField(_("ano"))

    class Meta:
        verbose_name = _("populacao")
        verbose_name_plural = _("populacoes")

    def __str__(self) -> str:
        return f"{self.codigo}: {self.populacao}"
