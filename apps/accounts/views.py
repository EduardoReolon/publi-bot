"""Views do dominio raiz (schema public) e do painel de um tenant."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.accounts.forms import SignupForm, criar_tenant_e_dono
from apps.accounts.models import Tenant, TenantMembership
from apps.accounts.tasks import provision_tenant


def landing(request: HttpRequest) -> HttpResponse:
    """Pagina inicial do dominio raiz."""
    tenants = []
    if request.user.is_authenticated:
        tenants = (
            TenantMembership.objects.filter(user=request.user, is_active=True)
            .select_related("tenant")
            .order_by("tenant__name")
        )
    return render(request, "accounts/landing.html", {"memberships": tenants})


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
        transaction.on_commit(lambda: provision_tenant.delay(str(tenant.pk)))

    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(reverse("accounts:provisioning", args=[tenant.slug]))


@login_required
def provisioning(request: HttpRequest, slug: str) -> HttpResponse:
    """Tela de espera enquanto o schema do tenant e criado."""
    tenant = get_object_or_404(Tenant, slug=slug)
    if not _pode_acessar(request.user, tenant):
        raise Http404

    dominio = tenant.domains.filter(is_primary=True).first()
    porta = request.get_port()
    sufixo_porta = f":{porta}" if porta not in {"80", "443"} else ""
    esquema = "https" if request.is_secure() else "http"

    return render(
        request,
        "accounts/provisioning.html",
        {
            "tenant": tenant,
            "url_do_painel": f"{esquema}://{dominio.domain}{sufixo_porta}/" if dominio else None,
        },
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

    return JsonResponse(
        {
            "status": tenant.status,
            "pronto": tenant.status == Tenant.Status.ACTIVE,
            "erro": tenant.provisioning_error or None,
        }
    )


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
    """Pagina inicial dentro de um tenant."""
    from django.db import connection

    return render(
        request,
        "accounts/painel.html",
        {"schema_name": connection.schema_name},
    )
