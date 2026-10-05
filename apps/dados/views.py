"""A tela do catalogo de dados publicos. Ver e usar: todos; curar (aprovar
serie, cadastrar a mao, marcar confiavel): so superusuario, porque o catalogo
e compartilhado por todos os clientes."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.views.decorators.http import require_POST

from apps.dados.adaptadores import situacao_do_adaptador
from apps.dados.locais import normalizar
from apps.dados.models import Instituicao, PedidoDeAdaptador, Serie, Valor

NICHOS = [
    ("saude", _l("Saude")),
    ("ia", _l("IA e tecnologia")),
    ("obras", _l("Obras e engenharia")),
    ("geral", _l("Geral")),
]
ABAS = ("series", "instituicoes", "pedidos")
POR_PAGINA = 50


def _curador(request) -> None:
    if not request.user.is_superuser:
        raise PermissionDenied


def _de_volta(request, aba: str = "series") -> HttpResponse:
    return redirect(request.POST.get("voltar") or f"{_url()}?aba={aba}")


def _url() -> str:
    from django.urls import reverse

    return reverse("dados:catalogo")


@login_required
def catalogo(request: HttpRequest) -> HttpResponse:
    aba = request.GET.get("aba") if request.GET.get("aba") in ABAS else "series"
    nicho = request.GET.get("nicho", "")
    situacao = request.GET.get("situacao", "")
    busca = request.GET.get("q", "").strip()

    series = Serie.objects.select_related("instituicao").annotate(n_valores=Count("valores"))
    if nicho:
        series = series.filter(
            Q(nichos__contains=[nicho]) | Q(instituicao__nichos__contains=[nicho])
        )
    if situacao:
        series = series.filter(situacao=situacao)
    if busca:
        series = series.filter(Q(titulo__icontains=busca) | Q(descricao__icontains=busca))
    from django.core.paginator import Paginator

    from apps.dados.catalogo import formatar

    total_filtrado = series.count()
    pagina = Paginator(
        series.order_by("situacao", "-citacoes", "instituicao__sigla", "titulo"), POR_PAGINA
    ).get_page(request.GET.get("pagina", 1))
    series = list(pagina)
    for serie in series:
        serie.ultimo = serie.valores.order_by("-periodo").first() if serie.n_valores else None
        if serie.ultimo:
            serie.ultimo.texto = formatar(serie.ultimo.valor)

    instituicoes = list(Instituicao.objects.annotate(n_series=Count("series")).order_by("sigla"))
    if nicho:
        instituicoes = [i for i in instituicoes if nicho in i.nichos]
    for instituicao in instituicoes:
        instituicao.estado = situacao_do_adaptador(instituicao)

    return render(
        request,
        "dados/catalogo.html",
        {
            "aba": "dados",
            "aba_do_catalogo": aba,
            "series": series,
            "pagina": pagina,
            "total_filtrado": total_filtrado,
            "filtros_da_url": _filtros_da_url(request),
            "instituicoes": instituicoes,
            "todas_as_instituicoes": Instituicao.objects.order_by("sigla"),
            "pedidos": _pedidos(),
            "contagens": {
                "series": Serie.objects.count(),
                "instituicoes": Instituicao.objects.count(),
                "pedidos": PedidoDeAdaptador.objects.filter(
                    situacao=PedidoDeAdaptador.Situacao.ABERTO
                ).count(),
                "por_aprovar": Serie.objects.filter(situacao=Serie.Situacao.SUGERIDA).count(),
            },
            "nichos": NICHOS,
            "nicho": nicho,
            "situacao": situacao,
            "situacoes": Serie.Situacao.choices,
            "busca": busca,
            "curador": request.user.is_superuser,
        },
    )


def _filtros_da_url(request) -> str:
    """A URL atual sem a pagina, para os links das paginas manterem os filtros."""
    copia = request.GET.copy()
    copia.pop("pagina", None)
    texto = copia.urlencode()
    return f"{texto}&" if texto else ""


def _pedidos() -> list:
    from apps.dados.acervo import texto_do_pedido

    pedidos = list(PedidoDeAdaptador.objects.filter(situacao=PedidoDeAdaptador.Situacao.ABERTO))
    for pedido in pedidos:
        pedido.texto = texto_do_pedido(pedido)
    return pedidos


@login_required
@require_POST
def situacao_da_serie(request: HttpRequest, pk) -> HttpResponse:
    _curador(request)
    serie = get_object_or_404(Serie, pk=pk)
    nova = request.POST.get("situacao")
    if nova in Serie.Situacao.values:
        serie.situacao = nova
        serie.save(update_fields=["situacao"])
    return _de_volta(request)


def _numero(texto: str) -> Decimal | None:
    """Aceita 25,9 · 1.234,5 · 1234.5."""
    texto = (texto or "").strip().replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


@login_required
@require_POST
def nova_serie(request: HttpRequest) -> HttpResponse:
    """Cadastro a mao: a serie e um valor. Para o que ainda nao tem adaptador."""
    _curador(request)
    instituicao = get_object_or_404(Instituicao, pk=request.POST.get("instituicao"))
    titulo = request.POST.get("titulo", "").strip()
    numero = _numero(request.POST.get("valor", ""))
    periodo = request.POST.get("periodo", "").strip()
    if not titulo or numero is None or not periodo:
        messages.error(request, _("Preencha titulo, periodo e um valor numerico."))
        return _de_volta(request)
    serie, _nova = Serie.objects.get_or_create(
        instituicao=instituicao,
        codigo=request.POST.get("codigo", "").strip() or f"manual-{slugify(titulo)[:100]}",
        defaults={
            "titulo": titulo,
            "descricao": request.POST.get("descricao", "").strip(),
            "unidade": request.POST.get("unidade", "").strip(),
            "url": request.POST.get("url", "").strip(),
            "origem": Serie.Origem.MANUAL,
            "situacao": Serie.Situacao.APROVADA,
        },
    )
    Valor.objects.update_or_create(
        serie=serie,
        local=normalizar(request.POST.get("local", "")),
        periodo=periodo,
        defaults={"valor": numero},
    )
    messages.success(request, _("Serie cadastrada: %(t)s.") % {"t": serie.titulo})
    return _de_volta(request)


@login_required
@require_POST
def confiavel(request: HttpRequest, pk) -> HttpResponse:
    _curador(request)
    instituicao = get_object_or_404(Instituicao, pk=pk)
    instituicao.confiavel = not instituicao.confiavel
    instituicao.save(update_fields=["confiavel"])
    return _de_volta(request, "instituicoes")


@login_required
@require_POST
def situacao_do_pedido(request: HttpRequest, pk) -> HttpResponse:
    _curador(request)
    pedido = get_object_or_404(PedidoDeAdaptador, pk=pk)
    if request.POST.get("situacao") in PedidoDeAdaptador.Situacao.values:
        pedido.situacao = request.POST["situacao"]
        pedido.notas = request.POST.get("notas", pedido.notas)
        pedido.save(update_fields=["situacao", "notas"])
    return _de_volta(request, "pedidos")


@login_required
@require_POST
def procurar(request: HttpRequest, pk) -> HttpResponse:
    """Procura no catalogo da instituicao e grava o que achar como sugerida."""
    from apps.dados.catalogo import procurar_e_sugerir

    _curador(request)
    instituicao = get_object_or_404(Instituicao, pk=pk)
    termo = request.POST.get("termo", "").strip()
    try:
        series = procurar_e_sugerir(instituicao, termo)
    except Exception as exc:  # rede, formato que mudou: avisa, nao derruba a tela
        messages.error(
            request,
            _("A busca em %(i)s falhou: %(e)s") % {"i": instituicao.sigla, "e": exc},
        )
        return _de_volta(request, "instituicoes")
    if series:
        messages.success(
            request,
            _("%(n)s serie(s) de %(i)s para aprovar (filtro 'Sugerida').")
            % {"n": len(series), "i": instituicao.sigla},
        )
        return redirect(f"{_url()}?aba=series&situacao=sugerida&q=")
    messages.info(request, _("Nada encontrado para '%(t)s'.") % {"t": termo})
    return _de_volta(request, "instituicoes")


@login_required
@require_POST
def situacao_em_lote(request: HttpRequest) -> HttpResponse:
    """Aprovar ou recusar varias series de uma vez (as marcadas na lista)."""
    _curador(request)
    nova = request.POST.get("situacao")
    if nova not in Serie.Situacao.values:
        return _de_volta(request)
    if request.POST.get("todas_sugeridas"):
        # Todas as sugeridas do filtro atual (nicho, instituicao e busca), nao so a pagina.
        consulta = Serie.objects.filter(situacao=Serie.Situacao.SUGERIDA)
        nicho = request.POST.get("nicho", "")
        if nicho:
            consulta = consulta.filter(
                Q(nichos__contains=[nicho]) | Q(instituicao__nichos__contains=[nicho])
            )
        busca = request.POST.get("q", "").strip()
        if busca:
            consulta = consulta.filter(Q(titulo__icontains=busca) | Q(descricao__icontains=busca))
        n = consulta.update(situacao=nova)
    else:
        n = Serie.objects.filter(pk__in=request.POST.getlist("serie")).update(situacao=nova)
    if n:
        messages.success(request, _("%(n)s serie(s) atualizadas.") % {"n": n})
    return _de_volta(request)
