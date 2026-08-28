"""Base de conhecimento: documentos, trechos curados e rastro de recuperacao.

Estas tabelas vivem no schema de cada tenant. Um tenant nao enxerga o corpus de
outro — consequencia direta do ADR-0003.

A estrategia e "indexacao por resumo": em vez de fatiar o documento inteiro as
cegas, uma pessoa seleciona os trechos de maior valor (tipicamente resumo e
conclusao) e apenas eles sao vetorizados. Isso troca volume por precisao, ao
custo de trabalho humano — que e o custo dominante do produto e por isso e
cronometrado em `curation_seconds`.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pgvector.django import HnswIndex, VectorField


class DocumentCategory(models.Model):
    """Tipo de documento: artigo cientifico, termo de referencia, relatorio.

    E um model, e nao um TextChoices, para que o proprio usuario cadastre
    categorias pelo painel sem exigir deploy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("nome"), max_length=80)
    slug = models.SlugField(_("slug"), max_length=80, unique=True)
    description = models.TextField(_("descricao"), blank=True)
    default_prompt_hint = models.TextField(
        _("orientacao para o prompt"),
        blank=True,
        help_text=_("Texto injetado no prompt ao usar fontes desta categoria."),
    )
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("categoria de documento")
        verbose_name_plural = _("categorias de documento")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Document(models.Model):
    """Um documento-fonte carregado por um humano.

    Idempotencia de ingestao em tres camadas, e nao uma so:

    1. `file_sha256`, calculado NO UPLOAD, antes de enfileirar qualquer coisa.
       Deduplica arquivo identico e economiza o processamento mais caro.
    2. `doi`, chave canonica quando existe, validavel contra o Crossref (que de
       quebra devolve titulo, autores e ano corretos).
    3. `content_fingerprint`, normalizado, NAO unico — serve apenas para exibir
       "possivel duplicata de X" na curadoria, nunca para bloquear.

    A terceira camada e deliberadamente consultiva: um hash de
    `titulo + autor + ano` e fragil ("Silva, J." contra "SILVA, Joao", NFC
    contra NFD, titulo com e sem subtitulo) e bloquear por ele recusaria
    documentos legitimos.
    """

    class Status(models.TextChoices):
        UPLOADED = "uploaded", _("Carregado")
        QUEUED = "queued", _("Na fila")
        PARSING = "parsing", _("Convertendo")
        PARSED = "parsed", _("Convertido")
        PENDING_CURATION = "pending_curation", _("Aguardando curadoria")
        CURATED = "curated", _("Curado")
        EMBEDDED = "embedded", _("Indexado")
        FAILED = "failed", _("Falhou")
        DUPLICATE = "duplicate", _("Duplicata")
        REJECTED = "rejected", _("Rejeitado")

    class License(models.TextChoices):
        """Direitos sobre o documento.

        Nao e burocracia: a regra de retencao depende disto. O Brasil nao tem
        fair use — a Lei 9.610/98 traz no Art. 46 uma lista fechada, e nenhuma
        limitacao cobre mineracao de texto para geracao comercial de conteudo.
        Guardar o texto integral de um paper proprietario e copia integral
        armazenada; guardar apenas o trecho citado se aproxima da citacao de
        pequenos trechos do Art. 46 VIII.
        """

        CC_BY = "cc_by", _("CC BY")
        CC_BY_NC = "cc_by_nc", _("CC BY-NC")
        OPEN_ACCESS = "open_access", _("Acesso aberto")
        PROPRIETARY = "proprietary", _("Proprietario")
        OWN = "own", _("Obra propria")
        UNKNOWN = "unknown", _("Desconhecido")

    # Licencas que permitem guardar o Markdown completo. Para as demais,
    # `markdown_full` e descartado apos a curadoria e so o Super Chunk fica.
    LICENCAS_QUE_PERMITEM_TEXTO_INTEGRAL = frozenset(
        {License.CC_BY, License.CC_BY_NC, License.OPEN_ACCESS, License.OWN}
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    category = models.ForeignKey(
        DocumentCategory,
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name=_("categoria"),
    )

    original_file = models.FileField(_("arquivo"), upload_to="documents/%Y/%m/")
    file_sha256 = models.CharField(_("sha256"), max_length=64, unique=True, db_index=True)
    file_size_bytes = models.PositiveBigIntegerField(_("tamanho em bytes"), default=0)

    # --- Metadados bibliograficos -----------------------------------------
    title = models.CharField(_("titulo"), max_length=500, blank=True)
    authors = models.CharField(
        _("autores"),
        max_length=300,
        blank=True,
        help_text=_("Formatado para citacao: 'Sobrenome et al.' a partir de 3 autores."),
    )
    authors_raw = models.JSONField(_("autores (bruto)"), default=list, blank=True)
    year = models.PositiveSmallIntegerField(
        _("ano"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1500), MaxValueValidator(2200)],
    )
    doi = models.CharField(_("DOI"), max_length=255, blank=True, null=True, unique=True)
    source_url = models.URLField(
        _("URL de origem"),
        max_length=500,
        blank=True,
        help_text=_("Usada como destino do link de saida. Confirmada por um humano."),
    )
    language = models.CharField(
        _("idioma"),
        max_length=10,
        blank=True,
        help_text=_("Idioma da fonte (pode diferir do idioma de publicacao)."),
    )

    class ExtractionMethod(models.TextChoices):
        DOCLING = "docling", _("Docling (analise de layout)")
        PYPDF = "pypdf", _("Texto do PDF, sem analise de layout")
        TEXT = "texto", _("Arquivo ja em texto")

    # Registrado no documento, e nao so no trabalho que o converteu, porque e
    # informacao que a curadoria precisa ver: o `pypdf` devolve a camada de
    # texto sem interpretar a estrutura da pagina — nao distingue coluna,
    # cabecalho, rodape nem tabela, e a ordem de leitura nao e garantida. Sem
    # isto exposto, alguem curaria texto mal lido sem saber, e a citacao
    # publicada apontaria para uma fonte cujo conteudo foi lido errado.

    class MetadataConfidence(models.TextChoices):
        AUTO = "auto", _("Extraido automaticamente")
        CROSSREF = "crossref", _("Confirmado no Crossref")
        MANUAL = "manual", _("Informado por humano")

    metadata_confidence = models.CharField(
        _("confianca dos metadados"),
        max_length=12,
        choices=MetadataConfidence.choices,
        default=MetadataConfidence.AUTO,
    )

    authority_score = models.PositiveSmallIntegerField(
        _("autoridade"),
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Usado para escolher a fonte primaria entre varias."),
    )

    content_fingerprint = models.CharField(
        _("impressao do conteudo"), max_length=64, blank=True, db_index=True
    )

    # --- Conteudo ----------------------------------------------------------
    # TextField, e nao BYTEA com gzip: o PostgreSQL ja comprime valores grandes
    # via TOAST, com ganho parecido. Gzip manual impediria `to_tsvector` (busca
    # hibrida), LIKE, re-chunking por SQL, e obrigaria descompressao na
    # aplicacao a cada leitura.
    markdown_full = models.TextField(_("markdown completo"), blank=True)
    extraction_method = models.CharField(
        _("metodo de extracao"),
        max_length=16,
        choices=ExtractionMethod.choices,
        blank=True,
    )

    # --- Direitos ----------------------------------------------------------
    license = models.CharField(
        _("licenca"), max_length=16, choices=License.choices, default=License.UNKNOWN
    )
    rights_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("direitos confirmados por"),
    )
    rights_confirmed_at = models.DateTimeField(_("direitos confirmados em"), null=True, blank=True)

    # --- Estado ------------------------------------------------------------
    status = models.CharField(
        _("situacao"), max_length=20, choices=Status.choices, default=Status.UPLOADED
    )
    failure_reason = models.TextField(_("motivo da falha"), blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_documents",
        verbose_name=_("carregado por"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_documents",
        verbose_name=_("revisado por"),
    )
    reviewed_at = models.DateTimeField(_("revisado em"), null=True, blank=True)

    # Sem este numero nao existe modelo de negocio: e ele que diz quanto custa
    # cada documento em trabalho humano qualificado.
    curation_seconds = models.PositiveIntegerField(_("segundos de curadoria"), default=0)

    created_at = models.DateTimeField(_("criado em"), default=timezone.now)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("documento")
        verbose_name_plural = _("documentos")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["content_fingerprint"]),
        ]

    def __str__(self) -> str:
        return self.title or f"Documento {self.pk}"

    @property
    def extracao_e_confiavel(self) -> bool:
        """Se o metodo usado da conta de um artigo cientifico de verdade."""
        return self.extraction_method in {
            self.ExtractionMethod.DOCLING,
            self.ExtractionMethod.TEXT,
            "",
        }

    @property
    def nome_do_arquivo(self) -> str:
        """So o nome, sem o caminho `documents/2026/08/` do upload_to."""
        import os

        return os.path.basename(self.original_file.name or "")

    @property
    def pode_guardar_texto_integral(self) -> bool:
        """Se o Markdown completo pode ser retido apos a curadoria."""
        return self.license in self.LICENCAS_QUE_PERMITEM_TEXTO_INTEGRAL


class SuperChunk(models.Model):
    """Trecho de alto valor selecionado por um humano, e sua representacao vetorial.

    E um-para-muitos por documento de proposito. Concatenar resumo e conclusao
    num unico vetor produz um centroide que representa mal os dois — e, com o
    modelo em uso, o excedente alem de 512 tokens seria truncado em silencio.
    """

    class Kind(models.TextChoices):
        ABSTRACT = "abstract", _("Resumo")
        CONCLUSION = "conclusion", _("Conclusao")
        CUSTOM = "custom", _("Outro trecho")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks", verbose_name=_("documento")
    )
    # Mantido por compatibilidade e para o `abstract`/`conclusion` de quem
    # curou antes dos blocos. A curadoria nao pede mais este campo: o titulo do
    # bloco carrega o significado, e nada na busca ramifica por tipo.
    kind = models.CharField(_("tipo"), max_length=12, choices=Kind.choices, default=Kind.CUSTOM)
    content = models.TextField(_("conteudo"))
    char_start = models.PositiveIntegerField(_("inicio"), default=0)
    char_end = models.PositiveIntegerField(_("fim"), default=0)

    embedding = VectorField(_("vetor"), dimensions=settings.EMBEDDING_DIM, null=True)

    # Gravados POR LINHA, e nao lidos do settings na consulta: permite que duas
    # geracoes de modelo convivam durante uma migracao de embeddings.
    embedding_model = models.CharField(_("modelo"), max_length=120, blank=True)
    embedding_dim = models.PositiveSmallIntegerField(_("dimensoes"), default=0)
    token_count = models.PositiveSmallIntegerField(_("tokens"), default=0)

    # --- Metadados desnormalizados ----------------------------------------
    # Copiados na indexacao para que a citacao sobreviva a qualquer alteracao
    # posterior no documento, e para o retrieval nao precisar de JOIN.
    source_title = models.CharField(_("titulo da fonte"), max_length=500, blank=True)
    source_authors = models.CharField(_("autores da fonte"), max_length=300, blank=True)
    source_year = models.PositiveSmallIntegerField(_("ano da fonte"), null=True, blank=True)
    source_url = models.URLField(_("URL da fonte"), max_length=500, blank=True)
    source_authority = models.PositiveSmallIntegerField(_("autoridade da fonte"), default=50)

    # De que parte do documento este trecho saiu. O titulo e o que o proprio
    # documento disser — "Abstract", "Discussao", o que for, em qualquer idioma
    # — e nao uma classificacao escolhida numa lista fixa. Ele aparece na tela
    # de revisao junto da citacao e tambem entra na vetorizacao como prefixo de
    # contexto, porque um paragrafo sozinho nao diz de que estudo veio.
    heading = models.CharField(_("titulo do bloco"), max_length=300, blank=True)
    block_index = models.PositiveSmallIntegerField(_("bloco"), default=0)
    paragraph_index = models.PositiveSmallIntegerField(_("trecho no bloco"), default=0)

    is_active = models.BooleanField(_("ativo"), default=True)
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("super chunk")
        verbose_name_plural = _("super chunks")
        ordering = ["document", "block_index", "paragraph_index"]
        constraints = [
            # Um resumo e uma conclusao por documento; "outro trecho" pode
            # repetir.
            models.UniqueConstraint(
                fields=["document", "kind"],
                condition=models.Q(kind__in=["abstract", "conclusion"]),
                name="uniq_chunk_documento_tipo_canonico",
            )
        ]
        indexes = [
            models.Index(fields=["is_active", "kind"]),
            # HNSW com distancia de cosseno (ADR-0004). Nunca IVFFlat, que
            # exige treino sobre dados existentes e degrada com insercao
            # incremental — o padrao exato da curadoria manual.
            HnswIndex(
                name="superchunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} de {self.document}"


class RetrievalQuery(models.Model):
    """Uma consulta feita ao indice vetorial.

    Guardada para que a regra do limiar seja auditavel e para sustentar a
    rastreabilidade exigida em conteudo revisado: e possivel responder "em que
    esta afirmacao se baseou" muito depois do fato.
    """

    class Origin(models.TextChoices):
        ARTICLE = "article", _("Artigo")
        QA = "qa", _("Pergunta e resposta")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    origin = models.CharField(_("origem"), max_length=10, choices=Origin.choices)
    query_text = models.TextField(_("texto da consulta"))
    top_k = models.PositiveSmallIntegerField(_("top k"), default=3)
    max_distance = models.FloatField(_("distancia maxima"))
    embedding_model = models.CharField(_("modelo"), max_length=120, blank=True)
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("consulta de recuperacao")
        verbose_name_plural = _("consultas de recuperacao")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.query_text[:60]


class RetrievalHit(models.Model):
    """Um trecho devolvido por uma consulta, com a distancia medida."""

    query = models.ForeignKey(
        RetrievalQuery, on_delete=models.CASCADE, related_name="hits", verbose_name=_("consulta")
    )
    super_chunk = models.ForeignKey(
        SuperChunk, on_delete=models.CASCADE, related_name="hits", verbose_name=_("trecho")
    )
    distance = models.FloatField(_("distancia"))
    rank = models.PositiveSmallIntegerField(_("posicao"))

    class Meta:
        verbose_name = _("resultado de recuperacao")
        verbose_name_plural = _("resultados de recuperacao")
        ordering = ["query", "rank"]
        constraints = [
            models.UniqueConstraint(fields=["query", "rank"], name="uniq_hit_consulta_posicao")
        ]

    def __str__(self) -> str:
        return f"#{self.rank} d={self.distance:.4f}"
