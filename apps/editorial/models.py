"""Perfil editorial do site: voz, vocabulario, estruturas e convite.

Uma linha por tenant — um tenant e um site (ADR-0003). O perfil entra nos
prompts de redacao como um bloco de "guia editorial", anexado ao prompt de
sistema na hora da chamada (`apps/editorial/services.py`), e nao como variavel
de cada prompt: assim os prompts guardados no banco continuam os mesmos, e
mudar o guia vale para a proxima chamada sem publicar versao nova de prompt.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.editorial.presets import ESTRUTURAS_PADRAO, MODOS, TIPOS_DE_CONTEUDO


def _estruturas_padrao() -> dict:
    return {tipo: list(itens) for tipo, itens in ESTRUTURAS_PADRAO.items()}


def _escala():
    return [MinValueValidator(1), MaxValueValidator(5)]


class EditorialProfile(models.Model):
    """O guia editorial do site."""

    class Fundamentacao(models.TextChoices):
        # A ideia central exige fonte do acervo (inclui nota do especialista).
        ESTRITA = "estrita", _("Ideia central sempre com fonte do acervo")
        # Reservado. Escrever sem fonte nenhuma fica desenhado e desligado:
        # so vale quando `PERMITIR_GERACAO_SEM_FONTE` estiver ligado no deploy.
        SEM_FONTE = "sem_fonte", _("Permitir gerar sem fonte (desligado)")

    id = models.SmallAutoField(primary_key=True)
    mode = models.CharField(_("modo"), max_length=20, choices=MODOS, default="saude")

    # --- Voz: as quatro dimensoes de tom da Nielsen Norman Group -----------
    tom_humor = models.PositiveSmallIntegerField(
        _("serio (1) a engracado (5)"), default=1, validators=_escala()
    )
    tom_formalidade = models.PositiveSmallIntegerField(
        _("formal (1) a casual (5)"), default=3, validators=_escala()
    )
    tom_respeito = models.PositiveSmallIntegerField(
        _("respeitoso (1) a irreverente (5)"), default=1, validators=_escala()
    )
    tom_entusiasmo = models.PositiveSmallIntegerField(
        _("objetivo (1) a entusiasmado (5)"), default=2, validators=_escala()
    )
    pessoa = models.CharField(
        _("como falar com o leitor"),
        max_length=40,
        default="voce",
        help_text=_("Ex.: 'voce', 'o senhor', 'nos' (a equipe)."),
    )
    regra_de_ouro = models.CharField(_("regra de ouro"), max_length=300, blank=True)

    # Pares "somos / nao somos" — o quadro popularizado pelo guia de conteudo
    # da Mailchimp. Lista de {"somos": ..., "nao_somos": ...}.
    somos = models.JSONField(_("somos / nao somos"), default=list, blank=True)

    # Vocabulario proibido, com a troca sugerida. Lista de
    # {"termo": ..., "troca": ..., "motivo": ...}. Verificado antes da
    # aprovacao: aparecer um destes bloqueia, a menos que o revisor confirme.
    termos = models.JSONField(_("termos proibidos"), default=list, blank=True)

    # Estruturas por tipo de conteudo: {tipo: [objetivo da secao, ...]}.
    estruturas = models.JSONField(_("estruturas"), default=_estruturas_padrao, blank=True)
    tipo_padrao = models.CharField(
        _("tipo de conteudo padrao"), max_length=20, choices=TIPOS_DE_CONTEUDO, default="guia"
    )

    convite = models.TextField(
        _("convite final"),
        blank=True,
        help_text=_(
            "O que oferecer ao fim do texto, e como. Ex.: 'Convide para agendar "
            "uma avaliacao na clinica, sem pressao'. Vazio: o texto termina sem convite."
        ),
    )
    exemplos = models.TextField(
        _("paragrafos de exemplo"),
        blank=True,
        help_text=_("Dois ou tres paragrafos no tom desejado. Calibram melhor que adjetivos."),
    )

    fundamentacao = models.CharField(
        _("fundamentacao"),
        max_length=12,
        choices=Fundamentacao.choices,
        default=Fundamentacao.ESTRITA,
    )

    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("perfil editorial")
        verbose_name_plural = _("perfil editorial")

    def __str__(self) -> str:
        return f"Perfil editorial ({self.get_mode_display()})"

    @classmethod
    def carregar(cls) -> EditorialProfile:
        objeto, _criado = cls.objects.get_or_create(pk=1)
        return objeto

    def estrutura_de(self, tipo: str) -> list[str]:
        estruturas = self.estruturas or {}
        return list(estruturas.get(tipo) or ESTRUTURAS_PADRAO.get(tipo) or [])


class PerfilDoNegocio(models.Model):
    """O que o site e e o que ele vende: a referencia de tudo que o PubliBot mede.

    Uma linha por tenant. Antes, isto estava espalhado (o "nicho" no cadastro
    do site, a oferta no guia editorial, as dores no radar), e ficava dificil
    responder "perto do QUE?" quando a tela falava em aderencia. Cada campo
    tem um uso so, descrito na tela Negocio:

    * `tema` (+ sementes do radar): o "perto do tema do site" do radar;
    * `oferta`: onde cabe a chamada no artigo, e o "perto da oferta" — a porta
      de entrada, o que e mais facil de vender;
    * `frentes`: o resto do que o negocio faz, inclusive o que uma oportunidade
      testada validou. O sistema so acrescenta aqui com a pessoa confirmando:
      a referencia do que e medido nao muda sozinha;
    * `publico`: para quem o artigo e escrito, no planejamento;
    * valores: a comparacao com anuncios (custo por conversao, retorno).

    As dores do publico continuam guardadas na configuracao do radar (sao
    buscadas como consultas), mas sao editadas aqui, junto do resto.
    """

    id = models.SmallAutoField(primary_key=True)
    tema = models.CharField(
        _("tema do site"),
        max_length=300,
        blank=True,
        help_text=_(
            "Sobre o que o site escreve, numa frase. Ex.: 'Custos e orcamento de obra "
            "para quem constroi ou reforma a casa'."
        ),
    )
    publico = models.CharField(
        _("publico"),
        max_length=300,
        blank=True,
        help_text=_(
            "Quem le e quem compra, numa frase. Ex.: 'Donos de obra de casa propria, "
            "sem formacao tecnica, que compram o material por conta'."
        ),
    )
    oferta = models.TextField(
        _("oferta"),
        blank=True,
        help_text=_(
            "O que o site vende, como o cliente diria. Ex.: 'Assinatura mensal: voce "
            "manda as notas de material pelo WhatsApp e um engenheiro planilha os "
            "custos e diz se voce pagou caro'."
        ),
    )
    frentes = models.TextField(
        _("frentes validadas"),
        blank=True,
        help_text=_(
            "Servicos alem da oferta de entrada, um por linha: os que voce ja faz, e "
            "as oportunidades que o teste com artigo validou (entram por 'Validar', "
            "em Radar > Oportunidades). Contam como parte do negocio, e deixam de ser "
            "novidade."
        ),
    )
    valor_da_conversao = models.DecimalField(
        _("valor de uma conversao (R$)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Quanto vale, em media, um contato que vira cliente (a primeira "
            "mensalidade, o ticket medio). Para calcular o retorno."
        ),
    )
    investimento_publibot = models.DecimalField(
        _("investimento mensal no PubliBot (R$)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    investimento_anuncios = models.DecimalField(
        _("investimento mensal em anuncios (R$)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("O que vai para Google Ads e afins. Vazio: sem comparacao de custo."),
    )
    cotacao_do_dolar = models.DecimalField(
        _("cotacao do dolar (R$)"),
        max_digits=6,
        decimal_places=2,
        default=Decimal("5.40"),
        help_text=_("O custo por clique vem em dolar da DataForSEO."),
    )

    class Meta:
        verbose_name = _("perfil do negocio")
        verbose_name_plural = _("perfil do negocio")

    def __str__(self) -> str:
        return self.tema or "Negocio"

    @classmethod
    def carregar(cls) -> PerfilDoNegocio:
        objeto, _criado = cls.objects.get_or_create(pk=1)
        return objeto


def perfil_do_negocio() -> PerfilDoNegocio | None:
    """O perfil do tenant em uso, ou None fora de um tenant."""
    from django.db import connection

    if getattr(connection, "schema_name", "public") == "public":
        return None
    return PerfilDoNegocio.carregar()
