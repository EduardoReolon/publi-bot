"""Prompts versionados, pautas, artigos e o rastro de como foram produzidos.

Duas ideias organizam este app.

**Prompts sao dados, nao codigo.** Ficam no banco, versionados, com o modelo
com que foram calibrados. Ajustar comportamento nao exige deploy, e um teste A/B
tem como comparar versoes.

**Tudo que sustenta uma afirmacao fica registrado.** `thesis_json`,
`ArticleCitation` e `ArticleRevision` existem para que se possa responder, meses
depois, em que fonte determinada frase se baseou e quanto um humano de fato
editou. Sem esse rastro, a revisao humana e uma alegacao sem prova.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PromptTemplate(models.Model):
    """Um ponto do fluxo onde o sistema fala com o modelo."""

    class Key(models.TextChoices):
        METADATA_EXTRACT = "metadata_extract", _("Extracao de metadados")
        TOPIC_IDEATION = "topic_ideation", _("Geracao de pautas")
        CONSENSUS_FILTER = "consensus_filter", _("Filtro de consenso")
        # A redacao acontece em quatro rodadas curtas, e nao numa chamada so:
        # um modelo pequeno escreve bem 300 palavras com tres fontes na frente,
        # e mal um artigo inteiro com quinze.
        ARTICLE_OUTLINE = "article_outline", _("Plano do artigo")
        SECTION_DRAFT = "section_draft", _("Redacao de secao")
        ARTICLE_FRAMING = "article_framing", _("Abertura e fecho")
        SEO_METADATA = "seo_metadata", _("Metadados de busca")
        # Caminho antigo, de uma tacada. Substituido pelos quatro acima;
        # mantido porque instalacoes existentes tem a linha no banco.
        SEO_DRAFT = "seo_draft", _("Redacao (caminho antigo)")
        QA_ANSWER = "qa_answer", _("Resposta a pergunta")
        IMAGE_PROMPT = "image_prompt", _("Prompt de imagem")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(_("chave"), max_length=32, choices=Key.choices, unique=True)
    description = models.TextField(_("descricao"), blank=True)
    created_at = models.DateTimeField(_("criado em"), default=timezone.now)

    class Meta:
        verbose_name = _("modelo de prompt")
        verbose_name_plural = _("modelos de prompt")
        ordering = ["key"]

    def __str__(self) -> str:
        return self.get_key_display()


class PromptVersion(models.Model):
    """Uma versao concreta de um prompt, com o modelo que a acompanha.

    O `model_name` fica aqui, e nao numa configuracao global, porque um prompt e
    calibrado *com* um modelo: texto que funciona bem num modelo de 7B pode
    render mal noutro. Separa-los faria o teste A/B comparar coisas diferentes
    sem saber.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        PromptTemplate,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("modelo de prompt"),
    )
    version = models.PositiveSmallIntegerField(_("versao"))

    # Permite duas variantes ativas ao mesmo tempo, que e o que torna o teste
    # A/B possivel.
    variant = models.CharField(_("variante"), max_length=16, default="A")

    system_prompt = models.TextField(_("prompt de sistema"))
    user_prompt_template = models.TextField(
        _("prompt do usuario"),
        help_text=_("Aceita marcadores {chave} preenchidos em tempo de execucao."),
    )
    variables = models.JSONField(_("variaveis esperadas"), default=list, blank=True)

    model_name = models.CharField(_("modelo"), max_length=120, blank=True)
    temperature = models.FloatField(_("temperatura"), default=0.2)
    max_tokens = models.PositiveIntegerField(_("tokens maximos"), default=4096)

    is_active = models.BooleanField(_("ativa"), default=False)
    traffic_weight = models.PositiveSmallIntegerField(
        _("peso no sorteio"),
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("versao de prompt")
        verbose_name_plural = _("versoes de prompt")
        ordering = ["template", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version"], name="uniq_versao_por_template"
            ),
            # Por VARIANTE, e nao por template: com unicidade por template so
            # uma variante poderia estar ativa, e o teste A/B seria impossivel.
            models.UniqueConstraint(
                fields=["template", "variant"],
                condition=models.Q(is_active=True),
                name="uniq_variante_ativa_por_template",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.template.key} v{self.version}{self.variant}"


class PromptRun(models.Model):
    """Uma execucao concreta de um prompt.

    Liga a versao usada ao que o humano fez com o resultado. Sem esse elo, o
    teste A/B nao tem funcao objetivo — trocar prompt seria trocar no escuro.
    """

    class Verdict(models.TextChoices):
        PENDING = "pending", _("Aguardando revisao")
        ACCEPTED = "accepted", _("Aceito sem alteracao")
        EDITED = "edited", _("Aceito com edicao")
        REJECTED = "rejected", _("Rejeitado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prompt_version = models.ForeignKey(
        PromptVersion,
        on_delete=models.PROTECT,
        related_name="runs",
        verbose_name=_("versao"),
    )
    input_tokens = models.PositiveIntegerField(_("tokens de entrada"), default=0)
    output_tokens = models.PositiveIntegerField(_("tokens de saida"), default=0)
    latency_ms = models.PositiveIntegerField(_("latencia (ms)"), default=0)
    human_verdict = models.CharField(
        _("veredito humano"), max_length=10, choices=Verdict.choices, default=Verdict.PENDING
    )
    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("execucao de prompt")
        verbose_name_plural = _("execucoes de prompt")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.prompt_version} -> {self.get_human_verdict_display()}"


class Topic(models.Model):
    """Uma pauta aprovada por um humano antes de virar artigo."""

    class Status(models.TextChoices):
        SUGGESTED = "suggested", _("Sugerida")
        APPROVED = "approved", _("Aprovada")
        REJECTED = "rejected", _("Rejeitada")
        USED = "used", _("Usada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(_("titulo"), max_length=300)
    briefing = models.TextField(_("orientacao"), blank=True)
    target_keyword = models.CharField(_("palavra-chave alvo"), max_length=120, blank=True)
    status = models.CharField(
        _("situacao"), max_length=12, choices=Status.choices, default=Status.SUGGESTED
    )

    # Alto = o tema conflita com conteudo ja publicado. Medido por similaridade
    # semantica, nao por comparacao de strings: "Como escolher um consultor de
    # dados" e "Guia para contratar consultoria de dados" sao canibalizacao pura
    # e passariam batido numa comparacao literal.
    cannibalization_score = models.FloatField(_("risco de canibalizacao"), default=0.0)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_topics",
        verbose_name=_("aprovada por"),
    )
    approved_at = models.DateTimeField(_("aprovada em"), null=True, blank=True)
    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("pauta")
        verbose_name_plural = _("pautas")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self) -> str:
        return self.title


class Article(models.Model):
    """Um artigo, do rascunho a publicacao."""

    class Status(models.TextChoices):
        DRAFTING = "drafting", _("Em producao")
        NEEDS_MORE_SOURCES = "needs_more_sources", _("Requer novas fontes")
        PENDING_REVIEW = "pending_review", _("Aguardando revisao")
        APPROVED_SCHEDULED = "approved_scheduled", _("Aprovado e agendado")
        PUBLISHED = "published", _("Publicado")
        PUSH_FAILED = "push_failed", _("Falha na publicacao")
        REJECTED = "rejected", _("Rejeitado")

    class Consensus(models.TextChoices):
        HIGH = "high", _("Concordancia alta")
        PARTIAL = "partial", _("Concordancia parcial")
        CONFLICT = "conflict", _("Fontes divergem")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.ForeignKey(
        Topic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        verbose_name=_("pauta"),
    )

    title = models.CharField(_("titulo"), max_length=300)
    slug = models.SlugField(_("slug"), max_length=300, blank=True)

    # Fonte da verdade editavel. Modelos produzem Markdown de forma muito mais
    # confiavel que HTML, e o HTML vai direto para o site de um terceiro.
    body_markdown = models.TextField(_("corpo (markdown)"), blank=True)
    # Derivado: convertido e sanitizado antes do envio.
    body_html = models.TextField(_("corpo (html)"), blank=True)

    excerpt = models.TextField(_("resumo"), blank=True)
    meta_description = models.CharField(_("meta description"), max_length=160, blank=True)
    focus_keyword = models.CharField(_("palavra-chave"), max_length=120, blank=True)

    # Termos secundarios que o texto deve cobrir. Sugeridos pelo planejamento e
    # editaveis na revisao: quem conhece o negocio sabe o que o publico procura
    # melhor que o modelo, mas ter uma sugestao evita a folha em branco.
    secondary_keywords = models.JSONField(_("palavras-chave secundarias"), default=list, blank=True)

    # Para quem o texto e escrito e o que a pessoa quer ao buscar. Muda o tom e
    # a estrutura mais do que qualquer outro parametro, e por isso e explicito
    # em vez de ficar implicito no prompt.
    audience = models.CharField(_("publico"), max_length=200, blank=True)
    search_intent = models.CharField(_("intencao de busca"), max_length=200, blank=True)

    # Saida estruturada do filtro de consenso.
    thesis_json = models.JSONField(_("tese"), default=dict, blank=True)
    consensus = models.CharField(
        _("consenso"), max_length=10, choices=Consensus.choices, blank=True
    )

    # Verdadeiro quando so uma fonte passou o limiar. Com uma unica fonte nao ha
    # consenso possivel: o filtro vira parafrase disfarcada de validacao.
    single_source = models.BooleanField(_("fonte unica"), default=False)

    # Destino do link de saida. A URL NUNCA e pedida ao modelo (ver
    # apps/content/rendering.py): vem daqui, confirmada por um humano.
    primary_source = models.ForeignKey(
        "knowledge.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_for_articles",
        verbose_name=_("fonte primaria"),
    )
    outbound_link_url = models.URLField(_("link de saida"), max_length=500, blank=True)
    anchor_text = models.CharField(_("texto-ancora"), max_length=200, blank=True)

    status = models.CharField(
        _("situacao"), max_length=24, choices=Status.choices, default=Status.DRAFTING
    )

    # --- Autoria e revisao -------------------------------------------------
    # Conteudo sem byline e o pior cenario possivel num nicho sensivel: nao ha
    # como avaliar quem escreveu nem com que credencial.
    author_name = models.CharField(_("autor"), max_length=150, blank=True)
    author_credentials = models.CharField(
        _("credenciais"),
        max_length=200,
        blank=True,
        help_text=_("Ex.: Enfermeira Obstetra, COREN-SP 123456"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_articles",
        verbose_name=_("revisado por"),
    )
    reviewed_at = models.DateTimeField(_("revisado em"), null=True, blank=True)
    review_seconds = models.PositiveIntegerField(_("segundos de revisao"), default=0)

    # Quanto do texto publicado difere do que o modelo produziu. Perto de zero
    # significa que a revisao foi carimbo.
    human_edit_ratio = models.FloatField(_("proporcao editada"), default=0.0)
    word_count = models.PositiveIntegerField(_("palavras"), default=0)

    # --- Publicacao --------------------------------------------------------
    scheduled_for = models.DateTimeField(_("agendado para"), null=True, blank=True, db_index=True)
    published_at = models.DateTimeField(_("publicado em"), null=True, blank=True)
    published_url = models.URLField(_("URL publicada"), max_length=500, blank=True)

    # Chave estavel do lado do site. A URL muda se o cliente editar o slug;
    # sem um identificador estavel seria impossivel corrigir ou despublicar.
    remote_id = models.CharField(_("id remoto"), max_length=120, blank=True, db_index=True)

    # Gerada UMA vez, quando o artigo entra em "aprovado e agendado", e
    # reenviada identica em toda tentativa. E o que impede publicacao duplicada
    # quando a resposta se perde depois do site ja ter gravado.
    idempotency_key = models.UUIDField(_("chave de idempotencia"), default=uuid.uuid4, unique=True)

    publish_attempts = models.PositiveSmallIntegerField(_("tentativas"), default=0)
    last_publish_error = models.TextField(_("ultimo erro"), blank=True)
    last_error_code = models.CharField(_("codigo do erro"), max_length=40, blank=True)
    next_retry_at = models.DateTimeField(_("proxima tentativa"), null=True, blank=True)

    created_at = models.DateTimeField(_("criado em"), default=timezone.now)
    updated_at = models.DateTimeField(_("atualizado em"), auto_now=True)

    class Meta:
        verbose_name = _("artigo")
        verbose_name_plural = _("artigos")
        ordering = ["-created_at"]
        indexes = [
            # Exatamente a consulta que o agendador roda minuto a minuto.
            models.Index(fields=["status", "scheduled_for"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def exige_confirmacao_de_divergencia(self) -> bool:
        """Fontes que se contradizem nao podem virar afirmacao pacifica.

        Situacao comum em literatura cientifica. Sem esta trava, o modelo
        escolheria um lado em silencio e o artigo afirmaria como assentado algo
        que e controverso — precisamente o que a revisao humana deveria
        interceptar, mas nada sinalizaria ao revisor.
        """
        return self.consensus == self.Consensus.CONFLICT


class ArticleRevision(models.Model):
    """Cada versao do corpo do artigo, do modelo ou do humano.

    Sem este historico nao ha como demonstrar esforco editorial nem medir
    `human_edit_ratio` — e a revisao humana vira uma alegacao sem prova.
    """

    class Source(models.TextChoices):
        LLM = "llm", _("Modelo")
        HUMAN = "human", _("Humano")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="revisions", verbose_name=_("artigo")
    )
    version = models.PositiveSmallIntegerField(_("versao"))
    body_markdown = models.TextField(_("corpo"))
    source = models.CharField(_("origem"), max_length=8, choices=Source.choices)
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("editor"),
    )
    prompt_run = models.ForeignKey(
        PromptRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revisions",
        verbose_name=_("execucao de prompt"),
    )
    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("revisao de artigo")
        verbose_name_plural = _("revisoes de artigo")
        ordering = ["article", "version"]
        constraints = [
            models.UniqueConstraint(fields=["article", "version"], name="uniq_revisao_por_artigo")
        ]

    def __str__(self) -> str:
        return f"{self.article} v{self.version} ({self.source})"


class ArticleSection(models.Model):
    """Uma secao do artigo, escrita numa rodada propria.

    O artigo e montado secao a secao por dois motivos que se reforcam.

    **O modelo.** Um LLM pequeno com janela curta escreve bem um trecho de 300
    palavras com tres fontes na frente, e mal um artigo inteiro com quinze. Cada
    secao e uma chamada com o contexto minimo dela: o proprio objetivo, as
    fontes que lhe cabem e o esqueleto para nao repetir o vizinho.

    **A revisao.** Quem revisa quase nunca quer refazer o artigo todo — quer
    refazer *aquela* secao que ficou rasa. Sem esta tabela, "refazer" so poderia
    significar jogar fora o texto inteiro, inclusive as partes boas.

    O texto publicado continua sendo o `body_markdown` do artigo: estas linhas
    sao o material de trabalho, e a montagem passa pelas mesmas travas de link
    e sanitizacao de sempre.
    """

    class Status(models.TextChoices):
        PLANNED = "planned", _("Planejada")
        WRITTEN = "written", _("Escrita")
        EDITED = "edited", _("Editada por humano")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="sections", verbose_name=_("artigo")
    )
    order = models.PositiveSmallIntegerField(_("ordem"))
    level = models.PositiveSmallIntegerField(_("nivel"), default=2)
    heading = models.CharField(_("titulo"), max_length=200)

    # O que esta secao precisa responder. Vem do planejamento e e o que impede
    # duas secoes de dizerem a mesma coisa sem nenhuma delas ver a outra.
    intent = models.TextField(_("objetivo"), blank=True)
    keywords = models.JSONField(_("palavras-chave"), default=list, blank=True)

    # Quais fontes cabem a esta secao. Guardado por id para o passo re-entrar
    # pelo banco, como todo o resto do orquestrador.
    chunk_ids = models.JSONField(_("fontes"), default=list, blank=True)

    body_markdown = models.TextField(_("texto"), blank=True)
    status = models.CharField(
        _("situacao"), max_length=8, choices=Status.choices, default=Status.PLANNED
    )
    prompt_run = models.ForeignKey(
        PromptRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sections",
        verbose_name=_("execucao"),
    )
    updated_at = models.DateTimeField(_("atualizada em"), auto_now=True)

    class Meta:
        verbose_name = _("secao do artigo")
        verbose_name_plural = _("secoes do artigo")
        ordering = ["article", "order"]
        constraints = [
            models.UniqueConstraint(fields=["article", "order"], name="uniq_secao_artigo_ordem")
        ]

    def __str__(self) -> str:
        return f"{self.order}. {self.heading}"

    @property
    def escrita(self) -> bool:
        return bool(self.body_markdown.strip())

    @property
    def palavras(self) -> int:
        return len(self.body_markdown.split())


class ArticleCitation(models.Model):
    """Qual trecho sustentou o artigo, e com que distancia foi recuperado."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="citations", verbose_name=_("artigo")
    )
    super_chunk = models.ForeignKey(
        "knowledge.SuperChunk",
        on_delete=models.SET_NULL,
        null=True,
        related_name="citations",
        verbose_name=_("trecho"),
    )
    rank = models.PositiveSmallIntegerField(_("posicao"))
    distance = models.FloatField(_("distancia"))
    used_as_primary = models.BooleanField(_("fonte primaria"), default=False)

    # Copiados no momento da citacao: o trecho pode ser desativado depois, e a
    # citacao precisa continuar auditavel.
    source_title = models.CharField(_("titulo"), max_length=500, blank=True)
    source_url = models.URLField(_("URL"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("citacao")
        verbose_name_plural = _("citacoes")
        ordering = ["article", "rank"]
        constraints = [
            models.UniqueConstraint(fields=["article", "rank"], name="uniq_citacao_por_posicao")
        ]

    def __str__(self) -> str:
        return f"#{self.rank} {self.source_title[:40]}"


class Question(models.Model):
    """Duvida deixada por um visitante do site.

    Duas decisoes de tratamento de dado, ambas com consequencia pratica:

    **O nome de quem perguntou nao e necessario para produzir o conteudo.** Ele
    so e guardado quando ha consentimento registrado no site de origem, e ainda
    assim pseudonimizado por padrao. Guardar o que nao se usa e risco sem
    contrapartida.

    **O texto tem prazo.** `retention_until` marca quando a pergunta deve ser
    apagada — perguntas costumam conter informacao pessoal que o produto nao
    precisa reter depois de responder.
    """

    class Status(models.TextChoices):
        IMPORTED = "imported", _("Importada")
        NEEDS_MORE_SOURCES = "needs_more_sources", _("Requer novas fontes")
        DRAFTING = "drafting", _("Em producao")
        PENDING_REVIEW = "pending_review", _("Aguardando revisao")
        ANSWERED = "answered", _("Respondida")
        DISCARDED = "discarded", _("Descartada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    site = models.ForeignKey(
        "integrations.Site",
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name=_("site"),
    )
    # Identificador do lado do site. A unicidade por site impede reimportar a
    # mesma pergunta a cada ciclo de coleta.
    remote_id = models.CharField(_("id remoto"), max_length=120)

    question_text = models.TextField(_("pergunta"), max_length=500)

    # Pseudonimo, nao o nome real, salvo consentimento explicito.
    author_pseudonym = models.CharField(_("identificacao"), max_length=80, blank=True)
    consent_at = models.DateTimeField(
        _("consentimento em"),
        null=True,
        blank=True,
        help_text=_("Sem isto, o nome nao e exibido nem guardado."),
    )

    submitted_at = models.DateTimeField(_("enviada em"))
    imported_at = models.DateTimeField(_("importada em"), default=timezone.now)

    status = models.CharField(
        _("situacao"), max_length=20, choices=Status.choices, default=Status.IMPORTED
    )
    best_similarity = models.FloatField(
        _("melhor similaridade"),
        null=True,
        blank=True,
        help_text=_("Menor distancia encontrada no acervo. Sustenta a regra do limiar."),
    )

    retention_until = models.DateTimeField(
        _("reter ate"),
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Apos esta data o texto e a identificacao sao apagados."),
    )
    purged_at = models.DateTimeField(_("expurgada em"), null=True, blank=True)

    class Meta:
        verbose_name = _("pergunta")
        verbose_name_plural = _("perguntas")
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["site", "remote_id"], name="uniq_pergunta_por_site")
        ]
        indexes = [models.Index(fields=["status", "-submitted_at"])]

    def __str__(self) -> str:
        return self.question_text[:60] or f"Pergunta {self.pk}"


class Answer(models.Model):
    """Conteudo produzido a partir de uma duvida.

    Enquadrado como conteudo informativo SOBRE O TEMA, e nao como resposta
    dirigida a pessoa. Isso reduz simultaneamente o risco regulatorio e a
    necessidade de tratar dado de terceiro.
    """

    class Status(models.TextChoices):
        DRAFTING = "drafting", _("Em producao")
        PENDING_REVIEW = "pending_review", _("Aguardando revisao")
        APPROVED_SCHEDULED = "approved_scheduled", _("Aprovada e agendada")
        PUBLISHED = "published", _("Publicada")
        PUSH_FAILED = "push_failed", _("Falha na publicacao")
        REJECTED = "rejected", _("Rejeitada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.OneToOneField(
        Question, on_delete=models.CASCADE, related_name="answer", verbose_name=_("pergunta")
    )

    body_markdown = models.TextField(_("corpo (markdown)"), blank=True)
    body_html = models.TextField(_("corpo (html)"), blank=True)

    outbound_link_url = models.URLField(_("link de saida"), max_length=500, blank=True)
    anchor_text = models.CharField(_("texto-ancora"), max_length=200, blank=True)

    status = models.CharField(
        _("situacao"), max_length=24, choices=Status.choices, default=Status.DRAFTING
    )

    author_name = models.CharField(_("autor"), max_length=150, blank=True)
    author_credentials = models.CharField(_("credenciais"), max_length=200, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_answers",
        verbose_name=_("revisada por"),
    )
    reviewed_at = models.DateTimeField(_("revisada em"), null=True, blank=True)
    review_seconds = models.PositiveIntegerField(_("segundos de revisao"), default=0)
    human_edit_ratio = models.FloatField(_("proporcao editada"), default=0.0)

    scheduled_for = models.DateTimeField(_("agendada para"), null=True, blank=True, db_index=True)
    published_at = models.DateTimeField(_("publicada em"), null=True, blank=True)
    published_url = models.URLField(_("URL publicada"), max_length=500, blank=True)
    remote_id = models.CharField(_("id remoto"), max_length=120, blank=True)

    idempotency_key = models.UUIDField(_("chave de idempotencia"), default=uuid.uuid4, unique=True)
    publish_attempts = models.PositiveSmallIntegerField(_("tentativas"), default=0)
    last_publish_error = models.TextField(_("ultimo erro"), blank=True)
    last_error_code = models.CharField(_("codigo do erro"), max_length=40, blank=True)
    next_retry_at = models.DateTimeField(_("proxima tentativa"), null=True, blank=True)

    created_at = models.DateTimeField(_("criada em"), default=timezone.now)

    class Meta:
        verbose_name = _("resposta")
        verbose_name_plural = _("respostas")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "scheduled_for"])]

    def __str__(self) -> str:
        return f"Resposta a {self.question}"


class AnswerCitation(models.Model):
    """Qual trecho sustentou a resposta."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    answer = models.ForeignKey(
        Answer, on_delete=models.CASCADE, related_name="citations", verbose_name=_("resposta")
    )
    super_chunk = models.ForeignKey(
        "knowledge.SuperChunk",
        on_delete=models.SET_NULL,
        null=True,
        related_name="answer_citations",
        verbose_name=_("trecho"),
    )
    rank = models.PositiveSmallIntegerField(_("posicao"))
    distance = models.FloatField(_("distancia"))
    used_as_primary = models.BooleanField(_("fonte primaria"), default=False)
    source_title = models.CharField(_("titulo"), max_length=500, blank=True)
    source_url = models.URLField(_("URL"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("citacao de resposta")
        verbose_name_plural = _("citacoes de resposta")
        ordering = ["answer", "rank"]
        constraints = [
            models.UniqueConstraint(fields=["answer", "rank"], name="uniq_citacao_resposta_posicao")
        ]

    def __str__(self) -> str:
        return f"#{self.rank} {self.source_title[:40]}"
