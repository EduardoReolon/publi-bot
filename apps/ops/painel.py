"""O que esta esperando uma pessoa, e o que deu errado.

Um numero so aparece aqui se alguem puder fazer algo a respeito. Contagem que
nao leva a uma acao vira ruido, e ruido faz o painel deixar de ser lido — que e
o modo como um alerta de verdade passa despercebido.

Todas as consultas rodam no schema do tenant em uso. Nao ha filtro por cliente
em lugar nenhum porque nao existe outro cliente alcancavel daqui: o isolamento
e o proprio schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


@dataclass
class Pendencia:
    """Uma linha do painel: um numero, o que ele significa e onde resolver."""

    rotulo: str
    total: int
    url: str
    # `atencao` distingue "ha trabalho a fazer" de "algo quebrou". Sao coisas
    # diferentes e o painel nao pode trata-las com o mesmo peso.
    atencao: bool = False
    detalhe: str = ""


@dataclass
class ResumoDoPainel:
    acoes: list[Pendencia] = field(default_factory=list)
    problemas: list[Pendencia] = field(default_factory=list)
    acervo: list[Pendencia] = field(default_factory=list)
    proximas_publicacoes: list = field(default_factory=list)
    site = None

    @property
    def tem_problema(self) -> bool:
        return any(p.total for p in self.problemas)


def contagens_de_pendencia() -> dict[str, int]:
    """Numeros do menu. Barato de proposito: roda em toda requisicao."""
    from apps.content.models import Article, Question
    from apps.knowledge.models import Document
    from apps.ops.models import GenerationJob

    return {
        "curadoria": Document.objects.filter(status=Document.Status.PENDING_CURATION).count(),
        "revisao": Article.objects.filter(status=Article.Status.PENDING_REVIEW).count(),
        "perguntas": Question.objects.filter(answer__isnull=True)
        .exclude(status=Question.Status.DISCARDED)
        .count(),
        "falhas": GenerationJob.objects.filter(status=GenerationJob.Status.FAILED).count()
        + Article.objects.filter(status=Article.Status.PUSH_FAILED).count(),
    }


def montar_resumo() -> ResumoDoPainel:
    from apps.content.models import Article, Question
    from apps.integrations.models import Site
    from apps.knowledge.models import Document, SuperChunk
    from apps.ops.models import GenerationJob

    resumo = ResumoDoPainel()
    resumo.site = Site.objects.first()

    por_situacao = dict(
        Article.objects.values_list("status").annotate(total=Count("status")).order_by()
    )

    # --- o que espera uma pessoa -------------------------------------------
    resumo.acoes = [
        Pendencia(
            rotulo=_("Artigos aguardando revisao"),
            total=por_situacao.get(Article.Status.PENDING_REVIEW, 0),
            url=reverse("content:artigos") + "?situacao=pending_review",
            detalhe=str(_("Nada e publicado sem passar por aqui.")),
        ),
        Pendencia(
            rotulo=_("Documentos aguardando curadoria"),
            total=Document.objects.filter(status=Document.Status.PENDING_CURATION).count(),
            url=reverse("knowledge:documentos") + "?situacao=pending_curation",
            detalhe=str(_("Convertidos, ainda fora do indice.")),
        ),
        Pendencia(
            rotulo=_("Perguntas sem resposta"),
            total=Question.objects.filter(answer__isnull=True)
            .exclude(status=Question.Status.DISCARDED)
            .count(),
            url=reverse("content:perguntas"),
        ),
        Pendencia(
            rotulo=_("Pautas aprovadas sem artigo"),
            total=_pautas_sem_artigo(),
            url=reverse("content:pautas") + "?situacao=approved",
        ),
    ]

    # --- o que quebrou -----------------------------------------------------
    resumo.problemas = [
        Pendencia(
            rotulo=_("Trabalhos que falharam"),
            total=GenerationJob.objects.filter(status=GenerationJob.Status.FAILED).count(),
            url=reverse("operacao:trabalhos") + "?situacao=failed",
            atencao=True,
        ),
        Pendencia(
            rotulo=_("Falhas ao publicar"),
            total=por_situacao.get(Article.Status.PUSH_FAILED, 0),
            url=reverse("content:artigos") + "?situacao=push_failed",
            atencao=True,
        ),
        Pendencia(
            rotulo=_("Documentos com falha"),
            total=Document.objects.filter(status=Document.Status.FAILED).count(),
            url=reverse("knowledge:documentos") + "?situacao=failed",
            atencao=True,
        ),
        Pendencia(
            rotulo=_("Aguardando capacidade"),
            total=GenerationJob.objects.filter(
                status=GenerationJob.Status.WAITING_CAPACITY
            ).count(),
            url=reverse("operacao:trabalhos") + "?situacao=waiting_capacity",
            detalhe=str(_("Nao e erro: a fila esta esperando a GPU.")),
        ),
    ]

    # --- estado do acervo ---------------------------------------------------
    resumo.acervo = [
        Pendencia(
            rotulo=_("Documentos indexados"),
            total=Document.objects.filter(
                status__in=[Document.Status.CURATED, Document.Status.EMBEDDED]
            ).count(),
            url=reverse("knowledge:documentos"),
        ),
        Pendencia(
            rotulo=_("Trechos no indice"),
            total=SuperChunk.objects.filter(is_active=True).count(),
            url=reverse("knowledge:documentos"),
        ),
        Pendencia(
            rotulo=_("Agendados para publicar"),
            total=por_situacao.get(Article.Status.APPROVED_SCHEDULED, 0),
            url=reverse("content:artigos") + "?situacao=approved_scheduled",
        ),
        Pendencia(
            rotulo=_("Publicados"),
            total=por_situacao.get(Article.Status.PUBLISHED, 0),
            url=reverse("content:artigos") + "?situacao=published",
        ),
    ]

    resumo.proximas_publicacoes = list(
        Article.objects.filter(
            status=Article.Status.APPROVED_SCHEDULED,
            scheduled_for__gte=timezone.now(),
        ).order_by("scheduled_for")[:5]
    )

    return resumo


def _pautas_sem_artigo() -> int:
    from apps.content.models import Topic

    return Topic.objects.filter(status=Topic.Status.APPROVED, articles__isnull=True).count()


def alertas_do_site(site) -> list[str]:
    """Condicoes que travam a publicacao mesmo com conteudo aprovado.

    Sao silenciosas por natureza: o conteudo fica pronto, o horario passa e
    nada acontece. Sem este aviso, a primeira pista seria alguem notar que o
    site parou de receber artigos.
    """
    from apps.integrations.scheduling import excedeu_o_teto_mensal, reserva_esta_baixa

    if site is None:
        return [
            str(
                _(
                    "Nenhum site cadastrado. Sem ele nada pode ser publicado — "
                    "cadastre em Site e cadencia."
                )
            )
        ]

    avisos: list[str] = []

    if site.publishing_paused:
        avisos.append(str(_("A publicacao esta pausada neste site.")))

    if site.circuit_open_until and site.circuit_open_until > timezone.now():
        avisos.append(
            str(_("O site acumulou falhas seguidas e o circuito esta aberto ate %(quando)s."))
            % {"quando": site.circuit_open_until.strftime("%d/%m %H:%M")}
        )

    schedule = getattr(site, "schedule", None)
    if schedule is None:
        avisos.append(str(_("Nenhuma cadencia configurada: nada sera publicado sozinho.")))
    elif not schedule.is_active:
        avisos.append(str(_("A cadencia esta desativada.")))
    elif reserva_esta_baixa(site):
        avisos.append(str(_("A reserva de conteudo aprovado esta abaixo do minimo configurado.")))

    if excedeu_o_teto_mensal(site):
        avisos.append(
            str(_("O teto de artigos do mes ja foi atingido; o restante fica para o proximo."))
        )

    return avisos
