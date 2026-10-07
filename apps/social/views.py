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

from apps.social import experimentos, fontes
from apps.social.abordagens import garantir_abordagens
from apps.social.forms import AbordagemForm, ConfiguracaoForm, DestinoForm, RecadoForm
from apps.social.models import Abordagem, Comentario, ConfiguracaoSocial, Destino, Post, Recado
from apps.social.redes import REDES, rede
from apps.social.redes.base import ErroDaRede

logger = logging.getLogger("publibot.social")

ABAS = ("revisar", "agenda", "publicados", "comentarios", "funciona", "configurar")


def _voltar(request, aba: str = "revisar") -> HttpResponse:
    return redirect(request.POST.get("voltar") or f"{reverse('social:inicio')}?aba={aba}")


def _contagens() -> dict:
    """Os numeros das abas (pendencias de cada uma)."""
    return {
        "revisar": Post.objects.filter(
            situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.GERANDO, Post.Situacao.RASCUNHO]
        ).count(),
        "agenda": Post.objects.filter(
            situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICANDO]
        ).count(),
        "comentarios": Comentario.objects.filter(
            tipo=Comentario.Tipo.PERGUNTA, respondido_em__isnull=True
        )
        .exclude(post__motivo=Post.Motivo.HISTORICO)
        .count(),
        "diagnostico": sum(1 for d in Destino.objects.filter(ligado=True) if _leitura_parada(d)),
        "estoque_baixo": sum(
            1
            for d in Destino.objects.filter(ligado=True, fotos_por_cento__gt=0)
            if _estoque_baixo(d)
        ),
    }


def _estoque_baixo(destino: Destino) -> bool:
    from apps.social.proprio import estoque

    return estoque(destino)["baixo"]


def _leitura_parada(destino: Destino) -> bool:
    from apps.social.painel import leitura_parada

    return bool(leitura_parada(destino))


@login_required
def inicio(request: HttpRequest) -> HttpResponse:
    from apps.social.abordagens import garantir_contas_padrao

    garantir_abordagens()
    garantir_contas_padrao()
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
        "contagens": _contagens(),
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
        contexto["destinos_do_link"] = fontes.destinos_do_link()
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
        contexto["quadros"] = [
            (d, experimentos.quadro(d), experimentos.quadro_de_recados(d)) for d in destinos
        ]
        contexto["metrica"] = contexto["config"].get_metrica_display()
    else:
        contexto["form_config"] = ConfiguracaoForm(instance=contexto["config"])
        contexto["form_destino"] = DestinoForm()
        contexto["form_abordagem"] = AbordagemForm()
        contexto["abordagens"] = Abordagem.objects.all()
        contexto["recados"] = [
            (r, RecadoForm(instance=r, auto_id=f"r{r.pk}_%s")) for r in Recado.objects.all()
        ]
        contexto["form_recado"] = RecadoForm(auto_id="recado_%s")
        from core.retorno_oauth import endereco_de_retorno

        contexto["retorno"] = endereco_de_retorno()
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
    from apps.social import laminas, publicacao
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
            from apps.social.proprio import como_artigo

            artigo = como_artigo(post.entrada) if post.entrada_id else fontes.artigo(post.artigo_id)
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
    elif acao in ("enquete", "post_comum"):
        extras = dict(post.extras or {})
        if acao == "enquete":
            extras["formato"] = "enquete"
        else:
            extras.pop("formato", None)
            extras.pop("enquete", None)
        post.extras = extras
        post.situacao = Post.Situacao.SUGERIDO
        post.save(update_fields=["extras", "situacao", "atualizado_em"])
        escrever_post.delay(str(post.pk))
        messages.info(
            request,
            _("Reescrevendo como enquete.")
            if acao == "enquete"
            else _("Reescrevendo como post comum."),
        )
    elif acao == "trocar_link":
        url = (request.POST.get("link_outro") or request.POST.get("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            messages.error(request, _("Escolha um destino ou cole um endereco com http(s)://."))
        else:
            post.artigo_url = url[:500]
            post.save(update_fields=["artigo_url", "atualizado_em"])
            messages.success(request, _("O link do post agora leva para %(u)s.") % {"u": url})
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
    return _salvar(request, Abordagem, AbordagemForm, pk, _("Abordagem salva."))


@login_required
@require_POST
def salvar_recado(request: HttpRequest, pk=None) -> HttpResponse:
    return _salvar(request, Recado, RecadoForm, pk, _("Recado salvo."))


def _salvar(request, modelo, formulario, pk, sucesso) -> HttpResponse:
    instancia = get_object_or_404(modelo, pk=pk) if pk else None
    form = formulario(request.POST, instance=instancia)
    if form.is_valid():
        form.save()
        messages.success(request, sucesso)
    else:
        messages.error(request, _("Confira os campos: %(e)s") % {"e": form.errors.as_text()})
    return _voltar(request, "configurar")


# -- Conectar a conta (OAuth) ---------------------------------------------------------
@login_required
def conectar(request: HttpRequest, pk) -> HttpResponse:
    destino = get_object_or_404(Destino, pk=pk)
    r = rede(destino.rede)
    from core.retorno_oauth import assinar, endereco_de_retorno

    anuncios = destino.rede == "instagram" and request.GET.get("anuncios") == "1"
    estado = assinar(
        connection.schema_name,
        reverse("social:retorno"),
        destino=str(destino.pk),
        anuncios=anuncios,
    )
    try:
        extra = {"escopos_extras": "ads_read"} if anuncios else {}
        url = r.oauth().url_de_autorizacao(destino, endereco_de_retorno(), estado, **extra)
    except ErroDaRede as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    return redirect(url)


@login_required
def retorno(request: HttpRequest) -> HttpResponse:
    """A rede devolve aqui, com o codigo. Troca pelo acesso e escolhe a conta."""
    from core.retorno_oauth import endereco_de_retorno, ler

    try:
        estado = ler(request.GET.get("state", ""))
        if estado.get("schema") != connection.schema_name:
            raise signing.BadSignature("outro cliente")
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
            destino, request.GET.get("code", ""), endereco_de_retorno()
        )
        oauth.gravar(destino, credenciais)
        if credenciais.get("usuario_id"):
            # Quem conectou, na rede (para atender o pedido de exclusao de dados).
            destino.usuario_remoto = str(credenciais["usuario_id"])[:120]
            destino.save(update_fields=["usuario_remoto"])
        contas = oauth.contas(destino)
    except ErroDaRede as exc:
        destino.ultimo_erro = str(exc)[:2000]
        destino.save(update_fields=["ultimo_erro"])
        messages.error(request, _("Nao conectou: %(e)s") % {"e": exc})
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    if estado.get("anuncios") and destino.conta_id:
        # Reconexao so para ler anuncios: a conta do Instagram ja esta escolhida.
        return _depois_de_conectar(request, destino, anuncios=True)
    if len(contas) == 1:
        destino.conta_id, destino.conta_nome = contas[0]
        destino.save(update_fields=["conta_id", "conta_nome"])
        messages.success(request, _("Conectado: %(c)s.") % {"c": destino.conta_nome})
        return _depois_de_conectar(request, destino, anuncios=estado.get("anuncios", False))
    if not contas:
        messages.error(
            request,
            _(
                "Conectou, mas a rede nao mostrou nenhuma conta "
                "(pagina, perfil profissional ou local)."
            )
            + (f" {oauth.sem_contas}" if oauth.sem_contas else ""),
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
    return _depois_de_conectar(request, destino)


def _depois_de_conectar(request, destino: Destino, *, anuncios: bool = False) -> HttpResponse:
    """Conta conectada: importa o passado (em segundo plano) e, se a pessoa
    pediu, acha a conta de anuncios. Termina no diagnostico da conta."""
    from apps.social import historico
    from apps.social.redes.meta_anuncios import AnunciosMeta
    from apps.social.tasks import importar_historico

    if anuncios:
        try:
            api = AnunciosMeta(destino)
            if not api.liberado():
                messages.warning(
                    request,
                    _(
                        "A Meta nao liberou a leitura de anuncios (ads_read) para este app. "
                        "Use a planilha exportada do Gerenciador, no Diagnostico."
                    ),
                )
            else:
                contas = api.contas()
                if len(contas) == 1:
                    destino.anuncios_conta_id, destino.anuncios_conta_nome = contas[0]
                    destino.save(update_fields=["anuncios_conta_id", "anuncios_conta_nome"])
                    messages.success(
                        request, _("Anuncios: %(c)s.") % {"c": destino.anuncios_conta_nome}
                    )
                elif not contas:
                    messages.warning(request, _("Nenhuma conta de anuncios nesta pessoa."))
                else:
                    messages.info(request, _("Escolha a conta de anuncios, abaixo."))
        except Exception as exc:  # a conta ja esta conectada: o erro so vai para a tela
            messages.error(request, _("Anuncios: %(e)s") % {"e": exc})
    if historico.suporta(destino):
        importar_historico.delay(connection.schema_name, str(destino.pk))
        messages.info(
            request,
            _("Importando os posts antigos da conta: o diagnostico fica pronto em instantes."),
        )
        return redirect(f"{reverse('social:diagnostico')}?destino={destino.pk}")
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
    import mimetypes

    from apps.social.laminas import caminho_da_imagem

    post = Post.objects.filter(chave_publica=chave).first()
    caminho = ""
    if post is not None:
        imagens = post.imagens or []
        caminho = imagens[n - 1].get("caminho", "") if 0 < n <= len(imagens) else ""
        caminho = caminho or caminho_da_imagem(post, n)
    if not caminho or not default_storage.exists(caminho):
        raise Http404
    tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
    return FileResponse(default_storage.open(caminho, "rb"), content_type=tipo)


def bio(request: HttpRequest, chave: str) -> HttpResponse:
    """O 'link na bio' do Instagram: os artigos dos posts recentes da conta,
    cada um pelo link rastreado do proprio post."""
    destino = get_object_or_404(Destino, chave_publica=chave)
    posts = (
        destino.posts.filter(situacao=Post.Situacao.PUBLICADO)
        .exclude(artigo_url="")
        .order_by("-publicado_em")[:12]
    )
    return render(request, "social/bio.html", {"destino": destino, "posts": posts})


# -- Estrategia --------------------------------------------------------------------
@login_required
def estrategia(request: HttpRequest) -> HttpResponse:
    from django.core.cache import cache
    from django.db import connection as conexao

    from apps.social import estrategia as modulo
    from apps.social import outra_ia, parametros
    from apps.social.abordagens import garantir_contas_padrao
    from apps.social.models import Tema
    from apps.social.tasks import recalcular_temas_do_cliente

    garantir_abordagens()
    if garantir_contas_padrao():
        messages.info(
            request,
            _(
                "Criei uma conta de cada rede (Instagram, LinkedIn e Google), ja ligadas: os "
                "posts comecam a ser sugeridos sozinhos. Desligue em Configurar a que nao usar."
            ),
        )
    # Sem temas ainda: calcula ja (em segundo plano), no maximo uma vez por hora.
    if not Tema.objects.filter(ativo=True).exists() and cache.add(
        f"social:temas-pedidos:{conexao.schema_name}", 1, timeout=3600
    ):
        recalcular_temas_do_cliente.delay(conexao.schema_name)
    destinos = list(Destino.objects.filter(ligado=True)) or list(Destino.objects.all())
    escolhido = next(
        (d for d in destinos if str(d.pk) == request.GET.get("destino")),
        destinos[0] if destinos else None,
    )
    return render(
        request,
        "social/estrategia.html",
        {
            "aba": "redes",
            "aba_das_redes": "estrategia",
            "contagens": _contagens(),
            "destinos": destinos,
            "destino": escolhido,
            "plano": modulo.plano(escolhido) if escolhido else None,
            "passos": modulo.primeiros_passos(escolhido) if escolhido else [],
            "fases": [(c, modulo.FASES[c]["nome"]) for c in modulo.ORDEM],
            "parametros": parametros.todos(),
            "temas": Tema.objects.filter(ativo=True).order_by("-nota")[:15],
            "pedido_ia": outra_ia.pedido(escolhido) if escolhido else "",
            "comparativo": outra_ia.comparativo(escolhido) if escolhido else {},
        },
    )


@login_required
@require_POST
def acao_na_estrategia(request: HttpRequest) -> HttpResponse:
    from decimal import Decimal, InvalidOperation

    from apps.social import escolha, parametros
    from apps.social.estrategia import registrar_seguidores
    from apps.social.models import Tema

    acao = request.POST.get("acao")
    destino = Destino.objects.filter(pk=request.POST.get("destino") or None).first()
    voltar = reverse("social:estrategia") + (f"?destino={destino.pk}" if destino else "")

    if acao == "seguidores" and destino is not None:
        try:
            registrar_seguidores(destino, int(request.POST.get("seguidores", "")))
            messages.success(request, _("Seguidores registrados."))
        except ValueError:
            messages.error(request, _("Digite um numero."))
    elif acao == "fase" and destino is not None:
        destino.fase_manual = request.POST.get("fase_manual", "")[:20]
        destino.save(update_fields=["fase_manual"])
    elif acao == "parametros":
        invalidos = parametros.gravar(ConfiguracaoSocial.carregar(), request.POST)
        if invalidos:
            messages.error(request, _("Valores invalidos: %(p)s") % {"p": ", ".join(invalidos)})
        else:
            messages.success(request, _("Parametros salvos."))
    elif acao == "recalcular_temas":
        from apps.social.temas import recalcular

        try:
            n = recalcular()
            messages.success(request, _("%(n)s tema(s) encontrados.") % {"n": n})
        except Exception as exc:  # sem vetores agora, por exemplo
            logger.exception("Temas nao recalculados.")
            messages.error(request, _("Nao deu para recalcular agora: %(e)s") % {"e": exc})
    elif acao == "gerar_do_tema" and destino is not None:
        tema = get_object_or_404(Tema, pk=request.POST.get("tema"))
        par = request.POST.get("par") == "1"
        criados = escolha.sugerir_tema(destino, tema, versoes=2 if par else None)
        messages.success(
            request,
            _("%(n)s post(s) sendo escritos (aba Para revisar).") % {"n": len(criados)},
        )
    elif acao == "impulso":
        post = get_object_or_404(Post, pk=request.POST.get("post"))
        try:
            valor = Decimal(request.POST.get("valor", "").replace(",", "."))
        except InvalidOperation:
            messages.error(request, _("Digite o valor pago."))
            return redirect(request.POST.get("voltar") or voltar)
        post.impulsionado = True
        post.custo_impulso = valor
        post.impulso_em = timezone.now()
        post.save(update_fields=["impulsionado", "custo_impulso", "impulso_em", "atualizado_em"])
        messages.success(request, _("Impulso registrado: o resultado passa a contar como pago."))
    return redirect(request.POST.get("voltar") or voltar)


# -- Segunda opiniao de uma IA grande ---------------------------------------------------
@login_required
@require_POST
def revisar_resposta_ia(request: HttpRequest) -> HttpResponse:
    from apps.social import outra_ia

    destino = get_object_or_404(Destino, pk=request.POST.get("destino"))
    resposta = request.POST.get("resposta", "")
    leitura = outra_ia.ler(resposta)
    if leitura.vazia and not leitura.comentarios:
        messages.error(
            request,
            _(
                "Nao achei os blocos PROPOSTAS, ABORDAGENS ou EVENTOS na resposta. Peca a IA "
                "'pode gerar' e cole a resposta inteira."
            ),
        )
        return redirect(f"{reverse('social:estrategia')}?destino={destino.pk}#outra-ia")
    return render(
        request,
        "social/resposta_ia.html",
        {
            "aba": "redes",
            "aba_das_redes": "estrategia",
            "contagens": _contagens(),
            "destino": destino,
            "leitura": leitura,
            "resposta": resposta,
        },
    )


@login_required
@require_POST
def aplicar_resposta_ia(request: HttpRequest) -> HttpResponse:
    from apps.social import outra_ia

    destino = get_object_or_404(Destino, pk=request.POST.get("destino"))

    def indices(nome: str) -> set[int]:
        return {int(x) for x in request.POST.getlist(nome) if x.isdigit()}

    feito = outra_ia.aplicar(
        outra_ia.ler(request.POST.get("resposta", "")),
        destino,
        propostas=indices("proposta"),
        abordagens=indices("abordagem"),
        eventos=indices("evento"),
    )
    messages.success(
        request,
        _("%(p)s post(s) sendo escritos e %(a)s abordagem(ns) nova(s) no sorteio.")
        % {"p": feito["posts"], "a": feito["abordagens"]},
    )
    return redirect(f"{reverse('social:inicio')}?aba=revisar")


# -- Diagnostico, historico e anuncios ----------------------------------------------------
@login_required
def diagnostico(request: HttpRequest) -> HttpResponse:
    from apps.social import diagnostico as modulo
    from apps.social import historico
    from apps.social.anuncios import como_exportar
    from apps.social.painel import leitura_parada

    destinos = list(Destino.objects.all())
    escolhido = next(
        (d for d in destinos if str(d.pk) == request.GET.get("destino")),
        next((d for d in destinos if d.conectado), destinos[0] if destinos else None),
    )
    contexto = {
        "aba": "redes",
        "aba_das_redes": "diagnostico",
        "contagens": _contagens(),
        "destinos": destinos,
        "destino": escolhido,
    }
    if escolhido is not None:
        d = modulo.montar(escolhido)
        contexto.update(
            {
                "d": d,
                "texto": modulo.como_texto(d),
                "suporta_historico": historico.suporta(escolhido),
                "como_exportar": como_exportar(escolhido.rede),
                "como_exportar_posts": historico.como_exportar(escolhido),
                "faltam": escolhido.posts.filter(
                    motivo=Post.Motivo.HISTORICO, extras__detalhado__isnull=True
                ).count(),
                "parada": leitura_parada(escolhido),
                "anuncios": escolhido.anuncios.select_related("post")[:50],
                "contas_de_anuncio": _contas_de_anuncio(escolhido),
            }
        )
    return render(request, "social/diagnostico.html", contexto)


def _contas_de_anuncio(destino: Destino) -> list:
    """Para escolher a conta de anuncios: so quando ha acesso e nenhuma escolhida."""
    if destino.anuncios_conta_id or destino.rede != "instagram" or not destino.conectado:
        return []
    from apps.social.redes.meta_anuncios import AnunciosMeta

    try:
        api = AnunciosMeta(destino)
        return api.contas() if api.liberado() else []
    except Exception as exc:  # rede fora do ar: a tela abre do mesmo jeito
        logger.info("Contas de anuncio de %s indisponiveis: %s", destino, exc)
        return []


@login_required
@require_POST
def acao_no_diagnostico(request: HttpRequest) -> HttpResponse:
    from apps.social import anuncios, historico
    from apps.social.tasks import importar_historico

    destino = get_object_or_404(Destino, pk=request.POST.get("destino"))
    voltar = f"{reverse('social:diagnostico')}?destino={destino.pk}"
    acao = request.POST.get("acao")
    if acao == "importar":
        importar_historico.delay(connection.schema_name, str(destino.pk))
        messages.info(request, _("Importando em segundo plano: recarregue em um minuto."))
    elif acao == "conta_de_anuncios":
        conta = request.POST.get("conta", "")
        destino.anuncios_conta_id = conta[:60]
        destino.anuncios_conta_nome = request.POST.get(f"nome_{conta}", conta)[:200]
        destino.save(update_fields=["anuncios_conta_id", "anuncios_conta_nome"])
        n = anuncios.sincronizar(destino)
        messages.success(request, _("%(n)s anuncio(s) lidos.") % {"n": n})
    elif acao == "sincronizar_anuncios":
        n = anuncios.sincronizar(destino)
        if destino.anuncios_erro:
            messages.error(request, _("A Meta recusou: %(e)s") % {"e": destino.anuncios_erro})
        else:
            messages.success(request, _("%(n)s anuncio(s) lidos.") % {"n": n})
    elif acao == "desligar_anuncios":
        destino.anuncios_conta_id = destino.anuncios_conta_nome = destino.anuncios_erro = ""
        destino.save(update_fields=["anuncios_conta_id", "anuncios_conta_nome", "anuncios_erro"])
        messages.info(
            request, _("Leitura automatica de anuncios desligada (a planilha segue valendo).")
        )
    elif acao == "planilha_de_posts":
        conteudo = _arquivo_da_planilha(request)
        if conteudo is not None:
            try:
                feito = historico.importar_planilha(destino, conteudo)
                messages.success(
                    request,
                    _("%(l)s post(s) no arquivo: %(n)s novos, %(a)s completados.")
                    % {"l": feito["linhas"], "n": feito["novos"], "a": feito["atualizados"]},
                )
            except anuncios.PlanilhaInvalida as exc:
                messages.error(request, str(exc))
    elif acao == "planilha":
        conteudo = _arquivo_da_planilha(request)
        if conteudo is not None:
            try:
                feito = anuncios.importar_planilha(destino, conteudo)
                messages.success(
                    request,
                    _("%(a)s anuncio(s) lidos; %(l)s ligados a posts.")
                    % {"a": feito["anuncios"], "l": feito["ligados"]},
                )
            except anuncios.PlanilhaInvalida as exc:
                messages.error(request, str(exc))
    return redirect(voltar)


def _arquivo_da_planilha(request) -> bytes | None:
    arquivo = request.FILES.get("planilha")
    if arquivo is None:
        messages.error(request, _("Escolha o arquivo (.csv ou .xlsx) exportado."))
        return None
    if arquivo.size > 10 * 1024 * 1024:
        messages.error(request, _("Arquivo grande demais (maximo 10 MB)."))
        return None
    return arquivo.read()


# -- Material proprio: post novo e banco de fotos -------------------------------------
@login_required
def novo_post(request: HttpRequest) -> HttpResponse:
    """Caso real ou novidade: texto (ou audio), fotos ou video, e as contas."""
    from apps.social import fontes, proprio
    from apps.social.models import Entrada

    garantir_abordagens()
    destinos = list(Destino.objects.filter(ligado=True)) or list(Destino.objects.all())
    if request.method == "POST":
        try:
            entrada = proprio.criar(
                tipo=request.POST.get("tipo") or Entrada.Tipo.CASO,
                texto=request.POST.get("texto", ""),
                arquivos=request.FILES.getlist("arquivos"),
                audio=request.FILES.get("audio"),
                artigo=request.POST.get("artigo", "auto"),
                link=request.POST.get("link", "").strip(),
                links=request.POST.get("links", "").splitlines(),
                autorizado=request.POST.get("autorizado") == "1",
                destinos=request.POST.getlist("destinos"),
                por=request.user,
            )
        except (proprio.EntradaInvalida, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(reverse("social:novo_post"))
        if entrada.referencias:
            messages.info(
                request,
                _(
                    "Lendo os links: os posts aparecem em Para revisar em instantes (link que "
                    "nao abrir fica avisado no post)."
                ),
            )
        elif entrada.situacao_do_audio == Entrada.Transcricao.ESPERANDO:
            messages.info(
                request,
                _("Transcrevendo o audio: os posts aparecem em Para revisar quando terminar."),
            )
        else:
            messages.success(request, _("Os posts estao sendo escritos (Para revisar)."))
        return redirect(f"{reverse('social:inicio')}?aba=revisar")
    artigos = [
        a for i in fontes.artigos_no_ar()[:60] if (a := fontes.artigo(i)) is not None and a.url
    ]
    return render(
        request,
        "social/novo.html",
        {
            "aba": "redes",
            "aba_das_redes": "novo",
            "contagens": _contagens(),
            "destinos": destinos,
            "artigos": artigos,
            "tipos": [
                (Entrada.Tipo.CASO, _("Caso real")),
                (Entrada.Tipo.NOVIDADE, _("Novidade ou bastidor")),
                (Entrada.Tipo.COMENTARIO, _("Noticia ou estudo comentado")),
            ],
        },
    )


@login_required
def banco_de_fotos(request: HttpRequest) -> HttpResponse:
    from apps.social import proprio
    from apps.social.models import Midia

    voltar = reverse("social:banco_de_fotos")
    if request.method == "POST":
        acao = request.POST.get("acao", "enviar")
        if acao == "enviar":
            arquivos = request.FILES.getlist("fotos")
            if not arquivos:
                messages.error(request, _("Escolha as fotos (ou videos)."))
            elif request.POST.get("autorizada") != "1":
                messages.error(
                    request,
                    _("Confirme que quem aparece autorizou (ou que nao aparece ninguem)."),
                )
            else:
                feito = proprio.receber_no_banco(
                    arquivos, nota=request.POST.get("nota", ""), autorizada=True
                )
                messages.success(
                    request,
                    _(
                        "%(r)s recebida(s): %(b)s descartada(s) por qualidade e %(q)s quase "
                        "igual(is) a outra."
                    )
                    % {"r": feito["recebidas"], "b": feito["ruins"], "q": feito["repetidas"]},
                )
                for erro in feito["erros"]:
                    messages.error(request, erro)
        elif acao in ("descartar", "usar"):
            midia = get_object_or_404(Midia, pk=request.POST.get("midia"))
            if acao == "descartar":
                midia.situacao, midia.motivo = Midia.Situacao.DESCARTADA, "descartada por voce"
            else:
                midia.situacao, midia.motivo = Midia.Situacao.NOVA, ""
            midia.save(update_fields=["situacao", "motivo"])
        elif acao == "nota":
            Midia.objects.filter(grupo=request.POST.get("grupo") or None).update(
                nota=request.POST.get("nota", "")[:2000]
            )
            messages.success(request, _("Nota salva: entra na legenda do post."))
        elif acao == "postar_agora":
            destino = get_object_or_404(Destino, pk=request.POST.get("destino"))
            grupo = request.POST.get("grupo")
            disponiveis = [
                g for g in proprio.grupos_disponiveis(destino) if str(g[0].grupo) == grupo
            ]
            if disponiveis:
                # O grupo escolhido passa na frente e vira post desta conta.
                from apps.social.models import Entrada

                entrada = Entrada.objects.create(
                    tipo=Entrada.Tipo.FOTOS,
                    autorizado=all(m.autorizada for m in disponiveis[0]),
                    destinos=[str(destino.pk)],
                )
                entrada.midias.set(disponiveis[0])
                Midia.objects.filter(grupo=grupo).update(situacao=Midia.Situacao.USADA)
                proprio.levar(entrada)
                messages.success(request, _("Post sendo escrito (Para revisar)."))
            else:
                messages.info(request, _("Esta conta ja postou estas fotos."))
        return redirect(voltar)

    grupos: dict = {}
    for midia in Midia.objects.filter(banco=True).order_by("-criada_em")[:300]:
        grupos.setdefault(midia.grupo or midia.pk, []).append(midia)
    destinos = list(Destino.objects.filter(ligado=True))
    return render(
        request,
        "social/fotos.html",
        {
            "aba": "redes",
            "aba_das_redes": "fotos",
            "contagens": _contagens(),
            "grupos": list(grupos.items()),
            "destinos": destinos,
            "estoques": [(d, proprio.estoque(d)) for d in destinos if d.fotos_por_cento],
            "config": ConfiguracaoSocial.carregar(),
        },
    )


@login_required
def midia_privada(request: HttpRequest, pk) -> HttpResponse:
    """A foto ou video do banco, para a tela (com login)."""
    import mimetypes

    from apps.social.models import Midia

    midia = get_object_or_404(Midia, pk=pk)
    tipo = mimetypes.guess_type(midia.arquivo.name)[0] or "application/octet-stream"
    return FileResponse(midia.arquivo.open("rb"), content_type=tipo)
