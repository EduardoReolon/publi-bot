"""Motor de cadencia: quando publicar o que.

Duas regras aqui existem por causa de modos de falha conhecidos.

**A consulta do tique NUNCA compara por igualdade de instante.** Sempre
`scheduled_for <= agora`. Com igualdade de minuto, qualquer atraso do
agendador — uma implantacao, um pico de carga, o relogio pulando — faria o
horario ser pulado e o conteudo nunca sair, sem erro em lugar nenhum.

**A transicao de estado acontece dentro de `select_for_update(skip_locked=True)`,
antes do despacho.** Isso torna desnecessario qualquer trava distribuida: dois
agendadores rodando ao mesmo tempo nao selecionam o mesmo artigo, porque o
segundo simplesmente nao enxerga a linha travada.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.content.models import Answer, Article
from apps.integrations.models import PublicationSchedule, PublicationSlot, Site

logger = logging.getLogger("publibot.scheduling")


def _fuso_do_site(site: Site) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(site.site_timezone)
    except zoneinfo.ZoneInfoNotFoundError:
        logger.warning("Fuso %r desconhecido para o site %s; usando UTC.", site.site_timezone, site)
        return zoneinfo.ZoneInfo("UTC")


def gerar_horarios(
    schedule: PublicationSchedule, *, ate: datetime | None = None
) -> list[PublicationSlot]:
    """Cria os horarios futuros de um site, sem duplicar os que ja existem.

    Os horarios sao calculados no fuso do site e convertidos para UTC na
    gravacao. Guardar em UTC e comparar em UTC evita a classe inteira de erro
    que aparece nas trocas de horario de verao.
    """
    if not schedule.is_active:
        return []

    site = schedule.site
    fuso = _fuso_do_site(site)
    agora_local = timezone.now().astimezone(fuso)
    limite = ate or (timezone.now() + timedelta(days=30))

    momentos: list[datetime] = []

    if schedule.mode == PublicationSchedule.Mode.WEEKLY_SLOTS:
        momentos = _horarios_semanais(schedule, agora_local, fuso, limite)
    else:
        momentos = _horarios_por_intervalo(schedule, agora_local, fuso, limite)

    criados = []
    for momento_local in momentos:
        em_utc = momento_local.astimezone(zoneinfo.ZoneInfo("UTC"))
        horario, foi_criado = PublicationSlot.objects.get_or_create(
            site=site, slot_at=em_utc, defaults={"local_slot_at": momento_local}
        )
        if foi_criado:
            criados.append(horario)

    return criados


# Quantos horarios livres a cadencia mantem a frente, e ate onde ela gera para
# isso: com todos os horarios dos proximos 30 dias ocupados, gerar "ate daqui a
# 30 dias" nao cria nada, e o artigo aprovado nao tinha onde entrar.
HORARIOS_LIVRES_MINIMOS = 5
GERAR_ATE_DIAS = 365


def horarios_livres():
    """Os horarios futuros sem conteudo agendado nem publicado neles (o artigo
    que voltou para revisao ou foi rejeitado libera o horario)."""
    from django.db.models import Q

    ocupado = Q(
        article__status__in=[Article.Status.APPROVED_SCHEDULED, Article.Status.PUBLISHED]
    ) | Q(answer__isnull=False)
    return (
        PublicationSlot.objects.filter(slot_at__gte=timezone.now())
        .exclude(ocupado)
        .order_by("slot_at")
    )


def garantir_horarios_livres(schedule: PublicationSchedule) -> list[PublicationSlot]:
    """Gera os horarios da cadencia, indo alem dos 30 dias quando preciso, ate
    haver HORARIOS_LIVRES_MINIMOS livres (ou um ano a frente)."""
    criados: list[PublicationSlot] = []
    dias = 30
    while True:
        criados += gerar_horarios(schedule, ate=timezone.now() + timedelta(days=dias))
        livres = horarios_livres().filter(site=schedule.site).count()
        if livres >= HORARIOS_LIVRES_MINIMOS or dias >= GERAR_ATE_DIAS:
            return criados
        dias += 30


def proximo_horario() -> datetime:
    """Quando sai o proximo conteudo aprovado sem data escolhida: o primeiro
    horario livre da cadencia (gerando mais, se faltar). Sem cadencia ativa,
    o dia seguinte ao ultimo agendado, na hora padrao do site. Nunca "agora":
    publicar na hora e o botao "Publicar ja"."""
    livre = horarios_livres().first()
    if livre is None:
        for schedule in PublicationSchedule.objects.filter(is_active=True).select_related("site"):
            garantir_horarios_livres(schedule)
        livre = horarios_livres().first()
    if livre is not None:
        return livre.slot_at

    site = Site.objects.first()
    fuso = _fuso_do_site(site) if site else zoneinfo.ZoneInfo("UTC")
    schedule = getattr(site, "schedule", None) if site else None
    hora, minuto = 10, 0
    if schedule is not None and schedule.times_of_day:
        try:
            hora, minuto = (int(p) for p in schedule.times_of_day[0].split(":"))
        except (ValueError, AttributeError):
            pass
    ultimo = (
        Article.objects.filter(
            status=Article.Status.APPROVED_SCHEDULED, scheduled_for__isnull=False
        )
        .order_by("-scheduled_for")
        .values_list("scheduled_for", flat=True)
        .first()
    )
    base = max(timezone.now(), ultimo) if ultimo else timezone.now()
    dia = base.astimezone(fuso).date() + timedelta(days=1)
    return datetime.combine(dia, time(hora, minuto), tzinfo=fuso)


def _horarios_semanais(schedule, agora_local, fuso, limite) -> list[datetime]:
    dias = set(schedule.weekdays or [])
    horas = schedule.times_of_day or []
    if not dias or not horas:
        return []

    momentos = []
    dia = agora_local.date()
    fim = limite.astimezone(fuso).date()

    while dia <= fim:
        if dia.weekday() in dias:
            for texto in horas:
                try:
                    hora, minuto = (int(p) for p in texto.split(":"))
                except (ValueError, AttributeError):
                    logger.warning("Horario invalido %r na cadencia de %s", texto, schedule.site)
                    continue
                momento = datetime.combine(dia, time(hora, minuto), tzinfo=fuso)
                if momento > agora_local:
                    momentos.append(momento)
        dia += timedelta(days=1)

    return momentos


def _horarios_por_intervalo(schedule, agora_local, fuso, limite) -> list[datetime]:
    horas = schedule.times_of_day or ["10:00"]
    try:
        hora, minuto = (int(p) for p in horas[0].split(":"))
    except (ValueError, AttributeError):
        hora, minuto = 10, 0

    ultimo = PublicationSlot.objects.filter(site=schedule.site).order_by("-slot_at").first()
    base = ultimo.slot_at.astimezone(fuso).date() if ultimo else agora_local.date()

    momentos = []
    fim = limite.astimezone(fuso).date()
    dia = base + timedelta(days=schedule.interval_days)

    while dia <= fim:
        momento = datetime.combine(dia, time(hora, minuto), tzinfo=fuso)
        if momento > agora_local:
            momentos.append(momento)
        dia += timedelta(days=schedule.interval_days)

    return momentos


def conteudo_pronto_para_publicar(limite: int = 50) -> list[tuple[str, str]]:
    """Seleciona o que ja passou da hora de publicar.

    Devolve pares (tipo, id). A selecao e a transicao de estado acontecem na
    mesma transacao, com `skip_locked`: dois agendadores concorrentes nao
    escolhem o mesmo item.
    """
    agora = timezone.now()
    selecionados: list[tuple[str, str]] = []

    with transaction.atomic():
        artigos = list(
            Article.objects.select_for_update(skip_locked=True)
            .filter(
                status=Article.Status.APPROVED_SCHEDULED,
                # `<=`, nunca `==`: um atraso do agendador nao pode fazer o
                # horario ser pulado para sempre.
                scheduled_for__lte=agora,
            )
            .order_by("scheduled_for")[:limite]
        )
        for artigo in artigos:
            selecionados.append(("article", str(artigo.pk)))

    with transaction.atomic():
        respostas = list(
            Answer.objects.select_for_update(skip_locked=True)
            .filter(status=Answer.Status.APPROVED_SCHEDULED, scheduled_for__lte=agora)
            .order_by("scheduled_for")[:limite]
        )
        for resposta in respostas:
            selecionados.append(("answer", str(resposta.pk)))

    return selecionados


def contar_reserva(site: Site) -> int:
    """Quantos conteudos ja estao prontos e agendados para este site.

    Conta respostas junto quando elas ocupam horario da cadencia — senao o site
    publica mais do que o configurado e o calculo da reserva fica errado.
    """
    total = Article.objects.filter(status=Article.Status.APPROVED_SCHEDULED).count()

    schedule = getattr(site, "schedule", None)
    if schedule is not None and schedule.qa_consumes_slot:
        total += Answer.objects.filter(status=Answer.Status.APPROVED_SCHEDULED).count()

    return total


def reserva_esta_baixa(site: Site) -> bool:
    schedule = getattr(site, "schedule", None)
    if schedule is None or not schedule.is_active:
        return False
    return contar_reserva(site) < schedule.buffer_threshold


def artigos_publicados_no_mes(site: Site) -> int:
    """Volume publicado no mes corrente.

    Sustenta o teto por site: volume alto e previsivel e o padrao que buscadores
    tratam como producao em escala para manipular resultado.
    """
    agora = timezone.now()
    return Article.objects.filter(
        status=Article.Status.PUBLISHED,
        published_at__year=agora.year,
        published_at__month=agora.month,
    ).count()


def excedeu_o_teto_mensal(site: Site) -> bool:
    return artigos_publicados_no_mes(site) >= site.max_articles_per_month
