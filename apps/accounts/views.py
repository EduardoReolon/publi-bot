"""Views do dominio raiz (schema public) e do painel de um tenant."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_tenants.utils import get_public_schema_name

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
        papeis = {v.tenant_id: (v.tenant, v.get_role_display()) for v in vinculos}
        if request.user.is_superuser:
            # O superusuario entra em todos os ambientes (o middleware ja deixa):
            # a lista mostra todos, inclusive os em que ele nao tem vinculo.
            for tenant in (
                Tenant.objects.exclude(schema_name=get_public_schema_name())
                .prefetch_related("domains")
                .order_by("name")
            ):
                papeis.setdefault(tenant.pk, (tenant, _("superusuario")))
            papeis = dict(sorted(papeis.items(), key=lambda item: item[1][0].name.lower()))
        for tenant, papel in papeis.values():
            pronto = tenant.status == Tenant.Status.ACTIVE
            ambientes.append(
                {
                    "tenant": tenant,
                    "papel": papel,
                    "pronto": pronto,
                    "url": url_do_tenant(request, tenant)
                    if pronto
                    else reverse("accounts:provisioning", args=[tenant.slug]),
                }
            )
    return render(
        request, "accounts/landing.html", {"ambientes": ambientes, **_contato_comercial()}
    )


def _contato_comercial() -> dict:
    """O WhatsApp comercial do .env, em link e no formato de leitura."""
    import re

    from django.conf import settings

    digitos = re.sub(r"\D", "", getattr(settings, "CONTATO_COMERCIAL_WHATSAPP", "") or "")
    if not digitos:
        return {}
    local = digitos.removeprefix("55") if len(digitos) > 11 else digitos
    legivel = f"({local[:2]}) {local[2:-4]}-{local[-4:]}" if len(local) >= 10 else local
    mensagem = "Ola! Vi o PubliBot e quero saber mais."
    return {
        "whatsapp_link": f"https://wa.me/55{local}?text={mensagem.replace(' ', '%20')}",
        "whatsapp_legivel": legivel,
    }


def signup(request: HttpRequest) -> HttpResponse:
    """Cadastro autonomo de um novo tenant."""
    logado = request.user if request.user.is_authenticated else None
    if request.method != "POST":
        return render(request, "accounts/signup.html", {"form": SignupForm(usuario=logado)})

    form = SignupForm(request.POST, usuario=logado)
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
            full_name=dados.get("full_name", ""),
            email=dados.get("email", ""),
            senha=dados.get("password1", ""),
            usuario=logado,
            root_domain=settings.ROOT_DOMAIN,
        )
        # `on_commit` garante que a task so seja despachada depois do COMMIT.
        # Sem isso, o worker pode buscar o tenant antes de ele existir para
        # outras conexoes e falhar com DoesNotExist — uma corrida que aparece
        # de forma intermitente e e desagradavel de diagnosticar.
        transaction.on_commit(lambda: despachar_provisionamento(str(tenant.pk), tenant.schema_name))

    if logado is None:
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
    """Painel do tenant: o que espera uma pessoa e o que quebrou.

    A separacao entre as duas coisas e deliberada. "Ha artigo para revisar" e
    trabalho normal; "um trabalho falhou" e defeito. Trata-las com o mesmo peso
    faria o painel virar uma lista de numeros que ninguem le — que e como um
    alerta de verdade passa despercebido.
    """
    from django.db import connection

    from apps.knowledge.saude import alertas_da_busca, montar_resumo_da_busca
    from apps.ops import extensoes
    from apps.ops.painel import alertas_da_indexacao, alertas_do_site, montar_resumo

    resumo = montar_resumo()

    # Os alertas da busca entram aqui, e nao so na tela deles: um limiar errado
    # nao produz sintoma nenhum na propria tela de busca — produz artigo ruim
    # do outro lado do sistema. Quem precisa ver isso nao esta procurando.
    busca = montar_resumo_da_busca()

    return render(
        request,
        "accounts/painel.html",
        {
            "aba": "painel",
            "schema_name": connection.schema_name,
            "resumo": resumo,
            "alertas": alertas_do_site(resumo.site)
            + alertas_da_busca(busca)
            + alertas_da_indexacao()
            + extensoes.alertas(),
            "busca": busca,
        },
    )


# -- Paginas publicas que as redes exigem (privacidade, termos, exclusao) ---------------
def privacidade(request: HttpRequest) -> HttpResponse:
    from apps.accounts.privacidade import contato

    return render(request, "accounts/privacidade.html", {"c": contato()})


def termos(request: HttpRequest) -> HttpResponse:
    from apps.accounts.privacidade import contato

    return render(request, "accounts/termos.html", {"c": contato()})


def exclusao_de_dados(request: HttpRequest) -> HttpResponse:
    """Como pedir a exclusao, e o andamento de um pedido (pelo codigo)."""
    from apps.accounts.models import PedidoDeExclusao
    from apps.accounts.privacidade import contato

    codigo = request.GET.get("codigo", "").strip()
    pedido = PedidoDeExclusao.objects.filter(codigo=codigo).first() if codigo else None
    return render(
        request,
        "accounts/exclusao.html",
        {"c": contato(), "codigo": codigo, "pedido": pedido},
    )


def _signed_request(request: HttpRequest) -> dict:
    from apps.accounts.privacidade import ler_signed_request

    return ler_signed_request(
        request.POST.get("signed_request", ""), getattr(settings, "SOCIAL_META_APP_SECRET", "")
    )


@csrf_exempt
@require_POST
def meta_exclusao(request: HttpRequest) -> HttpResponse:
    """Callback de exclusao de dados da Meta: apaga e devolve o acompanhamento."""
    from apps.accounts.privacidade import PedidoInvalido, registrar_exclusao

    try:
        dados = _signed_request(request)
    except PedidoInvalido as exc:
        return JsonResponse({"erro": str(exc)}, status=400)
    pedido = registrar_exclusao("instagram", str(dados["user_id"]))
    url = (
        f"{settings.ESQUEMA_PUBLICO}://{settings.ROOT_DOMAIN}"
        f"{reverse('accounts:exclusao_de_dados')}?codigo={pedido.codigo}"
    )
    return JsonResponse({"url": url, "confirmation_code": pedido.codigo})


@csrf_exempt
@require_POST
def meta_desautorizar(request: HttpRequest) -> HttpResponse:
    """Callback de "remover o app" da Meta: desconecta (nao apaga)."""
    from apps.accounts.privacidade import PedidoInvalido, excluir_em_todos_os_clientes

    try:
        dados = _signed_request(request)
    except PedidoInvalido as exc:
        return HttpResponse(str(exc), status=400)
    excluir_em_todos_os_clientes("instagram", str(dados["user_id"]), apagar=False)
    return HttpResponse("ok")


def retorno_oauth(request: HttpRequest) -> HttpResponse:
    """O retorno unico das conexoes (core/retorno_oauth.py): segue para o
    cliente do `state`, com o codigo, sem trocar nada aqui."""
    from django.core import signing

    from apps.accounts.models import Domain
    from core.retorno_oauth import ler

    if not request.GET.get("state"):
        # Aberto direto no navegador (ou na verificacao do app da rede): nao e
        # erro, so nao ha conexao em andamento.
        messages.info(
            request,
            _(
                "Este e o endereco de retorno das conexoes com as redes. Ele funciona "
                "sozinho quando voce conecta uma conta em Redes > Configurar."
            ),
        )
        return redirect("accounts:landing")
    try:
        estado = ler(request.GET.get("state", ""))
    except signing.BadSignature:
        messages.error(request, _("O pedido de conexao venceu ou nao e deste PubliBot."))
        return redirect("accounts:landing")
    dominio = (
        Domain.objects.filter(tenant__schema_name=estado.get("schema"))
        .order_by("-is_primary")
        .first()
    )
    volta = str(estado.get("volta") or "")
    if dominio is None or not volta.startswith("/"):
        return redirect("accounts:landing")
    return redirect(
        f"{settings.ESQUEMA_PUBLICO}://{dominio.domain}{volta}?{request.GET.urlencode()}"
    )
