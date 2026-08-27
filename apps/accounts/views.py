"""Views do dominio raiz (schema public) e do painel de um tenant."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from apps.accounts.enderecos import url_do_tenant
from apps.accounts.forms import SignupForm, criar_tenant_e_dono
from apps.accounts.models import Tenant, TenantMembership
from apps.accounts.tasks import despachar_provisionamento

logger = logging.getLogger("publibot.accounts")


def landing(request: HttpRequest) -> HttpResponse:
    """Pagina inicial do dominio raiz."""
    ambientes = []
    if request.user.is_authenticated:
        vinculos = (
            TenantMembership.objects.filter(user=request.user, is_active=True)
            .select_related("tenant")
            .prefetch_related("tenant__domains")
            .order_by("tenant__name")
        )
        # O endereco vem junto porque sem ele esta lista nao serve para nada:
        # o subdominio nao e adivinhavel a partir do que a tela mostrava. O
        # tenant `acme` responde em `acme.publibot.localhost`, e nao em
        # `acme.localhost`, e nada na pagina dizia isso.
        for vinculo in vinculos:
            pronto = vinculo.tenant.status == Tenant.Status.ACTIVE
            ambientes.append(
                {
                    "tenant": vinculo.tenant,
                    "papel": vinculo.get_role_display(),
                    "pronto": pronto,
                    "url": url_do_tenant(request, vinculo.tenant)
                    if pronto
                    else reverse("accounts:provisioning", args=[vinculo.tenant.slug]),
                }
            )
    return render(request, "accounts/landing.html", {"ambientes": ambientes})


def signup(request: HttpRequest) -> HttpResponse:
    """Cadastro autonomo de um novo tenant."""
    if request.method != "POST":
        return render(request, "accounts/signup.html", {"form": SignupForm()})

    form = SignupForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/signup.html", {"form": form}, status=400)

    dados = form.cleaned_data

    # O registro no banco e a criacao do schema sao passos separados de
    # proposito. Aqui, tudo numa transacao: ou o tenant, o dominio, o usuario e
    # o vinculo existem juntos, ou nenhum existe.
    with transaction.atomic():
        tenant, usuario = criar_tenant_e_dono(
            subdomain=dados["subdomain"],
            organization=dados["organization"],
            full_name=dados["full_name"],
            email=dados["email"],
            senha=dados["password1"],
            root_domain=settings.ROOT_DOMAIN,
        )
        # `on_commit` garante que a task so seja despachada depois do COMMIT.
        # Sem isso, o worker pode buscar o tenant antes de ele existir para
        # outras conexoes e falhar com DoesNotExist — uma corrida que aparece
        # de forma intermitente e e desagradavel de diagnosticar.
        transaction.on_commit(lambda: despachar_provisionamento(str(tenant.pk), tenant.schema_name))

    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(reverse("accounts:provisioning", args=[tenant.slug]))


@login_required
def provisioning(request: HttpRequest, slug: str) -> HttpResponse:
    """Tela de espera enquanto o schema do tenant e criado."""
    tenant = get_object_or_404(Tenant, slug=slug)
    if not _pode_acessar(request.user, tenant):
        raise Http404

    return render(
        request,
        "accounts/provisioning.html",
        {"tenant": tenant, "url_do_painel": url_do_tenant(request, tenant)},
    )


@login_required
@require_GET
def provisioning_status(request: HttpRequest, slug: str) -> JsonResponse:
    """Estado do provisionamento, consultado pela tela de espera.

    Devolve apenas o necessario: quem nao pode acessar recebe 404, e nao uma
    resposta que confirme a existencia do tenant.
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    if not _pode_acessar(request.user, tenant):
        raise Http404

    corpo = {
        "status": tenant.status,
        "pronto": tenant.status == Tenant.Status.ACTIVE,
        "erro": tenant.provisioning_error or None,
    }

    # A tela so pede o diagnostico depois de esperar tempo suficiente para que
    # a demora deixe de ser normal. Criar um schema e rodar as migrations leva
    # dezenas de segundos; perguntar antes disso so acrescentaria uma ida ao
    # broker a cada 1,5s sem nada a dizer.
    if request.GET.get("diagnostico") and tenant.status == Tenant.Status.PROVISIONING:
        corpo["diagnostico"] = _diagnosticar_provisionamento(tenant)

    return JsonResponse(corpo)


def _diagnosticar_provisionamento(tenant: Tenant) -> str | None:
    """Explica por que um provisionamento nao termina.

    A causa de longe mais comum em desenvolvimento nao e um erro: e nao haver
    nenhum worker do Celery rodando. O despacho funciona, a mensagem fica na
    fila, e nada no console diz isso.
    """
    from apps.ops.broker import mensagens_pendentes

    pendentes = mensagens_pendentes()

    if pendentes is None:
        # Nao conseguimos ler a fila. Quase sempre broker fora do ar — mas
        # dizer "a fila esta vazia" aqui seria inventar.
        logger.error("Tenant %s parado e a fila esta ilegivel.", tenant.schema_name)
        if not settings.DEBUG:
            return None
        return _(
            "Nao foi possivel consultar a fila. Verifique se o broker "
            "(Redis, ou o PostgreSQL com BROKER_BACKEND=postgres) esta no ar."
        )

    if pendentes == 0:
        # A mensagem foi consumida: existe worker, e ele esta lento ou morreu
        # no meio. O log do worker e o proximo lugar a olhar.
        return None

    logger.error(
        "Tenant %s parado com %d mensagem(ns) na fila: nenhum worker consumindo.",
        tenant.schema_name,
        pendentes,
    )
    if not settings.DEBUG:
        return None
    # Nomear o broker importa: a segunda causa mais comum nao e a falta de
    # worker, e sim um worker ligado a OUTRO broker — um terminal aberto antes
    # de o .env mudar continua no broker antigo, e os dois lados parecem
    # saudaveis enquanto falam com filas diferentes.
    return _(
        "A mensagem esta na fila (%(broker)s) e ninguem a consumiu: nenhum "
        "worker do Celery esta ligado a ela. Pare este servidor e suba os dois "
        "processos juntos com  python manage.py dev  — ou deixe um segundo "
        "terminal aberto com  celery -A core worker -l INFO --concurrency=1 "
        "--prefetch-multiplier=1  . Para conferir: python manage.py broker_status"
    ) % {"broker": settings.BROKER_BACKEND}


def _pode_acessar(usuario, tenant: Tenant) -> bool:
    """Um usuario so enxerga um tenant se tiver vinculo ativo com ele.

    Sem esta checagem, qualquer pessoa autenticada leria o estado — e o erro de
    provisionamento — de qualquer tenant, bastando adivinhar o slug.
    """
    if usuario.is_superuser:
        return True
    return TenantMembership.objects.filter(tenant=tenant, user=usuario, is_active=True).exists()


@login_required
def painel(request: HttpRequest) -> HttpResponse:
    """Pagina inicial dentro de um tenant.

    Nao ha interface propria para operar o produto: o que existe hoje e o admin
    do Django, que ja enxerga os 25 models deste schema. Esta pagina deixa isso
    explicito e serve de ponto de partida, em vez de ser uma tela vazia que
    parece um erro. As contagens saem do banco do proprio tenant, entao ela
    tambem funciona como prova de que o isolamento por schema esta de pe.
    """
    from django.db import connection

    from apps.content.models import Article, Question
    from apps.integrations.models import Site
    from apps.knowledge.models import Document, SuperChunk

    artigos = dict(Article.objects.values_list("status").annotate(total=Count("status")).order_by())

    resumo = [
        (_("Documentos na base"), Document.objects.count(), "knowledge/document"),
        (_("Trechos indexados"), SuperChunk.objects.count(), "knowledge/superchunk"),
        (
            _("Artigos aguardando revisao"),
            artigos.get(Article.Status.PENDING_REVIEW, 0),
            "content/article",
        ),
        (
            _("Artigos publicados"),
            artigos.get(Article.Status.PUBLISHED, 0),
            "content/article",
        ),
        (
            _("Perguntas sem resposta"),
            Question.objects.filter(answer__isnull=True).count(),
            "content/question",
        ),
        (_("Sites conectados"), Site.objects.count(), "integrations/site"),
    ]

    return render(
        request,
        "accounts/painel.html",
        {"schema_name": connection.schema_name, "resumo": resumo},
    )
