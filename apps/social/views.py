"""As telas das redes: a fila (como as pautas), a agenda, o que saiu, os
comentarios, o que funciona e a configuracao. Mais tres enderecos publicos,
sem login: o link rastreado, as imagens (a rede busca por URL) e o link na bio.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import F
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.social import experimentos
from apps.social.abordagens import garantir_abordagens
from apps.social.forms import AbordagemForm, ConfiguracaoForm, DestinoForm
from apps.social.models import Abordagem, Comentario, ConfiguracaoSocial, Destino, Post
from apps.social.redes import REDES, rede
from apps.social.redes.base import ErroDaRede

logger = logging.getLogger("publibot.social")

ABAS = ("revisar", "agenda", "publicados", "comentarios", "funciona", "configurar")
SAL_DO_OAUTH = "social-oauth"


def _voltar(request, aba: str = "revisar") -> HttpResponse:
    return redirect(request.POST.get("voltar") or f"{reverse('social:inicio')}?aba={aba}")


@login_required
def inicio(request: HttpRequest) -> HttpResponse:
    garantir_abordagens()
    aba = request.GET.get("aba") if request.GET.get("aba") in ABAS else "revisar"
    destinos = list(Destino.objects.all())
    filtro = request.GET.get("destino", "")
    posts = Post.objects.select_related("destino", "abordagem")
    if filtro:
        posts = posts.filter(destino_id=filtro)

    contexto = {
        "aba": "redes",
        "aba_das_redes": aba,
        "destinos": destinos,
        "filtro": filtro,
        "redes": REDES,
        "config": ConfiguracaoSocial.carregar(),
        "contagens": {
            "revisar": Post.objects.filter(
                situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.GERANDO, Post.Situacao.RASCUNHO]
            ).count(),
            "agenda": Post.objects.filter(
                situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICANDO]
            ).count(),
            "comentarios": Comentario.objects.filter(
                tipo=Comentario.Tipo.PERGUNTA, respondido_em__isnull=True
            ).count(),
        },
    }
    if aba == "revisar":
        contexto["posts"] = posts.filter(
            situacao__in=[
                Post.Situacao.SUGERIDO,
                Post.Situacao.GERANDO,
                Post.Situacao.RASCUNHO,
                Post.Situacao.FALHOU,
            ]
        ).order_by("destino__nome", "-criado_em")
        contexto["abordagens"] = Abordagem.objects.filter(ativa=True)
    elif aba == "agenda":
        contexto["posts"] = posts.filter(
            situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICANDO]
        ).order_by("agendado_para")
        contexto["agora"] = timezone.now()
    elif aba == "publicados":
        contexto["posts"] = posts.filter(situacao=Post.Situacao.PUBLICADO).order_by(
            "-publicado_em"
        )[:100]
    elif aba == "comentarios":
        contexto["comentarios"] = Comentario.objects.select_related("post__destino").order_by(
            F("respondido_em").asc(nulls_first=True), "-escrito_em"
        )[:200]
    elif aba == "funciona":
        contexto["quadros"] = [(d, experimentos.quadro(d)) for d in destinos]
        contexto["metrica"] = contexto["config"].get_metrica_display()
    else:
        contexto["form_config"] = ConfiguracaoForm(instance=contexto["config"])
        contexto["form_destino"] = DestinoForm()
        contexto["form_abordagem"] = AbordagemForm()
        contexto["abordagens"] = Abordagem.objects.all()
        contexto["retorno"] = request.build_absolute_uri(reverse("social:retorno"))
        for destino in destinos:
            destino.form = DestinoForm(instance=destino, prefix=str(destino.pk))
            destino.bio = request.build_absolute_uri(
                reverse("social:bio", args=[destino.chave_publica])
            )
            destino.rede_obj = rede(destino.rede)
            destino.vence_logo = bool(
                destino.expira_em and destino.expira_em <= timezone.now() + timedelta(days=7)
            )
    return render(request, "social/inicio.html", contexto)


# -- Posts ---------------------------------------------------------------------------
def _laminas_do_formulario(texto: str) -> list[dict]:
    """Uma lamina por linha: "titulo | texto"."""
    laminas = []
    for linha in (texto or "").splitlines():
        if not linha.strip():
            continue
        titulo, _sep, corpo = linha.partition("|")
        laminas.append({"titulo": titulo.strip()[:120], "texto": corpo.strip()[:220]})
    return laminas


@login_required
@require_POST
def acao_no_post(request: HttpRequest, pk) -> HttpResponse:
    from apps.social import fontes, laminas, publicacao
    from apps.social.tasks import escrever_post

    post = get_object_or_404(Post.objects.select_related("destino", "abordagem"), pk=pk)
    acao = request.POST.get("acao")

    if acao == "salvar":
        post.texto = request.POST.get("texto", post.texto).strip()
        extras = dict(post.extras or {})
        if "primeiro_comentario" in request.POST:
            extras["primeiro_comentario"] = request.POST["primeiro_comentario"].strip()
        if "laminas" in request.POST:
            extras["laminas"] = _laminas_do_formulario(request.POST["laminas"])
        post.extras = extras
        post.save(update_fields=["texto", "extras", "atualizado_em"])
        if "laminas" in request.POST:
            artigo = fontes.artigo(post.artigo_id)
            if artigo is not None:
                laminas.preparar_imagens(post, artigo)
        messages.success(request, _("Post salvo."))
    elif acao == "aprovar":
        quando = parse_datetime(request.POST.get("quando", "") or "")
        if quando is not None and timezone.is_naive(quando):
            quando = timezone.make_aware(quando)
        publicacao.aprovar(post, por=request.user, quando=quando)
        messages.success(
            request,
            _("Aprovado para %(q)s.")
            % {"q": timezone.localtime(post.agendado_para).strftime("%d/%m %H:%M")},
        )
    elif acao == "descartar":
        post.situacao = Post.Situacao.DESCARTADO
        post.save(update_fields=["situacao", "atualizado_em"])
        messages.info(request, _("Descartado."))
    elif acao == "reescrever":
        abordagem = Abordagem.objects.filter(pk=request.POST.get("abordagem") or None).first()
        if abordagem is None:
            outras = [a for a in experimentos.escolher(post.destino, 3) if a != post.abordagem]
            abordagem = outras[0] if outras else post.abordagem
        post.abordagem = abordagem
        post.situacao = Post.Situacao.SUGERIDO
        post.save(update_fields=["abordagem", "situacao", "atualizado_em"])
        escrever_post.delay(str(post.pk))
        messages.info(request, _("Reescrevendo com a abordagem '%(a)s'.") % {"a": abordagem})
    elif acao == "voltar_para_revisao":
        post.situacao = Post.Situacao.RASCUNHO
        post.agendado_para = None
        post.save(update_fields=["situacao", "agendado_para", "atualizado_em"])
    elif acao == "publicar_agora":
        try:
            publicacao.publicar(post)
            messages.success(request, _("Publicado."))
        except ErroDaRede as exc:
            messages.error(request, _("Nao publicou: %(e)s") % {"e": exc})
    elif acao == "ja_postei":
        url = request.POST.get("url", "").strip()
        publicacao.marcar_publicado(post, url=url)
        messages.success(request, _("Marcado como publicado. Os cliques do link ja contam."))
    return _voltar(request)


@login_required
@require_POST
def levar_as_redes(request: HttpRequest, artigo_id) -> HttpResponse:
    from apps.social.escolha import levar_as_redes as levar

    if not Destino.objects.filter(ligado=True).exists():
        messages.info(request, _("Cadastre uma conta em Redes > Configurar primeiro."))
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    garantir_abordagens()
    criados = levar(str(artigo_id))
    if criados:
        messages.success(
            request,
            _("%(n)s post(s) sendo escritos: acompanhe em Redes.") % {"n": len(criados)},
        )
    else:
        messages.info(request, _("Este artigo ja foi para todas as contas nos ultimos dias."))
    return redirect(request.POST.get("voltar") or reverse("social:inicio"))


# -- Configuracao ------------------------------------------------------------------
@login_required
@require_POST
def configurar(request: HttpRequest) -> HttpResponse:
    form = ConfiguracaoForm(request.POST, instance=ConfiguracaoSocial.carregar())
    if form.is_valid():
        form.save()
        messages.success(request, _("Configuracao salva."))
    else:
        messages.error(request, _("Confira os campos: %(e)s") % {"e": form.errors.as_text()})
    return _voltar(request, "configurar")


@login_required
@require_POST
def salvar_destino(request: HttpRequest, pk=None) -> HttpResponse:
    instancia = get_object_or_404(Destino, pk=pk) if pk else None
    form = DestinoForm(request.POST, instance=instancia, prefix=str(pk) if pk else None)
    if form.is_valid():
        destino = form.save()
        messages.success(request, _("Conta '%(n)s' salva.") % {"n": destino.nome})
    else:
        messages.error(request, _("Confira os campos: %(e)s") % {"e": form.errors.as_text()})
    return _voltar(request, "configurar")


@login_required
@require_POST
def salvar_abordagem(request: HttpRequest, pk=None) -> HttpResponse:
    instancia = get_object_or_404(Abordagem, pk=pk) if pk else None
    form = AbordagemForm(request.POST, instance=instancia)
    if form.is_valid():
        form.save()
        messages.success(request, _("Abordagem salva."))
    else:
        messages.error(request, _("Confira os campos: %(e)s") % {"e": form.errors.as_text()})
    return _voltar(request, "configurar")


# -- Conectar a conta (OAuth) ---------------------------------------------------------
@login_required
def conectar(request: HttpRequest, pk) -> HttpResponse:
    destino = get_object_or_404(Destino, pk=pk)
    r = rede(destino.rede)
    estado = signing.dumps(
        {"destino": str(destino.pk), "schema": connection.schema_name}, salt=SAL_DO_OAUTH
    )
    try:
        url = r.oauth().url_de_autorizacao(
            destino, request.build_absolute_uri(reverse("social:retorno")), estado
        )
    except ErroDaRede as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    return redirect(url)


@login_required
def retorno(request: HttpRequest) -> HttpResponse:
    """A rede devolve aqui, com o codigo. Troca pelo acesso e escolhe a conta."""
    try:
        estado = signing.loads(request.GET.get("state", ""), salt=SAL_DO_OAUTH, max_age=900)
    except signing.BadSignature:
        messages.error(
            request, _("O pedido de conexao venceu ou nao e deste PubliBot. Tente de novo.")
        )
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    destino = get_object_or_404(Destino, pk=estado["destino"])
    if request.GET.get("error"):
        messages.error(
            request,
            _("A rede recusou: %(e)s")
            % {"e": request.GET.get("error_description") or request.GET["error"]},
        )
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    oauth = rede(destino.rede).oauth()
    try:
        credenciais = oauth.trocar_codigo(
            destino,
            request.GET.get("code", ""),
            request.build_absolute_uri(reverse("social:retorno")),
        )
        oauth.gravar(destino, credenciais)
        contas = oauth.contas(destino)
    except ErroDaRede as exc:
        destino.ultimo_erro = str(exc)[:2000]
        destino.save(update_fields=["ultimo_erro"])
        messages.error(request, _("Nao conectou: %(e)s") % {"e": exc})
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    if len(contas) == 1:
        destino.conta_id, destino.conta_nome = contas[0]
        destino.save(update_fields=["conta_id", "conta_nome"])
        messages.success(request, _("Conectado: %(c)s.") % {"c": destino.conta_nome})
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    if not contas:
        messages.error(
            request,
            _(
                "Conectou, mas a rede nao mostrou nenhuma conta "
                "(pagina, perfil profissional ou local)."
            ),
        )
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    return render(
        request, "social/conta.html", {"aba": "redes", "destino": destino, "contas": contas}
    )


@login_required
@require_POST
def escolher_conta(request: HttpRequest, pk) -> HttpResponse:
    destino = get_object_or_404(Destino, pk=pk)
    conta_id = request.POST.get("conta", "")
    nome = request.POST.get(f"nome_{conta_id}", conta_id)
    destino.conta_id, destino.conta_nome = conta_id[:200], nome[:200]
    destino.save(update_fields=["conta_id", "conta_nome"])
    messages.success(request, _("Conectado: %(c)s.") % {"c": destino.conta_nome})
    return redirect(f"{reverse('social:inicio')}?aba=configurar")


@login_required
@require_POST
def desconectar(request: HttpRequest, pk) -> HttpResponse:
    destino = get_object_or_404(Destino, pk=pk)
    destino.credenciais = None
    destino.conta_id = destino.conta_nome = ""
    destino.expira_em = None
    destino.save(update_fields=["credenciais", "conta_id", "conta_nome", "expira_em"])
    messages.info(request, _("Desconectado. Os posts desta conta ficam no 'Copiar para postar'."))
    return redirect(f"{reverse('social:inicio')}?aba=configurar")


# -- Publicos (sem login) --------------------------------------------------------------
def clique(request: HttpRequest, chave: str) -> HttpResponse:
    """O link do post: conta o clique e segue para o artigo, com a origem marcada."""
    from apps.social.redacao import destino_do_clique

    post = Post.objects.select_related("destino", "abordagem").filter(chave_publica=chave).first()
    if post is None or not post.artigo_url:
        raise Http404
    Post.objects.filter(pk=post.pk).update(cliques=F("cliques") + 1)
    return redirect(destino_do_clique(post))


def imagem(request: HttpRequest, chave: str, n: int) -> HttpResponse:
    """Uma lamina do post, para a rede buscar (Instagram, Google). So de post
    que existe; a chave e aleatoria, entao nao ha como listar."""
    from apps.social.laminas import caminho_da_imagem

    post = Post.objects.filter(chave_publica=chave).first()
    caminho = caminho_da_imagem(post, n) if post else ""
    if not caminho or not default_storage.exists(caminho):
        raise Http404
    return FileResponse(default_storage.open(caminho, "rb"), content_type="image/png")


def bio(request: HttpRequest, chave: str) -> HttpResponse:
    """O 'link na bio' do Instagram: os artigos dos posts recentes da conta,
    cada um pelo link rastreado do proprio post."""
    destino = get_object_or_404(Destino, chave_publica=chave)
    posts = destino.posts.filter(situacao=Post.Situacao.PUBLICADO).order_by("-publicado_em")[:12]
    return render(request, "social/bio.html", {"destino": destino, "posts": posts})
