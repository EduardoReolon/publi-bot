"""Radar de pautas: de onde vem a demanda, quanto custou descobri-la.

O radar responde "sobre o que vale escrever", e nao "o que o texto afirma".
Tudo o que ele traz da web e SINAL DE DEMANDA — a pergunta que as pessoas
fazem, o volume de busca, a posicao do site — e nunca fato para o texto. Fato
continua saindo do acervo curado.

Contas pagas sao POR TENANT: cada cliente usa a propria conta da DataForSEO e
a propria chave do YouTube, e o custo cai direto na conta dele. O livro-caixa
aqui (`ChamadaExterna`) existe para acompanhar e para impor o teto, nao para
cobrar.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pgvector.django import VectorField


class ContasExternas(models.Model):
    """Credenciais das APIs de terceiros deste tenant. Linha unica.

    Cifradas como a chave do site: precisam ser reenviadas a cada chamada, e
    por isso nao podem ser so hasheadas. Nunca voltam para a tela.
    """

    id = models.SmallAutoField(primary_key=True)

    dataforseo_login = models.CharField(_("login DataForSEO"), max_length=200, blank=True)
    dataforseo_senha_ciphertext = models.BinaryField(
        _("senha DataForSEO (cifrada)"), null=True, blank=True
    )
    youtube_chave_ciphertext = models.BinaryField(
        _("chave YouTube (cifrada)"), null=True, blank=True
    )
    # Instancia propria do SearXNG. Vazio: usa a do `.env` (SEARXNG_URL), se houver.
    searxng_url = models.URLField(_("SearXNG"), max_length=300, blank=True)

    atualizado_em = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("contas externas")
        verbose_name_plural = _("contas externas")

    def __str__(self) -> str:
        return "Contas externas"

    @classmethod
    def carregar(cls) -> ContasExternas:
        objeto, _criado = cls.objects.get_or_create(pk=1)
        return objeto

    @property
    def tem_dataforseo(self) -> bool:
        return bool(self.dataforseo_login and self.dataforseo_senha_ciphertext)

    @property
    def tem_youtube(self) -> bool:
        return bool(self.youtube_chave_ciphertext)


class ConfiguracaoDoRadar(models.Model):
    """Quanto, com o que e com que teto o radar trabalha. Linha unica."""

    class Intensidade(models.TextChoices):
        DESLIGADO = "desligado", _("Desligado")
        MINIMO = "minimo", _("Minimo")
        NORMAL = "normal", _("Normal")
        INTENSO = "intenso", _("Intenso")

    class Buscador(models.TextChoices):
        SEARXNG = "searxng", _("SearXNG (gratuito)")
        DATAFORSEO = "dataforseo", _("DataForSEO (pago)")

    id = models.SmallAutoField(primary_key=True)
    intensidade = models.CharField(
        _("intensidade"), max_length=10, choices=Intensidade.choices, default=Intensidade.DESLIGADO
    )

    # --- Foco: quais fontes de demanda usar -------------------------------
    usar_serp = models.BooleanField(
        _("perguntas e buscas relacionadas do Google"),
        default=True,
        help_text=_("'As pessoas tambem perguntam' e buscas relacionadas. Pago na DataForSEO."),
    )
    usar_volume = models.BooleanField(
        _("volume de busca"),
        default=True,
        help_text=_("O mesmo dado do Planejador de palavras-chave do Google Ads."),
    )
    usar_perguntas_do_site = models.BooleanField(_("perguntas dos visitantes"), default=True)
    usar_youtube = models.BooleanField(_("comentarios do YouTube"), default=False)
    usar_search_console = models.BooleanField(_("Search Console"), default=False)

    # --- Fontes -------------------------------------------------------------
    buscar_fontes = models.BooleanField(
        _("buscar fontes na web para pauta sem cobertura"),
        default=True,
        help_text=_(
            "Quando o acervo nao sustenta uma pauta, busca paginas candidatas e "
            "as deixa em Documentos > Fontes sugeridas, esperando curadoria."
        ),
    )
    fontes_por_pauta = models.PositiveSmallIntegerField(
        _("candidatos por pauta"), default=5, validators=[MaxValueValidator(20)]
    )

    # --- Buscador ---------------------------------------------------------
    buscador = models.CharField(
        _("buscador"), max_length=12, choices=Buscador.choices, default=Buscador.SEARXNG
    )
    taxa_de_comparacao = models.PositiveSmallIntegerField(
        _("comparar com o pago (%)"),
        default=0,
        validators=[MaxValueValidator(100)],
        help_text=_(
            "Com o buscador gratuito, esta fracao das buscas e repetida na "
            "DataForSEO e os resultados sao comparados no painel."
        ),
    )

    # --- Onde e em que lingua ---------------------------------------------
    # 2076 = Brasil na DataForSEO. Uma cidade tem codigo proprio.
    codigo_de_local = models.PositiveIntegerField(_("codigo de local"), default=2076)
    codigo_de_idioma = models.CharField(_("idioma"), max_length=8, default="pt")

    sementes = models.TextField(
        _("palavras-semente"),
        blank=True,
        help_text=_(
            "Uma por linha: os temas centrais do negocio. O radar parte delas. "
            "Ex.: 'planilha de orcamento de obra', 'como calcular BDI'."
        ),
    )

    # --- Teto ---------------------------------------------------------------
    teto_mensal_usd = models.DecimalField(
        _("teto mensal (US$)"),
        max_digits=8,
        decimal_places=2,
        default=Decimal("2.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Atingido o teto, nenhuma chamada paga sai ate o mes virar."),
    )

    # --- Search Console -----------------------------------------------------
    propriedade_search_console = models.CharField(
        _("propriedade no Search Console"),
        max_length=300,
        blank=True,
        help_text=_(
            "Como aparece no Search Console: 'sc-domain:exemplo.com.br' (dominio) "
            "ou 'https://www.exemplo.com.br/' (prefixo de URL)."
        ),
    )

    ultima_rodada_em = models.DateTimeField(_("ultima rodada"), null=True, blank=True)

    class Meta:
        verbose_name = _("configuracao do radar")
        verbose_name_plural = _("configuracao do radar")

    def __str__(self) -> str:
        return f"Radar ({self.get_intensidade_display()})"

    @classmethod
    def carregar(cls) -> ConfiguracaoDoRadar:
        objeto, _criado = cls.objects.get_or_create(pk=1)
        return objeto

    @property
    def lista_de_sementes(self) -> list[str]:
        return [s.strip() for s in self.sementes.splitlines() if s.strip()]


class ChamadaExterna(models.Model):
    """Uma chamada a servico externo, paga ou nao.

    As gratuitas tambem sao registradas: sao elas que mostram se o buscador
    gratuito esta respondendo, e quantas vezes ele foi usado.
    """

    class Provedor(models.TextChoices):
        DATAFORSEO = "dataforseo", _("DataForSEO")
        SEARXNG = "searxng", _("SearXNG")
        YOUTUBE = "youtube", _("YouTube")
        SEARCH_CONSOLE = "search_console", _("Search Console")
        WEB = "web", _("Pagina web")

    class Finalidade(models.TextChoices):
        RADAR = "radar", _("Rodada do radar")
        MANUAL = "manual", _("Busca manual")
        COMPARACAO = "comparacao", _("Comparacao de buscadores")
        FONTES = "fontes", _("Busca de fontes")
        TRANSCRICAO = "transcricao", _("Transcricao")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provedor = models.CharField(_("provedor"), max_length=16, choices=Provedor.choices)
    endpoint = models.CharField(_("endpoint"), max_length=200)
    finalidade = models.CharField(_("finalidade"), max_length=12, choices=Finalidade.choices)
    consulta = models.CharField(_("consulta"), max_length=500, blank=True)
    # O custo que o PROVEDOR informou na resposta, quando informa. E o numero
    # que vai aparecer na fatura; uma tabela de precos aqui envelheceria.
    custo_usd = models.DecimalField(_("custo (US$)"), max_digits=10, decimal_places=6, default=0)
    itens = models.PositiveIntegerField(_("itens devolvidos"), default=0)
    sucesso = models.BooleanField(_("sucesso"), default=True)
    erro = models.TextField(_("erro"), blank=True)
    criado_em = models.DateTimeField(_("em"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("chamada externa")
        verbose_name_plural = _("chamadas externas")
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return f"{self.provedor} {self.endpoint} US$ {self.custo_usd}"


class ComparacaoDeBusca(models.Model):
    """A mesma consulta no buscador gratuito e no pago, lado a lado."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consulta = models.CharField(_("consulta"), max_length=500)
    gratuito = models.CharField(_("gratuito"), max_length=16, default="searxng")
    resultados_gratuito = models.PositiveSmallIntegerField(_("resultados (gratuito)"), default=0)
    resultados_pago = models.PositiveSmallIntegerField(_("resultados (pago)"), default=0)
    # Fracao das URLs do top 10 pago que o gratuito tambem trouxe, e o mesmo
    # por dominio. O de dominio e o mais util: o gratuito costuma trazer outra
    # pagina do mesmo site.
    sobreposicao_urls = models.FloatField(_("sobreposicao de URLs"), default=0)
    sobreposicao_dominios = models.FloatField(_("sobreposicao de dominios"), default=0)
    so_no_pago = models.JSONField(_("dominios so no pago"), default=list, blank=True)
    so_no_gratuito = models.JSONField(_("dominios so no gratuito"), default=list, blank=True)
    gratuito_falhou = models.BooleanField(_("gratuito falhou"), default=False)
    criado_em = models.DateTimeField(_("em"), default=timezone.now)

    class Meta:
        verbose_name = _("comparacao de busca")
        verbose_name_plural = _("comparacoes de busca")
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return self.consulta


class RodadaDoRadar(models.Model):
    class Origem(models.TextChoices):
        AGENDADA = "agendada", _("Agendada")
        MANUAL = "manual", _("Pedida na tela")

    class Situacao(models.TextChoices):
        RODANDO = "rodando", _("Rodando")
        CONCLUIDA = "concluida", _("Concluida")
        PARADA_NO_TETO = "teto", _("Parada no teto de custo")
        FALHOU = "falhou", _("Falhou")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    origem = models.CharField(_("origem"), max_length=10, choices=Origem.choices)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.RODANDO
    )
    iniciada_em = models.DateTimeField(_("iniciada em"), default=timezone.now)
    concluida_em = models.DateTimeField(_("concluida em"), null=True, blank=True)
    custo_usd = models.DecimalField(_("custo (US$)"), max_digits=10, decimal_places=6, default=0)
    resumo = models.JSONField(_("resumo"), default=dict, blank=True)
    erro = models.TextField(_("erro"), blank=True)

    class Meta:
        verbose_name = _("rodada do radar")
        verbose_name_plural = _("rodadas do radar")
        ordering = ["-iniciada_em"]

    def __str__(self) -> str:
        return f"Rodada de {self.iniciada_em:%d/%m %H:%M}"


class SinalDeDemanda(models.Model):
    """Uma evidencia de que alguem procura por isto."""

    class Fonte(models.TextChoices):
        PERGUNTA_RELACIONADA = "paa", _("As pessoas tambem perguntam")
        BUSCA_RELACIONADA = "relacionada", _("Busca relacionada")
        SEMENTE = "semente", _("Palavra-semente")
        PERGUNTA_DO_SITE = "visitante", _("Pergunta de visitante")
        YOUTUBE = "youtube", _("Comentario no YouTube")
        SEARCH_CONSOLE = "search_console", _("Search Console")
        MANUAL = "manual", _("Busca manual")

    class Situacao(models.TextChoices):
        # Da busca manual: espera a pessoa dizer se e do segmento.
        PENDENTE = "pendente", _("Aguardando decisao")
        ATIVO = "ativo", _("Ativo")
        DESCARTADO = "descartado", _("Descartado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    texto = models.CharField(_("texto"), max_length=500)
    fonte = models.CharField(_("fonte"), max_length=16, choices=Fonte.choices)
    volume = models.PositiveIntegerField(_("volume mensal"), null=True, blank=True)
    extra = models.JSONField(_("detalhes"), default=dict, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.ATIVO
    )
    rodada = models.ForeignKey(
        RodadaDoRadar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sinais",
        verbose_name=_("rodada"),
    )
    busca_manual = models.ForeignKey(
        "BuscaManual",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sinais",
        verbose_name=_("busca manual"),
    )
    grupo = models.ForeignKey(
        "GrupoDeDemanda",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sinais",
        verbose_name=_("grupo"),
    )
    criado_em = models.DateTimeField(_("em"), default=timezone.now)

    class Meta:
        verbose_name = _("sinal de demanda")
        verbose_name_plural = _("sinais de demanda")
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["situacao", "grupo"])]

    def __str__(self) -> str:
        return self.texto


class GrupoDeDemanda(models.Model):
    """Sinais que dizem a mesma coisa: "dor ciatica" e "dor no nervo ciatico".

    A nota e as parcelas dela ficam gravadas: a tela mostra POR QUE o grupo
    esta na frente, e nao so que esta.
    """

    class Situacao(models.TextChoices):
        NOVO = "novo", _("Novo")
        PAUTA = "pauta", _("Virou pauta")
        DESCARTADO = "descartado", _("Descartado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rotulo = models.CharField(_("rotulo"), max_length=500)
    # Media dos vetores dos sinais. E contra ela que um sinal novo e comparado:
    # agrupar de novo tudo a cada rodada mudaria grupos que ja viraram pauta.
    centroide = VectorField(_("centroide"), dimensions=settings.EMBEDDING_DIM, null=True)
    volume_total = models.PositiveIntegerField(_("volume somado"), default=0)
    nota = models.FloatField(_("nota"), default=0)
    parcelas = models.JSONField(_("parcelas da nota"), default=dict, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.NOVO
    )
    pauta = models.ForeignKey(
        "content.Topic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grupos_de_demanda",
        verbose_name=_("pauta"),
    )
    atualizado_em = models.DateTimeField(_("atualizado em"), auto_now=True)
    criado_em = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("grupo de demanda")
        verbose_name_plural = _("grupos de demanda")
        ordering = ["-nota"]

    def __str__(self) -> str:
        return self.rotulo


class BuscaManual(models.Model):
    """Uma consulta feita na tela, por curiosidade ou pesquisa.

    Os sinais que ela traz ficam PENDENTES ate a pessoa decidir: "e do meu
    segmento, guarde" (entram no radar) ou "descarte". O custo e registrado
    nos dois casos — a chamada ja foi feita.
    """

    class Decisao(models.TextChoices):
        PENDENTE = "pendente", _("Aguardando decisao")
        GUARDADA = "guardada", _("Guardada no radar")
        DESCARTADA = "descartada", _("Descartada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consulta = models.CharField(_("consulta"), max_length=500)
    com_volume = models.BooleanField(_("com volume"), default=False)
    decisao = models.CharField(
        _("decisao"), max_length=10, choices=Decisao.choices, default=Decisao.PENDENTE
    )
    resultados = models.JSONField(_("resultados"), default=list, blank=True)
    erro = models.TextField(_("erro"), blank=True)
    criado_em = models.DateTimeField(_("em"), default=timezone.now)

    class Meta:
        verbose_name = _("busca manual")
        verbose_name_plural = _("buscas manuais")
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return self.consulta


class ColetaDoConsole(models.Model):
    """Um retrato do Search Console: consultas e paginas de um periodo.

    Guardado por coleta, e nao sobrescrito: e a sequencia de retratos que
    mostra se um artigo publicado esta subindo ou caindo.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    propriedade = models.CharField(_("propriedade"), max_length=300)
    inicio = models.DateField(_("inicio"))
    fim = models.DateField(_("fim"))
    linhas = models.PositiveIntegerField(_("linhas"), default=0)
    coletada_em = models.DateTimeField(_("coletada em"), default=timezone.now)

    class Meta:
        verbose_name = _("coleta do Search Console")
        verbose_name_plural = _("coletas do Search Console")
        ordering = ["-coletada_em"]

    def __str__(self) -> str:
        return f"{self.propriedade} {self.inicio} a {self.fim}"


class LinhaDoConsole(models.Model):
    """Uma consulta numa pagina, no periodo da coleta."""

    id = models.BigAutoField(primary_key=True)
    coleta = models.ForeignKey(
        ColetaDoConsole,
        on_delete=models.CASCADE,
        related_name="linhas_set",
        verbose_name=_("coleta"),
    )
    consulta = models.CharField(_("consulta"), max_length=500)
    pagina = models.URLField(_("pagina"), max_length=500)
    cliques = models.PositiveIntegerField(_("cliques"), default=0)
    impressoes = models.PositiveIntegerField(_("impressoes"), default=0)
    ctr = models.FloatField(_("CTR"), default=0)
    posicao = models.FloatField(_("posicao media"), default=0)

    class Meta:
        verbose_name = _("linha do Search Console")
        verbose_name_plural = _("linhas do Search Console")
        indexes = [models.Index(fields=["coleta", "pagina"])]

    def __str__(self) -> str:
        return f"{self.consulta} @ {self.posicao:.1f}"
