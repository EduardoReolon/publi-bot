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
    # OpenAlex: chave gratuita (openalex.org); sem ela a cota diaria e minima.
    openalex_chave_ciphertext = models.BinaryField(
        _("chave OpenAlex (cifrada)"), null=True, blank=True
    )
    # E-mail de contato que OpenAlex e Unpaywall pedem (nao e segredo).
    email_para_bases_academicas = models.EmailField(
        _("e-mail para as bases academicas"),
        blank=True,
        help_text=_("OpenAlex e Unpaywall pedem um e-mail de contato em cada consulta."),
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

    @property
    def tem_openalex(self) -> bool:
        return bool(self.openalex_chave_ciphertext)


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
    usar_youtube = models.BooleanField(
        _("comentarios do YouTube"),
        default=False,
        help_text=_(
            "A cada rodada, em qualquer intensidade, busca 5 videos de ate 6 sementes: as "
            "perguntas dos comentarios viram sinais, e os videos vao para Documentos > "
            "Fontes sugeridas. Gratuito; o PubliBot para antes de acabar a cota diaria."
        ),
    )
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
    fontes_pelo_radar = models.BooleanField(
        _("sugerir fontes a partir das buscas do radar"),
        default=True,
        help_text=_(
            "As paginas que as buscas do radar ja trazem (sem custo extra) viram "
            "fonte sugerida — poucas por rodada, so de temas que o acervo ainda nao "
            "cobre, e so o que parece artigo. Com muita sugestao esperando decisao, "
            "para de sugerir."
        ),
    )
    fontes_por_pauta = models.PositiveSmallIntegerField(
        _("candidatos por pauta"), default=5, validators=[MaxValueValidator(20)]
    )
    artigos_cientificos = models.BooleanField(
        _("buscar artigos cientificos"),
        default=True,
        help_text=_(
            "Gratuito. Busca no OpenAlex (centenas de milhoes de artigos, com resumo "
            "e PDF de acesso aberto) para pautas sem fonte e para os temas da rodada "
            "que o acervo nao cobre; e aproveita o bloco 'Google Academico' das "
            "buscas que o radar ja paga. Tudo espera curadoria em Fontes sugeridas."
        ),
    )
    sementes_cientificas = models.TextField(
        _("sementes cientificas"),
        blank=True,
        help_text=_(
            "Uma por linha: como o assunto aparece em artigo academico — nome de "
            "metodo, estrategia, teoria —, de preferencia em ingles. Sao as buscas do "
            "OpenAlex; as sementes comerciais trariam lixo. Use o pedido ao lado para "
            "uma IA traduzir o seu negocio para o vocabulario academico."
        ),
    )
    artigos_por_rodada = models.PositiveSmallIntegerField(
        _("artigos cientificos por rodada"), default=5, validators=[MaxValueValidator(30)]
    )
    procurar_links_quebrados = models.BooleanField(
        _("procurar links quebrados"),
        default=True,
        help_text=_(
            "Gratuito e devagar: algumas paginas por hora, dos sites que aparecem nas "
            "buscas do seu tema. Link que aponta para pagina que nao existe mais, perto "
            "de um artigo seu, vira oportunidade de link em Radar > Imprensa e links."
        ),
    )

    # --- Concorrentes ----------------------------------------------------------
    idade_para_vigiar = models.PositiveSmallIntegerField(
        _("vigiar artigos publicados ha mais de (dias)"),
        default=45,
        help_text=_(
            "Depois dessa idade, o radar compara cada artigo com os temas novos: "
            "demanda com volume tao perto do artigo quanto o tema que o originou "
            "vira sugestao de ampliar. Antes disso o Google ainda esta avaliando a "
            "pagina, e mexer atrapalha."
        ),
    )

    concorrentes = models.TextField(
        _("concorrentes"),
        blank=True,
        help_text=_(
            "Um por linha: 'dominio | nome do negocio no Google'. O nome e opcional "
            "e so e usado para as avaliacoes. Ex.: 'clinicax.com.br | Clinica X Curitiba'. "
            "Os sugeridos pelo radar e confirmados na tela entram aqui sozinhos."
        ),
    )
    usar_concorrentes_conteudo = models.BooleanField(
        _("o que os concorrentes publicam (sitemap)"),
        default=False,
        help_text=_("Gratuito: le o sitemap publico de cada concorrente."),
    )
    usar_concorrentes_buscas = models.BooleanField(
        _("buscas em que os concorrentes aparecem"),
        default=False,
        help_text=_("Pago (DataForSEO Labs): palavras em que cada dominio ranqueia."),
    )
    usar_avaliacoes = models.BooleanField(
        _("avaliacoes dos concorrentes no Google"),
        default=False,
        help_text=_(
            "Pago (DataForSEO, fila): reclamacoes e perguntas nas avaliacoes do "
            "Perfil da Empresa. Para negocio local."
        ),
    )

    # --- Buscador ---------------------------------------------------------
    class ModoDataForSEO(models.TextChoices):
        FILA = "fila", _("Fila padrao (mais barata, resultado em minutos)")
        AO_VIVO = "ao_vivo", _("Ao vivo (resultado na hora, ~3x mais caro)")

    modo_dataforseo = models.CharField(
        _("modo da DataForSEO nas rodadas"),
        max_length=8,
        choices=ModoDataForSEO.choices,
        default=ModoDataForSEO.FILA,
        help_text=_(
            "As rodadas nao tem pressa: na fila padrao o resultado chega em alguns "
            "minutos, por cerca de um terco do preco. A busca manual e sempre ao vivo."
        ),
    )
    buscador = models.CharField(
        _("buscador"),
        max_length=12,
        choices=Buscador.choices,
        default=Buscador.DATAFORSEO,
        help_text=_(
            "O SearXNG so vale se voce hospedar um (SEARXNG_URL). Sem ele "
            "configurado, as buscas vao para a DataForSEO de qualquer forma."
        ),
    )
    taxa_de_comparacao = models.PositiveSmallIntegerField(
        _("comparar com o pago (%)"),
        default=0,
        validators=[MaxValueValidator(100)],
        help_text=_(
            "So vale com o buscador gratuito (SearXNG). Serve para decidir se o "
            "gratuito basta: nesta porcentagem das buscas do radar, a mesma busca "
            "tambem e feita na DataForSEO (paga), e o painel mostra quanto do "
            "resultado pago o gratuito acertou. Ex.: 10% = 1 busca paga a cada 10. "
            "Com o buscador DataForSEO, deixe 0."
        ),
    )

    # --- Onde e em que lingua ---------------------------------------------
    # 2076 = Brasil na DataForSEO. E o PAIS: vale para o que so existe por
    # pais (as buscas dos concorrentes no Labs) e para as buscas quando nenhuma
    # regiao foi escolhida.
    codigo_de_local = models.PositiveIntegerField(
        _("pais (codigo)"),
        default=2076,
        help_text=_("2076 = Brasil. As regioes abaixo refinam as buscas e o volume."),
    )
    codigo_de_idioma = models.CharField(_("idioma"), max_length=8, default="pt")
    # [{"codigo": 1001773, "nome": "Curitiba, Parana", "tipo": "City"}, ...]
    regioes = models.JSONField(
        _("regioes"),
        default=list,
        blank=True,
        help_text=_(
            "Cidades ou estados em que o site quer ser achado. Cada busca e feita "
            "em cada regiao (o custo multiplica pelo numero de regioes), e o volume "
            "e somado. Vazio = o pais inteiro."
        ),
    )

    dores = models.TextField(
        _("dores do publico"),
        blank=True,
        help_text=_(
            "Uma por linha: problemas que o seu publico sente, nas palavras dele, "
            "e NAO os seus servicos. Ex.: 'manchas no rosto', 'clientes somem "
            "depois da primeira compra'. Alimentam as Oportunidades: temas que o "
            "publico procura e que o site ainda nao oferece."
        ),
    )

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

    @property
    def lista_de_sementes_cientificas(self) -> list[str]:
        return [s.strip() for s in self.sementes_cientificas.splitlines() if s.strip()]

    @property
    def lista_de_dores(self) -> list[str]:
        return [s.strip() for s in self.dores.splitlines() if s.strip()]

    def locais(self) -> list[tuple[int, str]]:
        """(codigo, nome) de onde buscar: as regioes, ou o pais inteiro."""
        escolhidas = [
            (int(r["codigo"]), str(r.get("nome") or r["codigo"]))
            for r in (self.regioes or [])
            if str(r.get("codigo", "")).isdigit()
        ]
        return escolhidas or [(self.codigo_de_local, "")]

    @property
    def local_principal(self) -> int:
        """A primeira regiao: a da busca manual e das avaliacoes."""
        return self.locais()[0][0]

    @property
    def lista_de_concorrentes(self) -> list[dict]:
        """[{"dominio": ..., "nome": ...}] a partir do texto da tela."""
        saida = []
        for linha in self.concorrentes.splitlines():
            if not linha.strip():
                continue
            dominio, _sep, nome = (p.strip() for p in linha.partition("|"))
            dominio = dominio.lower().removeprefix("https://").removeprefix("http://")
            dominio = dominio.removeprefix("www.").strip("/")
            if dominio:
                saida.append({"dominio": dominio, "nome": nome})
        return saida


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
        OPENALEX = "openalex", _("OpenAlex")
        UNPAYWALL = "unpaywall", _("Unpaywall")

    class Finalidade(models.TextChoices):
        RADAR = "radar", _("Rodada do radar")
        MANUAL = "manual", _("Busca manual")
        COMPARACAO = "comparacao", _("Comparacao de buscadores")
        FONTES = "fontes", _("Busca de fontes")
        CONCORRENTES = "concorrentes", _("Concorrentes")
        TRANSCRICAO = "transcricao", _("Transcricao")
        VALOR = "valor", _("Valor do trafego")

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
        # Tarefas na fila da DataForSEO: a coleta volta a cada poucos minutos.
        AGUARDANDO = "aguardando", _("Aguardando resultados da fila")
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
    # Onde a rodada esta: "coleta" (buscas e fontes gratuitas), "volume"
    # (esperando o volume de busca) ou "fim". So muda de fase quando as
    # tarefas da fila da fase anterior voltaram (ou expiraram).
    fase = models.CharField(_("fase"), max_length=10, default="coleta")
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
        CONCORRENTE_CONTEUDO = "conc_conteudo", _("Publicado por concorrente")
        CONCORRENTE_BUSCA = "conc_busca", _("Busca em que o concorrente aparece")
        AVALIACAO = "avaliacao", _("Avaliacao de concorrente")
        DOR = "dor", _("Dor do publico")

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

    # Avaliacao de outra IA, colada pela pessoa (radar.revisao_ia). E so uma
    # etiqueta: nada e apagado sozinho; a tela filtra e a pessoa decide.
    class AvaliacaoIA(models.TextChoices):
        NENHUMA = "", _("Sem avaliacao")
        BOA = "boa", _("Boa")
        RUIM = "ruim", _("Ruim")

    avaliacao_ia = models.CharField(
        _("avaliacao da IA"), max_length=4, choices=AvaliacaoIA.choices, blank=True, default=""
    )
    motivo_ia = models.CharField(_("motivo da IA"), max_length=300, blank=True)
    avaliado_em = models.DateTimeField(_("avaliado em"), null=True, blank=True)
    atualizado_em = models.DateTimeField(_("atualizado em"), auto_now=True)
    criado_em = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("grupo de demanda")
        verbose_name_plural = _("grupos de demanda")
        ordering = ["-nota"]

    def __str__(self) -> str:
        return self.rotulo

    # O que cada parcela mede, com a referencia dita (Negocio). "Aderencia"
    # sozinha nao diz a que.
    ROTULOS_DAS_PARCELAS = {
        "demanda": _("demanda"),
        "diversidade": _("fontes diferentes"),
        "aderencia": _("perto do tema do site"),
        "canibalizacao": _("perto do que ja foi escrito"),
        "cobertura": _("fonte no acervo"),
        "comercial": _("valor comercial"),
        "conversao": _("vizinho de artigo que converte"),
    }

    @property
    def parcelas_rotuladas(self) -> list[tuple[str, float]]:
        return [
            (str(self.ROTULOS_DAS_PARCELAS.get(k, k)), v) for k, v in (self.parcelas or {}).items()
        ]


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


class TarefaNaFila(models.Model):
    """Uma tarefa postada na fila padrao da DataForSEO, esperando o resultado.

    O custo e cobrado quando a tarefa e POSTADA; buscar o resultado depois e
    gratuito. Por isso o livro-caixa registra o post, e esta tabela so
    acompanha o que falta colher.
    """

    class Tipo(models.TextChoices):
        SERP = "serp", _("Pagina de resultados")
        VOLUME = "volume", _("Volume de busca")
        AVALIACOES = "avaliacoes", _("Avaliacoes")

    class Situacao(models.TextChoices):
        AGUARDANDO = "aguardando", _("Aguardando")
        COLHIDA = "colhida", _("Colhida")
        FALHOU = "falhou", _("Falhou")
        EXPIRADA = "expirada", _("Expirada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(_("id na DataForSEO"), max_length=100, unique=True)
    tipo = models.CharField(_("tipo"), max_length=12, choices=Tipo.choices)
    finalidade = models.CharField(_("finalidade"), max_length=12)
    rodada = models.ForeignKey(
        RodadaDoRadar,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tarefas",
        verbose_name=_("rodada"),
    )
    contexto = models.JSONField(_("contexto"), default=dict, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.AGUARDANDO
    )
    erro = models.TextField(_("erro"), blank=True)
    criada_em = models.DateTimeField(_("criada em"), default=timezone.now)
    colhida_em = models.DateTimeField(_("colhida em"), null=True, blank=True)

    class Meta:
        verbose_name = _("tarefa na fila")
        verbose_name_plural = _("tarefas na fila")
        ordering = ["criada_em"]
        indexes = [models.Index(fields=["situacao", "criada_em"])]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.task_id}"


class ConcorrenteSugerido(models.Model):
    """Dominio que aparece na primeira pagina das buscas do site.

    E o jeito das ferramentas de SEO acharem "concorrentes organicos": quem
    disputa as mesmas buscas. Aqui a lista sai das paginas de resultado que o
    radar ja buscou (sem custo extra), e uma pessoa confirma: dominio grande
    que aparece em tudo (portal de noticias, marketplace) nao e concorrente.
    """

    class Situacao(models.TextChoices):
        SUGERIDO = "sugerido", _("Sugerido")
        CONFIRMADO = "confirmado", _("Confirmado")
        RECUSADO = "recusado", _("Nao e concorrente")
        # Aparece nas mesmas buscas mas complementa, em vez de disputar: um
        # candidato a artigo convidado, conteudo em conjunto, estudo de dados.
        PARCEIRO = "parceiro", _("Possivel parceiro")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dominio = models.CharField(_("dominio"), max_length=255, unique=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.SUGERIDO
    )
    # Consultas distintas em que apareceu, e a melhor posicao em cada uma.
    consultas = models.JSONField(_("consultas"), default=dict, blank=True)
    # Ate 5 paginas de exemplo: {"url", "titulo", "consulta"}.
    exemplos = models.JSONField(_("exemplos"), default=list, blank=True)
    # Quao perto do negocio esta cada busca em que apareceu (0 a 1). Separa
    # quem disputa o seu nucleo (concorrente) de quem aparece nos assuntos
    # vizinhos (parceiro provavel: mesmo publico, outro servico).
    aderencias = models.JSONField(_("proximidade das buscas"), default=dict, blank=True)
    # Apareceu no bloco "Principais noticias" do Google: e veiculo de imprensa.
    imprensa = models.BooleanField(_("veiculo de imprensa"), default=False)
    # Quando o e-mail com as pautas foi mandado (painel de imprensa).
    contatado_em = models.DateTimeField(_("contatado em"), null=True, blank=True)
    visto_em = models.DateTimeField(_("visto em"), default=timezone.now)

    class Meta:
        verbose_name = _("concorrente sugerido")
        verbose_name_plural = _("concorrentes sugeridos")
        ordering = ["dominio"]

    def __str__(self) -> str:
        return self.dominio

    @property
    def aparicoes(self) -> int:
        return len(self.consultas)

    @property
    def melhor_posicao(self) -> int | None:
        return min(self.consultas.values()) if self.consultas else None


class LocalDisponivel(models.Model):
    """Um local que a DataForSEO aceita, para o seletor de regioes.

    A lista vem de uma rota gratuita deles e fica guardada: o seletor procura
    aqui, sem chamada externa a cada tecla.
    """

    codigo = models.PositiveIntegerField(_("codigo"), primary_key=True)
    nome = models.CharField(_("nome"), max_length=200)
    tipo = models.CharField(_("tipo"), max_length=40, blank=True)
    pai = models.PositiveIntegerField(_("local acima"), null=True, blank=True)
    pais = models.CharField(_("pais"), max_length=2, blank=True)
    # O nome sem acento e em minusculas, para "curitiba" achar "Curitiba" e
    # "parana" achar "Paraná".
    busca = models.CharField(_("busca"), max_length=200, db_index=True, blank=True)

    class Meta:
        verbose_name = _("local disponivel")
        verbose_name_plural = _("locais disponiveis")
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class Oportunidade(models.Model):
    """Um tema que o publico procura e o site ainda nao oferece.

    E uma LENTE sobre os mesmos grupos do radar, com outra nota: em vez de
    aderencia ao negocio (que derruba o que esta longe do que o site faz),
    novidade, crescimento e valor comercial. A saida nao e pauta: e uma
    decisao de negocio, que a pessoa toma na tela.
    """

    class Situacao(models.TextChoices):
        NOVA = "nova", _("Nova")
        ACOMPANHANDO = "acompanhando", _("Virou semente")
        EM_TESTE = "em_teste", _("Em teste com artigo")
        VALIDADA = "validada", _("Validada: virou frente")
        ARQUIVADA = "arquivada", _("Arquivada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grupo = models.OneToOneField(
        GrupoDeDemanda,
        on_delete=models.CASCADE,
        related_name="oportunidade",
        verbose_name=_("grupo"),
    )
    situacao = models.CharField(
        _("situacao"), max_length=12, choices=Situacao.choices, default=Situacao.NOVA
    )
    nota = models.FloatField(_("nota"), default=0)
    parcelas = models.JSONField(_("parcelas"), default=dict, blank=True)
    # Termos que distinguem o tema dos outros (c-TF-IDF).
    termos = models.JSONField(_("termos"), default=list, blank=True)
    # {"yoy": 0.42, "z": 2.1, "significativo": true, "meses": [[a, m, v], ...]}
    crescimento = models.JSONField(_("crescimento"), default=dict, blank=True)
    cpc = models.FloatField(_("custo por clique (US$)"), null=True, blank=True)
    # Escrita pelo modelo de linguagem, quando ha um no ar. Opcional: a
    # oportunidade existe e e decidida sem ela.
    descricao = models.JSONField(_("descricao"), default=dict, blank=True)
    descricao_em = models.DateTimeField(_("descrita em"), null=True, blank=True)
    criada_em = models.DateTimeField(_("criada em"), default=timezone.now)
    atualizada_em = models.DateTimeField(_("atualizada em"), auto_now=True)

    class Meta:
        verbose_name = _("oportunidade")
        verbose_name_plural = _("oportunidades")
        ordering = ["-nota"]

    def __str__(self) -> str:
        return self.grupo.rotulo

    @property
    def serie_em_barras(self) -> list[dict]:
        """Os meses do historico em % do maior, para o minigrafico da tela."""
        meses = (self.crescimento or {}).get("meses") or []
        maior = max((v for _a, _m, v in meses), default=0) or 1
        return [
            {"rotulo": f"{m:02d}/{a % 100:02d}", "volume": v, "altura": round(100 * v / maior)}
            for a, m, v in meses
        ]

    @property
    def volume_total(self) -> int | None:
        return self.grupo.volume_total or None

    @property
    def parcelas_em_barras(self) -> list[dict]:
        rotulos = {
            "demanda": _("demanda"),
            "crescimento": _("crescimento"),
            "comercial": _("valor comercial"),
            "novidade": _("novidade (longe do tema do site)"),
            "publico": _("perto das dores do publico"),
            "diversidade": _("fontes diferentes"),
        }
        return [
            {"nome": rotulos.get(k, k), "valor": v, "pct": round(100 * float(v or 0))}
            for k, v in (self.parcelas or {}).items()
        ]


class SementeSugerida(models.Model):
    """Uma palavra-semente ou dor sugerida, esperando a pessoa decidir."""

    class Tipo(models.TextChoices):
        SEMENTE = "semente", _("Palavra-semente")
        DOR = "dor", _("Dor do publico")

    class Origem(models.TextChoices):
        PAGINA = "pagina", _("Pagina do site")
        MODELO = "modelo", _("Modelo de linguagem")
        RADAR = "radar", _("Tema forte do radar")
        OUTRA_IA = "outra_ia", _("Outra IA (resposta colada)")

    class Situacao(models.TextChoices):
        SUGERIDA = "sugerida", _("Sugerida")
        ACEITA = "aceita", _("Aceita")
        RECUSADA = "recusada", _("Recusada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    texto = models.CharField(_("texto"), max_length=200)
    # Sem acento e em minusculas: a mesma sugestao de duas origens e uma so.
    chave = models.CharField(_("chave"), max_length=200)
    tipo = models.CharField(_("tipo"), max_length=8, choices=Tipo.choices)
    origem = models.CharField(_("origem"), max_length=8, choices=Origem.choices)
    situacao = models.CharField(
        _("situacao"), max_length=9, choices=Situacao.choices, default=Situacao.SUGERIDA
    )
    evidencia = models.JSONField(_("evidencia"), default=dict, blank=True)
    criada_em = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("semente sugerida")
        verbose_name_plural = _("sementes sugeridas")
        ordering = ["tipo", "-criada_em"]
        constraints = [
            models.UniqueConstraint(fields=["chave", "tipo"], name="semente_sugerida_unica")
        ]

    def __str__(self) -> str:
        return self.texto


class SugestaoDeAtualizacao(models.Model):
    """Um artigo publicado que vale revisar, e o porque.

    Quando o site ja cobriu um nicho, o trabalho passa de criar para
    atualizar: acrescentar o que o publico passou a perguntar, empurrar para
    a primeira pagina o que esta quase la, recuperar o que perdeu posicao.
    """

    class Tipo(models.TextChoices):
        ACRESCENTAR = "acrescentar", _("Demanda nova sobre o mesmo tema")
        QUASE_LA = "quase_la", _("Quase na primeira pagina")
        PERDEU_POSICAO = "perdeu", _("Perdeu posicao")
        FONTE_VENCIDA = "fonte", _("Fonte vencida ou substituida")

    class Situacao(models.TextChoices):
        ABERTA = "aberta", _("Aberta")
        FEITA = "feita", _("Feita")
        DISPENSADA = "dispensada", _("Dispensada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.URLField(_("pagina"), max_length=500)
    titulo = models.CharField(_("titulo"), max_length=300)
    artigo = models.ForeignKey(
        "content.Article",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sugestoes_de_atualizacao",
        verbose_name=_("artigo"),
    )
    tipo = models.CharField(_("tipo"), max_length=12, choices=Tipo.choices)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.ABERTA
    )
    # Conforme o tipo: {"sinais": [...]}, {"consultas": [...]} ou
    # {"posicao": 12.3, "posicao_anterior": 7.1}.
    evidencia = models.JSONField(_("evidencia"), default=dict, blank=True)
    prioridade = models.FloatField(_("prioridade"), default=0)
    criada_em = models.DateTimeField(_("criada em"), default=timezone.now)
    atualizada_em = models.DateTimeField(_("atualizada em"), auto_now=True)
    decidida_em = models.DateTimeField(_("decidida em"), null=True, blank=True)

    class Meta:
        verbose_name = _("sugestao de atualizacao")
        verbose_name_plural = _("sugestoes de atualizacao")
        ordering = ["-prioridade"]
        indexes = [models.Index(fields=["situacao", "url", "tipo"])]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()}: {self.titulo}"


class VigiaDeArtigo(models.Model):
    """O que um artigo publicado cobre, para perceber demanda nova perto dele.

    Na publicacao, o artigo e comparado com o tema que o originou: essa e a
    distancia de REFERENCIA — o quanto o texto "encaixa" na demanda para a qual
    foi escrito. Depois, a cada rodada, um tema com volume que fique TAO perto
    quanto essa referencia (ou mais) e demanda que o artigo deveria responder.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artigo = models.OneToOneField(
        "content.Article",
        on_delete=models.CASCADE,
        related_name="vigia",
        verbose_name=_("artigo"),
    )
    vetor = VectorField(_("vetor"), dimensions=settings.EMBEDDING_DIM, null=True, blank=True)
    distancia_de_referencia = models.FloatField(_("distancia de referencia"), null=True, blank=True)
    grupo_de_referencia = models.ForeignKey(
        GrupoDeDemanda,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("tema de referencia"),
    )
    calculado_em = models.DateTimeField(_("calculado em"), default=timezone.now)

    class Meta:
        verbose_name = _("vigia de artigo")
        verbose_name_plural = _("vigias de artigo")

    def __str__(self) -> str:
        return str(self.artigo)


class ResultadoOrganico(models.Model):
    """Uma pagina da primeira pagina de uma busca do radar.

    A busca ja foi paga: guardar os resultados permite reaproveita-los — para
    achar concorrentes e fontes — sem buscar de novo. Os antigos sao apagados.
    """

    id = models.BigAutoField(primary_key=True)
    rodada = models.ForeignKey(
        RodadaDoRadar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resultados",
        verbose_name=_("rodada"),
    )
    consulta = models.CharField(_("consulta"), max_length=500)
    url = models.URLField(_("URL"), max_length=500)
    titulo = models.CharField(_("titulo"), max_length=500, blank=True)
    trecho = models.TextField(_("trecho"), blank=True)
    posicao = models.PositiveSmallIntegerField(_("posicao"))
    criado_em = models.DateTimeField(_("criado em"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("resultado organico")
        verbose_name_plural = _("resultados organicos")
        ordering = ["rodada", "consulta", "posicao"]

    def __str__(self) -> str:
        return f"{self.posicao}. {self.url}"


class CustoDaPalavra(models.Model):
    """O custo por clique de uma palavra no Google Ads, por regiao.

    Vem de toda chamada de volume (fila ou ao vivo), sem custo a mais: e o que
    permite dizer quanto os cliques organicos de um artigo custariam se fossem
    comprados em anuncio (o "valor do trafego" das ferramentas de SEO).
    """

    id = models.BigAutoField(primary_key=True)
    palavra = models.CharField(_("palavra"), max_length=200)
    local = models.PositiveIntegerField(_("regiao"), default=0)
    cpc = models.FloatField(_("custo por clique (US$)"), null=True, blank=True)
    volume = models.PositiveIntegerField(_("volume mensal"), null=True, blank=True)
    competicao = models.FloatField(_("competicao"), null=True, blank=True)
    atualizado_em = models.DateTimeField(_("atualizado em"), default=timezone.now)

    class Meta:
        verbose_name = _("custo da palavra")
        verbose_name_plural = _("custos das palavras")
        constraints = [
            models.UniqueConstraint(fields=["palavra", "local"], name="uniq_custo_por_regiao")
        ]

    def __str__(self) -> str:
        return f"{self.palavra} ({self.local})"


class PaginaVerificada(models.Model):
    """Pagina de outro site cujos links ja foram conferidos (nao repete tao cedo)."""

    id = models.BigAutoField(primary_key=True)
    url = models.URLField(_("URL"), max_length=500, unique=True)
    links = models.PositiveSmallIntegerField(_("links conferidos"), default=0)
    erro = models.CharField(_("erro"), max_length=300, blank=True)
    verificada_em = models.DateTimeField(_("verificada em"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("pagina verificada")
        verbose_name_plural = _("paginas verificadas")

    def __str__(self) -> str:
        return self.url


class LinkQuebrado(models.Model):
    """Link de um site do assunto que aponta para pagina que nao existe mais.

    Oportunidade classica ("broken link building"): o dono conserta um erro do
    proprio site e o seu artigo entra no lugar do link morto.
    """

    class Situacao(models.TextChoices):
        NOVO = "novo", _("Novo")
        CONTATADO = "contatado", _("Contatado")
        DESCARTADO = "descartado", _("Descartado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pagina_url = models.URLField(_("pagina"), max_length=500)
    pagina_titulo = models.CharField(_("titulo da pagina"), max_length=300, blank=True)
    dominio = models.CharField(_("dominio"), max_length=255, db_index=True)
    link_url = models.URLField(_("link quebrado"), max_length=500)
    texto = models.CharField(_("texto do link"), max_length=300, blank=True)
    status_http = models.PositiveSmallIntegerField(_("resposta"), default=0)
    # O artigo publicado que cobre o mesmo assunto; sem ele, o tema pode virar pauta.
    artigo = models.ForeignKey(
        "content.Article",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("artigo sugerido"),
    )
    proximidade = models.FloatField(_("proximidade"), null=True, blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.NOVO
    )
    encontrado_em = models.DateTimeField(_("encontrado em"), default=timezone.now)

    class Meta:
        verbose_name = _("link quebrado")
        verbose_name_plural = _("links quebrados")
        ordering = ["-encontrado_em"]
        constraints = [
            models.UniqueConstraint(fields=["pagina_url", "link_url"], name="uniq_link_quebrado")
        ]

    def __str__(self) -> str:
        return f"{self.pagina_url} -> {self.link_url}"
