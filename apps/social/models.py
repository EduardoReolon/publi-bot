"""Redes sociais: para onde vai (destino), como convencer (abordagem), o post
e o que voltou dele (cliques, comentarios).

Tudo mora no schema do tenant. Do nucleo, so o id do artigo (sem chave
estrangeira): o modulo le o artigo pela porta `fontes.py`, e um artigo apagado
nao apaga o historico do que foi postado.
"""

from __future__ import annotations

import json
import secrets
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _chave() -> str:
    return secrets.token_urlsafe(16)


class ConfiguracaoSocial(models.Model):
    """Uma linha por tenant: o que vale para todas as redes."""

    class Metrica(models.TextChoices):
        CLIQUES = "cliques", _("Cliques no link")
        CONVERSOES = "conversoes", _("Conversoes no site")
        ENGAJAMENTO = "engajamento", _("Engajamento na rede (curtidas, comentarios...)")
        TAXA = "taxa", _("Taxa sobre o alcance (salvos, compartilhamentos, comentarios, cliques)")

    id = models.SmallAutoField(primary_key=True)
    ligado = models.BooleanField(
        _("sugerir posts sozinho"),
        default=True,
        help_text=_(
            "Ligado, o PubliBot escolhe os artigos e escreve os posts uma vez por dia. "
            "Desligado, so quando voce pede ('Levar as redes', no artigo)."
        ),
    )
    metrica = models.CharField(
        _("o que conta como sucesso"),
        max_length=12,
        choices=Metrica.choices,
        default=Metrica.CLIQUES,
        help_text=_(
            "E por esta medida que o PubliBot aprende quais abordagens funcionam em cada rede."
        ),
    )
    variantes = models.PositiveSmallIntegerField(
        _("versoes por post"),
        default=1,
        help_text=_(
            "2: cada artigo ganha duas versoes (abordagens diferentes) para voce escolher "
            "uma — ou publicar as duas em dias diferentes e comparar."
        ),
    )
    reciclar_apos_meses = models.PositiveSmallIntegerField(
        _("repostar artigo que converte depois de (meses)"), default=3
    )
    espacamento_dias = models.PositiveSmallIntegerField(
        _("minimo de dias entre posts do mesmo artigo na mesma rede"), default=30
    )
    link_da_oferta = models.URLField(
        _("pagina da oferta (landing page)"),
        max_length=500,
        blank=True,
        help_text=_(
            "Opcional. Aparece como destino possivel do link de um post, junto da "
            "pagina inicial e dos artigos."
        ),
    )
    instrucoes = models.TextField(
        _("instrucoes para todas as redes"),
        blank=True,
        help_text=_(
            "O que vale em qualquer rede: como o negocio quer ser visto, o que evitar, "
            "como assinar. Ex.: 'Assine como Dra. Ana, cardiologista'."
        ),
    )
    regras = models.TextField(
        _("regras que nunca se quebram"),
        blank=True,
        default=(
            "Sem promessa de resultado ou de cura. Sem urgencia falsa ('so hoje', "
            "'ultimas vagas'). Sem antes e depois. Sem preco como chamariz. So afirme o "
            "que o artigo afirma, com os numeros dele."
        ),
        help_text=_("Regras do conselho profissional e do negocio. Vao em todo post."),
    )
    # Os numeros da estrategia que a pessoa mudou (ver apps/social/parametros.py).
    parametros = models.JSONField(_("parametros"), default=dict, blank=True)
    descrever_fotos = models.BooleanField(
        _("descrever as fotos com o modelo"),
        default=False,
        help_text=_(
            "Foto do banco sem nota ganha uma descricao do que aparece, feita por um modelo "
            "que enxerga imagem (precisa de uma conexao de inferencia com modelo de visao). "
            "Desligado: a legenda sai da nota e do Negocio."
        ),
    )

    class Meta:
        verbose_name = _("configuracao das redes")

    def __str__(self) -> str:
        return str(_("Configuracao das redes"))

    @classmethod
    def carregar(cls) -> ConfiguracaoSocial:
        objeto, _ = cls.objects.get_or_create(pk=1)
        return objeto


class Destino(models.Model):
    """Uma conta numa rede: o perfil pessoal no LinkedIn, a pagina da empresa,
    o Instagram, o perfil no Google. Cada um com publico, tom e aprovacao."""

    class Aprovacao(models.TextChoices):
        SEMPRE = "sempre", _("Todo post passa por mim")
        AUTOMATICO = "automatico", _("Publicar sozinho no horario")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rede = models.CharField(_("rede"), max_length=20)
    nome = models.CharField(_("nome"), max_length=120)
    autor = models.CharField(_("quem posta"), max_length=20, blank=True)
    ligado = models.BooleanField(_("ligado"), default=True)
    aprovacao = models.CharField(
        _("aprovacao"), max_length=12, choices=Aprovacao.choices, default=Aprovacao.SEMPRE
    )
    teto_semanal = models.PositiveSmallIntegerField(_("posts por semana, no maximo"), default=3)
    fotos_por_cento = models.PositiveSmallIntegerField(
        _("posts do banco de fotos (%)"),
        default=0,
        help_text=_(
            "Quanto dos posts automaticos desta conta vem do banco de fotos (o resto, dos "
            "artigos). 0: so artigos (consultoria, clinica). 80: quase tudo foto (barbearia, "
            "estetica). Entre 1 e 99, o PubliBot ajusta sozinho para o tipo que funciona mais."
        ),
    )
    mistura_ajustada_em = models.DateTimeField(null=True, blank=True)
    publico = models.TextField(
        _("quem esta nesta conta"),
        blank=True,
        help_text=_(
            "Quem segue e o que espera daqui. Ex.: 'Gestores de clinica e medicos, "
            "leem no intervalo, querem dado e opiniao de quem pratica'."
        ),
    )
    tom = models.CharField(_("tom"), max_length=200, blank=True)
    instrucoes = models.TextField(_("instrucoes desta conta"), blank=True)
    hashtags_fixas = models.CharField(_("hashtags fixas"), max_length=200, blank=True)
    # Dias da semana (0 = segunda) e horarios ("09:00") em que os posts saem.
    dias = models.JSONField(_("dias"), default=list, blank=True)
    horarios = models.JSONField(_("horarios"), default=list, blank=True)
    # Lâminas (carrossel): {"fundo": "#...", "texto": "#...", "destaque": "#..."}.
    cores = models.JSONField(_("cores das laminas"), default=dict, blank=True)
    chamada_final = models.CharField(_("ultima lamina / chamada"), max_length=160, blank=True)

    # Video (Reels/Shorts): a IA sugere carrossel ou video por post; aqui se
    # decide o que fazer com a sugestao, e com que voz narrar.
    class Video(models.TextChoices):
        SUGERIR = "sugerir", _("A IA sugere; eu gero o video quando quiser")
        AUTOMATICO = "automatico", _("Gerar o video sozinho quando a IA sugerir")
        SEMPRE = "sempre", _("Sempre video (todo post com laminas vira video)")
        NUNCA = "nunca", _("So carrossel (sem video)")

    video = models.CharField(
        _("video"), max_length=12, choices=Video.choices, default=Video.SUGERIR
    )
    voz = models.CharField(
        _("voz da narracao"),
        max_length=100,
        blank=True,
        help_text=_(
            "O id da voz no servico de voz (ElevenLabs: Voices > ID; OpenAI: alloy, nova...; "
            "Google: pt-BR-Neural2-B). Vazio: a voz padrao do servico."
        ),
    )
    # Identidade visual: o estilo das imagens das laminas (para a IA de imagem).
    legendas = models.BooleanField(
        _("legendas no video"),
        default=True,
        help_text=_("A fala escrita embaixo da lamina, um trecho por vez (quem ve sem som le)."),
    )
    # Os estilos que a conta aceita; a IA escolhe, post a post, o que serve
    # melhor ao assunto (e diz por que). Vazio: todos.
    estilos_permitidos = models.JSONField(_("estilos permitidos"), default=list, blank=True)
    orientacao_imagens = models.TextField(
        _("orientacoes so para as imagens"),
        blank=True,
        help_text=_(
            "Vale para as imagens das laminas (carrossel). Ex.: 'fundo liso, personagem a "
            "esquerda, espaco vazio embaixo para o texto'."
        ),
    )
    orientacao_videos = models.TextField(
        _("orientacoes so para os videos"),
        blank=True,
        help_text=_(
            "Vale para a animacao das cenas. Ex.: 'movimentos curtos e lentos, camera parada, "
            "os macacos piscam e gesticulam; nada de cortes rapidos'."
        ),
    )
    estilo_das_imagens = models.CharField(
        _("estilo preferido"),
        max_length=12,
        blank=True,
        choices=[
            ("", _("— (so cores e texto)")),
            ("foto", _("Foto realista")),
            ("ilustracao", _("Ilustracao (desenho)")),
            ("quadrinhos", _("Quadrinhos (cenas com personagens)")),
            ("apresentacao", _("Apresentacao (slide limpo, icones e graficos)")),
            ("misto", _("Misto: foto em alguns cenarios, desenho no resto")),
        ],
        help_text=_(
            "O de sempre, quando a IA nao escolher outro entre os permitidos. Vazio: so as "
            "cores e o texto."
        ),
    )
    descricao_do_estilo = models.TextField(
        _("orientacoes gerais (imagens e videos)"),
        blank=True,
        help_text=_(
            "Traco, cores, luz, personagens que se repetem, o que evitar. Ex.: 'ilustracao "
            "plana, tons de azul e areia, personagem: uma recepcionista de cabelo curto'."
        ),
    )
    cenarios_de_foto = models.TextField(
        _("quando usar foto realista (no misto)"),
        blank=True,
        help_text=_("Ex.: 'ambiente e equipamentos; nunca pessoas reconheciveis'."),
    )

    # --- Estrategia -------------------------------------------------------------
    # Pela API (Instagram, pagina do LinkedIn) ou digitado (perfil pessoal).
    seguidores = models.PositiveIntegerField(_("seguidores"), null=True, blank=True)
    # [{"dia": "2026-10-05", "n": 120}]: o crescimento por semana.
    seguidores_historico = models.JSONField(default=list, blank=True)
    # Vazio: a fase sai dos seguidores. Preenchido: a pessoa fixou a fase.
    fase_manual = models.CharField(_("fase (fixar)"), max_length=20, blank=True)
    hashtags_de_referencia = models.CharField(
        _("hashtags de referencia do nicho"),
        max_length=300,
        blank=True,
        help_text=_(
            "Instagram: ate 10, separadas por virgula. O PubliBot le os posts que mais "
            "engajam nelas (de outras contas) uma vez por semana, para saber que temas e "
            "formatos funcionam no nicho antes de voce ter seguidores."
        ),
    )
    referencias_em = models.DateTimeField(null=True, blank=True)

    # --- Historico e leitura automatica ----------------------------------------
    # Ultima leitura automatica que deu certo (seguidores, resultado): o painel
    # avisa quando para, e diz o erro.
    sincronizado_em = models.DateTimeField(null=True, blank=True)
    erro_de_sincronia = models.TextField(blank=True)
    historico_importado_em = models.DateTimeField(null=True, blank=True)
    # O resultado da conta, quando a rede so mede assim (Google: mes a mes):
    # {"meses": [{"mes": "2026-09", "visualizacoes": 900, ...}], "avaliacoes": {...}}.
    desempenho = models.JSONField(default=dict, blank=True)
    historico_planilha_em = models.DateTimeField(null=True, blank=True)
    # Meta Ads: a conta de anuncios (act_...) lida pela API, ou a planilha.
    anuncios_conta_id = models.CharField(_("conta de anuncios"), max_length=60, blank=True)
    anuncios_conta_nome = models.CharField(max_length=200, blank=True)
    anuncios_sincronizados_em = models.DateTimeField(null=True, blank=True)
    anuncios_planilha_em = models.DateTimeField(null=True, blank=True)
    anuncios_erro = models.TextField(blank=True)

    # --- Conexao com a API ----------------------------------------------------
    conta_id = models.CharField(_("id da conta na rede"), max_length=200, blank=True)
    conta_nome = models.CharField(_("conta conectada"), max_length=200, blank=True)
    # Quem conectou, pelo id da rede (Meta: o id por app que vem no pedido de
    # exclusao de dados).
    usuario_remoto = models.CharField(max_length=120, blank=True, db_index=True)
    credenciais = models.BinaryField(_("credenciais (cifradas)"), null=True, blank=True)
    expira_em = models.DateTimeField(_("acesso vence em"), null=True, blank=True)
    conectado_em = models.DateTimeField(_("conectado em"), null=True, blank=True)
    ultimo_erro = models.TextField(_("ultimo erro"), blank=True)
    # O endereco publico de "link na bio" (Instagram), sem login.
    chave_publica = models.CharField(max_length=40, default=_chave, unique=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["rede", "nome"]
        verbose_name = _("destino")
        verbose_name_plural = _("destinos")

    def __str__(self) -> str:
        return self.nome

    @property
    def publicos_ativos(self):
        return self.publicos.filter(ativo=True)

    @property
    def rede_nome(self) -> str:
        from apps.social.redes import REDES

        return REDES[self.rede].nome if self.rede in REDES else self.rede

    @property
    def conectado(self) -> bool:
        return bool(self.credenciais) and bool(self.conta_id)

    def ler_credenciais(self) -> dict:
        from apps.inference.security import decifrar

        texto = decifrar(self.credenciais)
        return json.loads(texto) if texto else {}

    def gravar_credenciais(self, dados: dict) -> None:
        from apps.inference.security import cifrar

        self.credenciais = cifrar(json.dumps(dados))


class Abordagem(models.Model):
    """Um jeito de convencer: o gancho que faz a pessoa parar de rolar a tela.

    E o que mais vai mudar, entao e dado, nao codigo: a pessoa cria, edita e
    desliga pela tela, e o PubliBot mede qual funciona em cada conta."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(_("nome"), max_length=80, unique=True)
    instrucao = models.TextField(
        _("o que pedir a IA"),
        help_text=_("Como escrever com esta abordagem. Vai inteiro no pedido ao modelo."),
    )
    # Redes em que vale (vazio = todas).
    redes = models.JSONField(_("redes"), default=list, blank=True)
    ativa = models.BooleanField(_("ativa"), default=True)
    semente = models.BooleanField(default=False, editable=False)

    class Origem(models.TextChoices):
        PUBLIBOT = "", _("PubliBot ou voce")
        OUTRA_IA = "outra_ia", _("Sugerida pela outra IA")

    # De onde veio: o placar compara as que a outra IA criou com as demais.
    origem = models.CharField(max_length=12, choices=Origem.choices, blank=True, default="")
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nome"]
        verbose_name = _("abordagem")
        verbose_name_plural = _("abordagens")

    def __str__(self) -> str:
        return self.nome

    def vale_para(self, rede: str) -> bool:
        return not self.redes or rede in self.redes


class Recado(models.Model):
    """Uma frase de posicionamento que entra em PARTE dos posts ("aqui tudo tem
    fonte", "sem sensacionalismo"), para medir se ajuda: o PubliBot compara os
    posts com e sem o recado na mesma conta. Dado, nao codigo: a pessoa cria,
    edita e desliga pela tela."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(_("nome"), max_length=80, unique=True)
    instrucao = models.TextField(
        _("a ideia a passar"),
        help_text=_(
            "A ideia, nao a frase pronta: o PubliBot escreve com as palavras do post. "
            "Ex.: 'aqui o conteudo e checado em estudos, sem sensacionalismo'."
        ),
    )
    por_cento = models.PositiveSmallIntegerField(
        _("em quantos posts (%)"),
        default=20,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("Em parte dos posts, para comparar com os que ficam sem."),
    )
    redes = models.JSONField(_("redes"), default=list, blank=True)
    ativo = models.BooleanField(_("ativo"), default=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome

    def vale_para(self, rede: str) -> bool:
        return not self.redes or rede in self.redes


class Tema(models.Model):
    """Um assunto que atravessa varios artigos (para o carrossel do Instagram,
    principalmente): as frases parecidas de artigos diferentes, juntas, com a
    nota de quanto o publico se interessa (dores, busca, conversao, nicho)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    titulo = models.CharField(_("tema"), max_length=400)
    # [{"artigo_id", "artigo_titulo", "frase"}]
    frases = models.JSONField(default=list, blank=True)
    artigos = models.JSONField(default=list, blank=True)
    artigo_principal = models.UUIDField(null=True, blank=True)
    nota = models.FloatField(default=0)
    # As parcelas da nota e o porque, para a tela.
    sinais = models.JSONField(default=dict, blank=True)
    centro = models.JSONField(default=list, blank=True)
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-nota"]

    def __str__(self) -> str:
        return self.titulo[:80]


class ReferenciaDoNicho(models.Model):
    """Um post de OUTRA conta, numa hashtag de referencia, com o engajamento
    dele. E o que funciona no nicho antes de a conta ter seguidores."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="referencias")
    hashtag = models.CharField(max_length=80)
    id_remoto = models.CharField(max_length=120)
    legenda = models.TextField(blank=True)
    formato = models.CharField(max_length=30, blank=True)
    curtidas = models.PositiveIntegerField(default=0)
    comentarios = models.PositiveIntegerField(default=0)
    link = models.URLField(max_length=500, blank=True)
    publicado_em = models.DateTimeField(null=True, blank=True)
    coletado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-curtidas"]
        constraints = [
            models.UniqueConstraint(fields=["destino", "id_remoto"], name="uniq_referencia")
        ]

    def __str__(self) -> str:
        return f"#{self.hashtag}: {self.legenda[:50]}"

    @property
    def engajamento(self) -> int:
        return self.curtidas + 3 * self.comentarios


class Post(models.Model):
    class Situacao(models.TextChoices):
        SUGERIDO = "sugerido", _("Sugerido")
        GERANDO = "gerando", _("Escrevendo")
        RASCUNHO = "rascunho", _("Para revisar")
        APROVADO = "aprovado", _("Aprovado (agendado)")
        PUBLICANDO = "publicando", _("Publicando")
        PUBLICADO = "publicado", _("Publicado")
        FALHOU = "falhou", _("Falhou")
        DESCARTADO = "descartado", _("Descartado")

    class Motivo(models.TextChoices):
        NOVO = "novo", _("Artigo novo")
        SUBINDO = "subindo", _("Quase na primeira pagina do Google")
        CONVERTE = "converte", _("Artigo que traz clientes")
        PEDIDO = "pedido", _("Pedido por voce")
        TEMA = "tema", _("Tema que atravessa varios artigos")
        TESTE = "teste", _("Teste pago de abordagem")
        OUTRA_IA = "outra_ia", _("Sugerido pela outra IA")
        CASO = "caso", _("Caso ou novidade sua")
        FOTOS = "fotos", _("Do banco de fotos")
        HISTORICO = "historico", _("Antes do PubliBot (importado)")
        IDEIA = "ideia", _("Ideia aprovada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="posts")
    artigo_id = models.UUIDField(_("artigo"), null=True, blank=True, db_index=True)
    artigo_titulo = models.CharField(_("titulo do artigo"), max_length=300, blank=True)
    artigo_url = models.URLField(_("endereco do artigo"), max_length=500, blank=True)
    abordagem = models.ForeignKey(
        Abordagem, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts"
    )
    # Post do material da propria pessoa (caso, novidade, fotos), e nao de artigo.
    entrada = models.ForeignKey(
        "Entrada", on_delete=models.SET_NULL, null=True, blank=True, related_name="posts"
    )
    # Post de um tema (varios artigos), e nao de um artigo so.
    tema = models.ForeignKey(
        Tema, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts"
    )
    motivo = models.CharField(_("motivo"), max_length=10, choices=Motivo.choices)
    por_que = models.TextField(_("por que este post"), blank=True)
    situacao = models.CharField(
        _("situacao"), max_length=10, choices=Situacao.choices, default=Situacao.SUGERIDO
    )
    texto = models.TextField(_("texto"), blank=True)
    # {"gancho", "hashtags": [...], "primeiro_comentario", "laminas": [{"titulo","texto"}]}
    extras = models.JSONField(_("extras"), default=dict, blank=True)
    # O que a conferencia apontou (numero que o artigo nao tem, texto longo...).
    avisos = models.JSONField(_("avisos"), default=list, blank=True)
    # O material do artigo que a IA recebeu (para conferir de onde saiu o texto).
    material = models.JSONField(_("material"), default=dict, blank=True)
    # Caminhos no storage das imagens (lamina 1, 2... ou a capa).
    imagens = models.JSONField(_("imagens"), default=list, blank=True)
    agendado_para = models.DateTimeField(_("agendado para"), null=True, blank=True)
    publicado_em = models.DateTimeField(_("publicado em"), null=True, blank=True)
    url_remota = models.URLField(_("endereco do post"), max_length=500, blank=True)
    id_remoto = models.CharField(_("id na rede"), max_length=300, blank=True)
    erro = models.TextField(_("erro"), blank=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    # Duas versoes do mesmo artigo para a mesma conta (abordagens diferentes).
    variante_de = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="variantes"
    )
    # Cliques contados pelo link do PubliBot; o resto, pela API da rede.
    cliques = models.PositiveIntegerField(_("cliques"), default=0)
    metricas = models.JSONField(_("resultado"), default=dict, blank=True)
    # Acima da mediana da conta na medida escolhida? None: cedo para dizer.
    sucesso = models.BooleanField(_("funcionou"), null=True, blank=True)
    # Impulso pago: cada impulso e um `Anuncio` (lido da Meta ou digitado); aqui
    # fica a soma, para filtrar e comparar sem juntar tabelas.
    impulsionado = models.BooleanField(_("impulsionado"), default=False)
    custo_impulso = models.DecimalField(
        _("valor do impulso (R$)"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    impulso_em = models.DateTimeField(null=True, blank=True)
    # A oferta para quem comentar (ex.: "comente QUERO e receba o guia").
    isca = models.ForeignKey(
        "Isca", on_delete=models.SET_NULL, null=True, blank=True, related_name="posts"
    )
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    # O link rastreado (/redes/r/<chave>/) e as imagens publicas usam esta chave.
    chave_publica = models.CharField(max_length=40, default=_chave, unique=True)
    criado_em = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["situacao", "agendado_para"])]

    def __str__(self) -> str:
        return f"{self.destino} — {self.artigo_titulo[:60]}"

    @property
    def principios_usados(self) -> list[tuple[str, bool]]:
        """[(nome, e o principal)] dos principios de Cialdini do post."""
        from apps.social.persuasao import PRINCIPIOS

        extras = self.extras or {}
        principal = extras.get("principio", "")
        return [
            (PRINCIPIOS[p]["nome"], p == principal)
            for p in extras.get("principios") or []
            if p in PRINCIPIOS
        ]

    @property
    def laminas_e_fundos(self) -> list[dict]:
        """Cada lamina e de onde vem o fundo dela (ver `identidade`)."""
        from apps.social import identidade

        return identidade.situacao_das_laminas(self)

    @property
    def pedido_de_imagens(self) -> str:
        from apps.social import identidade

        return identidade.pedido(self)

    @property
    def pedido_de_quadros(self) -> str:
        from apps.social import identidade

        return identidade.pedido_de_quadros(self)

    @property
    def pedido_de_animacao(self) -> str:
        from apps.social import identidade

        return identidade.pedido_de_animacao(self)

    @property
    def roteiro_do_video(self) -> str:
        from apps.social import video

        return video.roteiro(self)

    @property
    def falas_do_video(self) -> str:
        """Uma fala por linha, das laminas escritas (sem a chamada final)."""
        return "\n".join(
            (x.get("fala") or "").replace("\n", " ")
            for x in (self.extras or {}).get("laminas") or []
        )

    @property
    def no_foco(self) -> dict:
        """Como foi no seu foco, contra os da conta com o mesmo foco."""
        from apps.social import focos

        if self.situacao != self.Situacao.PUBLICADO:
            return {}
        return focos.desempenho(self)

    @property
    def gancho(self) -> str:
        return (self.extras or {}).get("gancho", "")


class Comentario(models.Model):
    """Um comentario num post publicado. Pergunta vira Pergunta do PubliBot;
    a resposta aprovada volta como resposta ao comentario."""

    class Tipo(models.TextChoices):
        PERGUNTA = "pergunta", _("Pergunta")
        ELOGIO = "elogio", _("Elogio")
        RECLAMACAO = "reclamacao", _("Reclamacao")
        RELATO = "relato", _("Relato")
        OUTRO = "outro", _("Outro")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comentarios")
    id_remoto = models.CharField(max_length=300)
    # So o primeiro nome: o nome inteiro nao acrescenta nada.
    autor = models.CharField(_("autor"), max_length=80, blank=True)
    texto = models.TextField(_("texto"))
    escrito_em = models.DateTimeField(_("escrito em"), null=True, blank=True)
    tipo = models.CharField(_("tipo"), max_length=12, choices=Tipo.choices, blank=True)
    # A Pergunta criada no PubliBot (id, sem chave estrangeira: e do nucleo).
    pergunta_id = models.UUIDField(null=True, blank=True)
    resposta = models.TextField(_("resposta enviada"), blank=True)
    # Pediu a isca do post? "sim", "talvez" (a pessoa decide no painel) ou
    # "nao" (decidido no painel); vazio: o post nao tem isca ou nao bateu.
    pedido = models.CharField(_("pediu a oferta"), max_length=8, blank=True)
    respondido_em = models.DateTimeField(null=True, blank=True)
    id_da_resposta = models.CharField(max_length=300, blank=True)
    erro = models.TextField(blank=True)
    lido_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-escrito_em"]
        constraints = [
            models.UniqueConstraint(fields=["post", "id_remoto"], name="uniq_comentario_por_post")
        ]

    def __str__(self) -> str:
        return self.texto[:60]


class Anuncio(models.Model):
    """Um anuncio pago (um impulso), com o gasto e o resultado do periodo todo.
    Vem da API (ads_read), da planilha exportada do Gerenciador ou digitado na
    tela; ligado ao post que impulsiona, quando da para saber qual. Um post
    pode ter varios (valores, datas e publicos diferentes)."""

    class Origem(models.TextChoices):
        API = "api", _("API da Meta")
        PLANILHA = "planilha", _("Planilha exportada")
        MANUAL = "manual", _("Digitado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="anuncios")
    id_remoto = models.CharField(max_length=200)
    nome = models.CharField(_("anuncio"), max_length=300, blank=True)
    campanha = models.CharField(_("campanha"), max_length=300, blank=True)
    objetivo = models.CharField(_("objetivo"), max_length=60, blank=True)
    gasto = models.DecimalField(_("gasto (R$)"), max_digits=12, decimal_places=2, default=0)
    alcance = models.PositiveIntegerField(null=True, blank=True)
    impressoes = models.PositiveIntegerField(null=True, blank=True)
    cliques = models.PositiveIntegerField(_("cliques no link"), null=True, blank=True)
    resultados = models.PositiveIntegerField(null=True, blank=True)
    inicio = models.DateField(null=True, blank=True)
    fim = models.DateField(null=True, blank=True)
    # O post do Instagram que o anuncio impulsiona (id na rede) e o criativo.
    media_id = models.CharField(max_length=120, blank=True)
    link = models.URLField(max_length=500, blank=True)
    imagem = models.URLField(max_length=1000, blank=True)
    texto = models.TextField(blank=True)
    post = models.ForeignKey(
        Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="anuncios"
    )
    publico = models.ForeignKey(
        "PublicoDeImpulso",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anuncios",
        verbose_name=_("publico"),
    )
    origem = models.CharField(max_length=10, choices=Origem.choices)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inicio", "id_remoto"]
        constraints = [
            models.UniqueConstraint(fields=["destino", "id_remoto"], name="uniq_anuncio")
        ]

    def __str__(self) -> str:
        return self.nome or self.id_remoto

    @property
    def dias(self) -> int | None:
        if not (self.inicio and self.fim):
            return None
        return (self.fim - self.inicio).days + 1

    @property
    def custo_por_clique(self):
        """Gasto / cliques no link, em reais (None sem clique)."""
        if not self.gasto or not self.cliques:
            return None
        return round(self.gasto / self.cliques, 2)


class PublicoDeImpulso(models.Model):
    """Um publico de impulso pago (o que se marca no Gerenciador de Anuncios:
    local, idade, interesses), salvo para reaproveitar e comparar: cada impulso
    (`Anuncio`) diz qual publico usou, e o resultado soma por publico."""

    class Origem(models.TextChoices):
        MANUAL = "manual", _("Digitado")
        OUTRA_IA = "outra_ia", _("Outra IA")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="publicos")
    nome = models.CharField(_("nome"), max_length=80)
    descricao = models.TextField(
        _("como marcar no Gerenciador"),
        blank=True,
        help_text=_("Local, idade, interesses, o que mais marcar."),
    )
    por_que = models.CharField(_("por que"), max_length=300, blank=True)
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.MANUAL)
    ativo = models.BooleanField(_("ativo"), default=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["destino", "nome"], name="uniq_publico_de_impulso")
        ]

    def __str__(self) -> str:
        return self.nome


class Isca(models.Model):
    """Uma oferta para quem comentar: "comente QUERO e receba ...". O negocio
    cadastra o que pode dar (guia, checklist, avaliacao gratis, aula); quem
    escreve o post usa quando o post e de engajamento; o comentario que pede
    recebe a resposta pronta, com o link rastreado."""

    class Modo(models.TextChoices):
        TODOS = "todos", _("Responder todo mundo que comentar, igual")
        PALAVRA = (
            "palavra",
            _("So quando a palavra aparece (maiuscula, acento e pontuacao nao importam)"),
        )
        APROXIMADO = (
            "aproximado",
            _("Palavra com erro de digitacao, 'quero', 'me manda'; o duvidoso vai ao painel"),
        )

    class Origem(models.TextChoices):
        MANUAL = "manual", _("Digitada")
        OUTRA_IA = "outra_ia", _("Outra IA")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(_("o que a pessoa recebe"), max_length=120, unique=True)
    descricao = models.TextField(
        _("como funciona"),
        blank=True,
        help_text=_("Vai para quem escreve o post: o que e, para quem serve, o que NAO prometer."),
    )
    palavra = models.CharField(
        _("palavra para comentar"),
        max_length=40,
        blank=True,
        help_text=_("Ex.: QUERO. Vazio: qualquer comentario pede."),
    )
    modo = models.CharField(
        _("como reconhecer o pedido"), max_length=12, choices=Modo.choices, default=Modo.APROXIMADO
    )
    resposta = models.TextField(
        _("resposta ao comentario"),
        help_text=_("Use {nome} para o primeiro nome e {link} para o link rastreado."),
    )
    link = models.URLField(_("link da entrega"), max_length=500, blank=True)
    responder_sozinho = models.BooleanField(
        _("responder sozinho"),
        default=True,
        help_text=_("Desligado: os pedidos esperam no painel, um clique para responder."),
    )
    redes = models.JSONField(_("redes"), default=list, blank=True)
    ativa = models.BooleanField(_("ativa"), default=True)
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.MANUAL)
    cliques = models.PositiveIntegerField(default=0)
    chave_publica = models.CharField(max_length=40, default=_chave, unique=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class RespostaDaOutraIA(models.Model):
    """Um pedido feito a outra IA por API (no lugar de copiar e colar) e a
    resposta, que passa pelos mesmos leitores da resposta colada. Roda em
    segundo plano: a IA grande leva de segundos a minutos."""

    class Tipo(models.TextChoices):
        PROPOSTAS = "propostas", _("Propostas de posts")
        POST = "post", _("Post inteiro")
        ISCAS = "iscas", _("Ideias de iscas")
        PUBLICOS = "publicos", _("Publico do impulso")

    class Situacao(models.TextChoices):
        ESPERANDO = "esperando", _("Esperando a resposta")
        PRONTA = "pronta", _("Pronta para ver")
        APLICADA = "aplicada", _("Aplicada")
        ERRO = "erro", _("Erro")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    destino = models.ForeignKey(
        Destino, on_delete=models.CASCADE, null=True, blank=True, related_name="respostas_ia"
    )
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, null=True, blank=True, related_name="respostas_ia"
    )
    pedido = models.TextField()
    resposta = models.TextField(blank=True)
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.ESPERANDO)
    erro = models.TextField(blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} ({self.get_situacao_display()})"


def _caminho_do_fundo(instancia, nome: str) -> str:
    import os

    extensao = os.path.splitext(nome)[1].lower()[:5] or ".png"
    return f"social/fundos/{instancia.destino_id}/{instancia.pk}{extensao}"


class FundoDeLamina(models.Model):
    """Um fundo padrao das laminas da conta (o "modelo de slide"): o da capa,
    o dos do meio, o da ultima — ou, entre os do meio, um por tipo ("dado",
    "pergunta", "passo a passo"). O PubliBot escolhe qual vai em cada lamina e
    escreve o texto por cima."""

    class Papel(models.TextChoices):
        CAPA = "capa", _("Capa (1a lamina)")
        MEIO = "meio", _("Laminas do meio")
        FINAL = "final", _("Ultima lamina")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="fundos")
    papel = models.CharField(_("onde usar"), max_length=6, choices=Papel.choices)
    etiqueta = models.CharField(
        _("tipo (opcional)"),
        max_length=40,
        blank=True,
        help_text=_("So nas do meio: dado, pergunta, lista, dica... Vazio: qualquer uma."),
    )
    arquivo = models.ImageField(upload_to=_caminho_do_fundo, max_length=300)
    cor_texto = models.CharField(_("cor do texto neste fundo"), max_length=7, blank=True)
    cor_destaque = models.CharField(_("cor do titulo neste fundo"), max_length=7, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["papel", "etiqueta", "criado_em"]

    def __str__(self) -> str:
        return f"{self.get_papel_display()} {self.etiqueta}".strip()


def _caminho_do_elemento(instancia, nome: str) -> str:
    import os

    extensao = os.path.splitext(nome)[1].lower()[:5] or ".png"
    return f"social/elementos/{instancia.destino_id}/{instancia.pk}{extensao}"


class ElementoVisual(models.Model):
    """Um personagem, cenario ou objeto fixo da conta ("Gugu, o gorila de
    terno azul"): quem escreve o post o chama pelo nome na cena da lamina, e o
    pedido de imagem leva a descricao inteira — sempre a mesma, que e o que da
    consistencia. A imagem de referencia e para anexar na IA que aceita."""

    class Tipo(models.TextChoices):
        PERSONAGEM = "personagem", _("Personagem")
        CENARIO = "cenario", _("Cenario")
        OBJETO = "objeto", _("Objeto")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destino = models.ForeignKey(Destino, on_delete=models.CASCADE, related_name="elementos")
    tipo = models.CharField(_("tipo"), max_length=12, choices=Tipo.choices)
    nome = models.CharField(_("nome"), max_length=60)
    descricao = models.TextField(
        _("como e, sempre igual"),
        help_text=_(
            "A aparencia fixa, em detalhe: especie/idade, roupa, cores, acessorios, traco. "
            "Ex.: 'gorila adulto de terno azul-marinho, gravata amarela, oculos redondos'."
        ),
    )
    imagem = models.ImageField(
        _("imagem de referencia (opcional)"), upload_to=_caminho_do_elemento, blank=True
    )
    ativo = models.BooleanField(_("ativo"), default=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["tipo", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["destino", "nome"], name="uniq_elemento_por_conta")
        ]

    def __str__(self) -> str:
        return self.nome

    @property
    def prompts_de_folha(self) -> list:
        from apps.social.identidade import prompts_de_folha

        return prompts_de_folha(self.nome, self.descricao, self.tipo)


def _caminho_da_midia(instancia, nome: str) -> str:
    import os

    extensao = os.path.splitext(nome)[1].lower()[:6] or ".bin"
    return f"social/midias/{timezone.now():%Y/%m}/{instancia.pk}{extensao}"


class Midia(models.Model):
    """Uma foto ou video da propria pessoa: do banco de fotos (agenda sozinho)
    ou anexado a um post proprio. A foto e guardada ja normalizada (girada,
    no maximo 2048 px, sem os metadados — inclusive a localizacao)."""

    class Tipo(models.TextChoices):
        FOTO = "foto", _("Foto")
        VIDEO = "video", _("Video")

    class Situacao(models.TextChoices):
        NOVA = "nova", _("No banco")
        USADA = "usada", _("Ja usada")
        DESCARTADA = "descartada", _("Descartada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tipo = models.CharField(max_length=6, choices=Tipo.choices, default=Tipo.FOTO)
    arquivo = models.FileField(upload_to=_caminho_da_midia, max_length=300)
    # Do banco de fotos (o PubliBot agenda) ou so de um post proprio.
    banco = models.BooleanField(default=False)
    nota = models.TextField(_("nota"), blank=True)
    # O que aparece na foto, escrito pelo modelo de visao (opcional).
    descricao = models.TextField(blank=True)
    descricao_tentada = models.BooleanField(default=False)
    tirada_em = models.DateTimeField(null=True, blank=True)
    largura = models.PositiveIntegerField(null=True, blank=True)
    altura = models.PositiveIntegerField(null=True, blank=True)
    nitidez = models.FloatField(null=True, blank=True)
    brilho = models.FloatField(null=True, blank=True)
    # dHash de 64 bits (hex): fotos quase iguais tem assinaturas proximas.
    assinatura = models.CharField(max_length=16, blank=True)
    # Fotos do mesmo atendimento (tiradas perto uma da outra) viram um carrossel.
    grupo = models.UUIDField(null=True, blank=True, db_index=True)
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.NOVA)
    motivo = models.CharField(_("por que descartada"), max_length=200, blank=True)
    # Quem aparece autorizou o uso da imagem (ou nao aparece ninguem).
    autorizada = models.BooleanField(default=False)
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["tirada_em", "criada_em"]

    def __str__(self) -> str:
        return self.nota[:50] or self.arquivo.name


class Entrada(models.Model):
    """O material que a propria pessoa manda: um caso real, uma novidade, ou um
    grupo de fotos do banco. E o "artigo" dos posts que nao vem de artigo: o
    post so afirma o que esta aqui."""

    class Tipo(models.TextChoices):
        CASO = "caso", _("Caso real")
        NOVIDADE = "novidade", _("Novidade ou bastidor")
        COMENTARIO = "comentario", _("Noticia ou estudo comentado")
        FOTOS = "fotos", _("Fotos do trabalho")

    class Transcricao(models.TextChoices):
        NENHUMA = "", _("Sem audio")
        ESPERANDO = "esperando", _("Transcrevendo")
        PRONTA = "pronta", _("Transcrita")
        FALHOU = "falhou", _("Falhou")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    texto = models.TextField(_("o que aconteceu"), blank=True)
    audio = models.FileField(upload_to="social/audios/%Y/%m/", max_length=300, blank=True)
    transcricao = models.TextField(blank=True)
    situacao_do_audio = models.CharField(
        max_length=10, choices=Transcricao.choices, blank=True, default=""
    )
    midias = models.ManyToManyField(Midia, blank=True, related_name="entradas")
    # Para onde o post leva: um artigo relacionado (id do nucleo) ou um endereco.
    artigo_id = models.UUIDField(null=True, blank=True)
    link = models.URLField(_("link"), max_length=500, blank=True)
    # De onde veio, quando nao foi a pessoa aqui: "ideia:<id>" (caixa de ideias).
    # E o que impede a mesma ideia de virar dois posts na mesma conta.
    origem = models.CharField(max_length=80, blank=True, db_index=True)
    # Paginas de terceiros que o PubliBot le (noticia, estudo): [{"url", "titulo",
    # "site", "texto"} ou {"url", "erro"}]. Sem "texto" nem "erro": ainda lendo.
    referencias = models.JSONField(default=list, blank=True)
    autorizado = models.BooleanField(default=False)
    # As contas que recebem o post (esperando a transcricao, quando ha audio).
    destinos = models.JSONField(default=list, blank=True)
    criada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-criada_em"]

    def __str__(self) -> str:
        return self.titulo

    @property
    def relato(self) -> str:
        """Tudo o que o post pode afirmar: o texto, o audio transcrito e as notas
        (e descricoes) das fotos."""
        partes = [self.texto, self.transcricao]
        for midia in self.midias.all():
            partes += [midia.nota, midia.descricao]
        return "\n".join(p.strip() for p in partes if p and p.strip())

    @property
    def titulo(self) -> str:
        primeira = next((linha.strip() for linha in self.relato.splitlines() if linha.strip()), "")
        if primeira:
            return primeira[:120]
        return str(self.get_tipo_display())
