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
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.social import experimentos, fontes, persuasao
from apps.social.abordagens import garantir_abordagens
from apps.social.forms import AbordagemForm, ConfiguracaoForm, DestinoForm, RecadoForm
from apps.social.models import (
    Abordagem,
    Comentario,
    ConfiguracaoSocial,
    Destino,
    Isca,
    Post,
    Recado,
)
from apps.social.redes import REDES, rede
from apps.social.redes.base import ErroDaRede

logger = logging.getLogger("publibot.social")

ABAS = ("revisar", "agenda", "publicados", "comentarios", "funciona", "configurar")


def _voltar(request, aba: str = "revisar") -> HttpResponse:
    return redirect(request.POST.get("voltar") or f"{reverse('social:inicio')}?aba={aba}")


# A conta escolhida na barra acima das abas, lembrada na sessao: todas as abas
# mostram so ela (ou todas, onde faz sentido). "?destino=" muda a escolha.
CONTA_NA_SESSAO = "redes_conta"


def _conta(request: HttpRequest, *, exigir: bool = False) -> Destino | None:
    """A conta da barra. `exigir`: a tela e de uma conta so (Estrategia,
    Diagnostico, Configurar): sem escolha, a primeira ligada."""
    import uuid

    if "destino" in request.GET:
        request.session[CONTA_NA_SESSAO] = request.GET.get("destino") or ""
    escolha = request.session.get(CONTA_NA_SESSAO) or ""
    try:
        conta = Destino.objects.filter(pk=uuid.UUID(escolha)).first() if escolha else None
    except ValueError:
        conta = None
    if conta is None and exigir:
        conta = Destino.objects.filter(ligado=True).first() or Destino.objects.first()
    return conta


def _contagens(request: HttpRequest | None = None, *, exigir: bool = False) -> dict:
    """Os numeros das abas (pendencias de cada uma), da conta da barra, e a
    propria barra (as contas, a escolhida e se a tela aceita "todas")."""
    conta = _conta(request, exigir=exigir) if request is not None else None
    posts = Post.objects.filter(destino=conta) if conta else Post.objects.all()
    comentarios = Comentario.objects.filter(post__destino=conta) if conta else Comentario.objects
    contas = list(Destino.objects.filter(ligado=True)) if conta is None else [conta]
    return {
        "revisar": posts.filter(
            situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.GERANDO, Post.Situacao.RASCUNHO]
        ).count(),
        "agenda": posts.filter(
            situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICANDO]
        ).count(),
        "comentarios": comentarios.filter(tipo=Comentario.Tipo.PERGUNTA, respondido_em__isnull=True)
        .exclude(post__motivo=Post.Motivo.HISTORICO)
        .count(),
        "diagnostico": sum(1 for d in contas if _leitura_parada(d)),
        "iscas": _iscas_a_decidir(conta),
        "estoque_baixo": sum(1 for d in contas if d.fotos_por_cento and _estoque_baixo(d)),
        # A barra de contas (acima das abas).
        "contas": list(Destino.objects.all()),
        "conta": conta,
        "so_uma": exigir,
    }


def _iscas_a_decidir(conta=None) -> int:
    from apps.social import iscas

    return len(iscas.a_decidir(conta))


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
    so_uma = aba == "configurar"
    conta = _conta(request, exigir=so_uma)
    posts = Post.objects.select_related("destino", "abordagem")
    if conta is not None:
        posts = posts.filter(destino=conta)

    contexto = {
        "aba": "redes",
        "aba_das_redes": aba,
        "destinos": destinos,
        "conta": conta,
        "redes": REDES,
        "config": ConfiguracaoSocial.carregar(),
        "contagens": _contagens(request, exigir=so_uma),
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
        contexto["iscas"] = Isca.objects.filter(ativa=True)
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
        comentarios = Comentario.objects.select_related("post__destino")
        if conta is not None:
            comentarios = comentarios.filter(post__destino=conta)
        contexto["comentarios"] = comentarios.order_by(
            F("respondido_em").asc(nulls_first=True), "-escrito_em"
        )[:200]
    elif aba == "funciona":
        contexto["quadros"] = [
            (d, experimentos.quadro(d), experimentos.quadro_de_recados(d), persuasao.quadro(d))
            for d in ([conta] if conta else destinos)
        ]
        contexto["metrica"] = contexto["config"].get_metrica_display()
    else:
        parte = request.GET.get("parte")
        contexto["parte"] = (
            parte if parte in PARTES_DE_CONFIGURAR else ("conta" if conta else "nova")
        )
        contexto.update(_configurar(request, conta, contexto["parte"], contexto["config"]))
    return render(request, "social/inicio.html", contexto)


# Configurar: um submenu (a conta da barra; o visual fica em Identidade visual).
PARTES_DE_CONFIGURAR = ("conta", "textos", "todas", "nova")


def _da_rede(itens, conta):
    """Abordagens ou recados que valem para a rede da conta (sem rede: todas)."""
    return [x for x in itens if conta is None or not x.redes or conta.rede in x.redes]


def _configurar(request: HttpRequest, conta, parte: str, config) -> dict:
    if parte == "conta" and conta is not None:
        from core.retorno_oauth import endereco_de_retorno

        conta.form = DestinoForm(instance=conta, prefix=str(conta.pk))
        conta.bio = request.build_absolute_uri(reverse("social:bio", args=[conta.chave_publica]))
        conta.rede_obj = rede(conta.rede)
        conta.vence_logo = bool(
            conta.expira_em and conta.expira_em <= timezone.now() + timedelta(days=7)
        )
        return {"retorno": endereco_de_retorno()}
    if parte == "textos":
        inicial = {"redes": [conta.rede]} if conta else {}
        return {
            "abordagens": _da_rede(Abordagem.objects.all(), conta),
            "form_abordagem": AbordagemForm(initial=inicial),
            "recados": [
                (r, RecadoForm(instance=r, auto_id=f"r{r.pk}_%s"))
                for r in _da_rede(Recado.objects.all(), conta)
            ],
            "form_recado": RecadoForm(auto_id="recado_%s", initial=inicial),
        }
    if parte == "todas":
        return {"form_config": ConfiguracaoForm(instance=config)}
    return {"form_destino": DestinoForm()}


# -- Posts ---------------------------------------------------------------------------
def _laminas_do_formulario(texto: str) -> list[dict]:
    """Uma lamina por linha: "titulo | texto | fonte" (a fonte e opcional)."""
    laminas = []
    for linha in (texto or "").splitlines():
        if not linha.strip():
            continue
        titulo, _sep, resto = linha.partition("|")
        corpo, _sep, fonte = resto.partition("|")
        lamina = {"titulo": titulo.strip()[:120], "texto": corpo.strip()[:220]}
        if fonte.strip():
            lamina["fonte"] = fonte.strip()[:90]
        laminas.append(lamina)
    return laminas


@login_required
@require_GET
def previa_da_lamina(request: HttpRequest, pk) -> HttpResponse:
    """A 1a lamina com o enquadramento da tela (?zoom=&x=&y=&escuro=), sem
    gravar: a previa que muda enquanto a pessoa mexe nos controles."""
    from apps.social import laminas

    post = get_object_or_404(Post.objects.select_related("destino"), pk=pk)
    artigo = _artigo_do_post(post)
    if artigo is None:
        raise Http404
    jpg = laminas.previa_da_primeira(
        post,
        artigo,
        laminas.ajuste_limpo(request.GET),
        titulo=request.GET.get("titulo"),
        texto=request.GET.get("texto"),
        fonte=request.GET.get("fonte"),
    )
    return HttpResponse(jpg, content_type="image/jpeg")


def _artigo_do_post(post):
    from apps.social.proprio import como_artigo

    return como_artigo(post.entrada) if post.entrada_id else fontes.artigo(post.artigo_id)


@login_required
def post_pela_outra_ia(request: HttpRequest, pk) -> HttpResponse:
    """O post inteiro escrito por outra IA: o pedido (com o material do artigo,
    a clareza e o estilo das imagens) e a resposta colada, que passa pela mesma
    conferencia da nossa IA."""
    from apps.social import criacao

    post = get_object_or_404(Post.objects.select_related("destino", "abordagem"), pk=pk)
    if request.method == "POST":
        aplicou, avisos, comentarios = criacao.aplicar(
            post, request.POST.get("resposta", ""), na_tela=True
        )
        if not aplicou:
            messages.error(
                request,
                _(
                    "Nao achei GANCHO nem LEGENDA na resposta. Peca 'pode gerar' e cole a "
                    "resposta inteira."
                ),
            )
            return redirect("social:post_pela_outra_ia", post.pk)
        messages.success(
            request,
            _("Post da outra IA aplicado. Confira os avisos e envie as imagens das laminas.")
            if avisos
            else _("Post da outra IA aplicado, sem avisos. Envie as imagens das laminas."),
        )
        if comentarios:
            messages.info(request, comentarios[:1000])
        return redirect(f"{reverse('social:inicio')}?aba=revisar#post-{post.pk}")
    return render(
        request,
        "social/post_outra_ia.html",
        {
            "aba": "redes",
            "aba_das_redes": "revisar",
            "contagens": _contagens(request),
            "post": post,
            "pedido": criacao.pedido(post, na_tela=True),
            "api_da_outra_ia": _api_da_outra_ia(),
        },
    )


@login_required
def editar_lamina(request: HttpRequest, pk) -> HttpResponse:
    """O editor da 1a lamina: arrastar a foto e o texto, tamanho, alinhamento,
    cor e escurecimento, com o desenho na hora no navegador. Salvar grava o
    ajuste e o texto e refaz as imagens no servidor (que e o que vai ao ar)."""
    import json

    from apps.social import laminas

    post = get_object_or_404(Post.objects.select_related("destino"), pk=pk)
    artigo = _artigo_do_post(post)
    if artigo is None:
        raise Http404
    if request.method == "POST":
        try:
            ajuste = json.loads(request.POST.get("ajuste") or "{}")
        except ValueError:
            ajuste = {}
        extras = dict(post.extras or {})
        lista = [dict(lamina) for lamina in extras.get("laminas") or []]
        if lista:
            lista[0]["titulo"] = request.POST.get("titulo", lista[0].get("titulo", "")).strip()[
                :120
            ]
            lista[0]["texto"] = request.POST.get("texto", lista[0].get("texto", "")).strip()[:220]
            fonte = request.POST.get("fonte", lista[0].get("fonte", "")).strip()[:90]
            if fonte:
                lista[0]["fonte"] = fonte
            else:
                lista[0].pop("fonte", None)
            extras["laminas"] = lista
        extras["ajuste_da_capa"] = laminas.ajuste_limpo(ajuste)
        post.extras = extras
        post.save(update_fields=["extras", "atualizado_em"])
        _refazer_imagens(post)
        messages.success(request, _("Primeira lamina salva e imagens refeitas."))
        return redirect("social:editar_lamina", pk=post.pk)
    return render(
        request,
        "social/editar_lamina.html",
        {"post": post, "editor": laminas.dados_do_editor(post, artigo)},
    )


@login_required
@require_GET
def foto_da_lamina(request: HttpRequest, pk) -> HttpResponse:
    """A foto de fundo da 1a lamina (a enviada ou a capa do artigo), para o editor."""
    import mimetypes

    from apps.social import laminas

    post = get_object_or_404(Post.objects.select_related("destino"), pk=pk)
    artigo = _artigo_do_post(post)
    caminho = laminas.foto_de_fundo(post, artigo) if artigo is not None else ""
    if not caminho or not default_storage.exists(caminho):
        raise Http404
    tipo = mimetypes.guess_type(caminho)[0] or "image/jpeg"
    return FileResponse(default_storage.open(caminho, "rb"), content_type=tipo)


@login_required
@require_GET
def fonte_da_lamina(request: HttpRequest, peso: str) -> HttpResponse:
    """A mesma fonte das laminas, para o editor medir e quebrar o texto igual."""
    from apps.social import laminas

    caminho = laminas.caminho_da_fonte(negrito=peso == "negrito")
    if not caminho:
        raise Http404
    resposta = FileResponse(open(caminho, "rb"), content_type="font/ttf")
    resposta["Cache-Control"] = "private, max-age=86400"
    return resposta


def _refazer_imagens(post) -> bool:
    """Monta de novo as imagens do post (laminas, capa ou fotos do material
    proprio) com o texto e a foto atuais. Falso se o artigo sumiu."""
    from apps.social import laminas

    return laminas.refazer_imagens(post)


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
            _refazer_imagens(post)
        messages.success(request, _("Post salvo."))
    elif acao == "regerar_imagens":
        if _refazer_imagens(post):
            messages.success(request, _("Imagens refeitas com o texto atual."))
        else:
            messages.error(request, _("O artigo deste post nao existe mais: nao ha como refazer."))
    elif acao == "trocar_imagem":
        arquivo = request.FILES.get("imagem")
        try:
            caminho = laminas.guardar_imagem_propria(post, arquivo) if arquivo else ""
        except Exception:
            caminho = ""
        if not caminho:
            messages.error(request, _("Envie uma imagem (JPG, PNG ou WebP)."))
        else:
            # Foto nova, enquadramento novo: o ajuste da anterior nao serve.
            extras = {**(post.extras or {}), "imagem_propria": caminho}
            extras.pop("ajuste_da_capa", None)
            post.extras = extras
            post.save(update_fields=["extras", "atualizado_em"])
            _refazer_imagens(post)
            messages.success(request, _("Foto trocada e imagens refeitas."))
    elif acao == "enviar_video_ia":
        from apps.social import video_ia

        arquivo = request.FILES.get("video")
        try:
            if arquivo is None:
                raise video_ia.VideoInvalido("escolha o arquivo do video.")
            video_ia.enviar(post, arquivo)
            messages.success(request, _("Video recebido: o post vai como video (Reels)."))
        except video_ia.VideoInvalido as exc:
            messages.error(request, str(exc))
    elif acao.startswith("cena_") or acao.startswith("cenas_"):
        _acao_nas_cenas(request, post, acao)
    elif acao == "tirar_video_ia":
        from apps.social import video_ia

        video_ia.tirar(post)
        messages.success(request, _("Video tirado: o post volta ao carrossel."))
    elif acao == "voltar_ao_carrossel":
        from apps.social import video_ia

        video_ia.tirar(post)
        messages.success(request, _("De volta ao carrossel."))
    elif acao == "gerar_imagens_aqui":
        from apps.social.tasks import gerar_imagens_das_laminas

        if not fontes.ha_modelo_de_imagem():
            messages.error(
                request,
                _(
                    "Nao ha gerador de imagem cadastrado (Configuracao > Inferencia, tipo "
                    "'image'). Use o pedido para uma IA de imagem de fora."
                ),
            )
        else:
            fontes.na_fila(
                gerar_imagens_das_laminas,
                str(post.pk),
                rotulo="Imagens das laminas",
                alvo=post.pk,
            )
            messages.info(
                request,
                _(
                    "Gerando as imagens das laminas sem imagem (uma por vez, alguns minutos). "
                    "Recarregue depois."
                ),
            )
    elif acao in (
        "refazer_imagem",
        "opcoes_de_imagem",
        "escolher_opcao",
        "preparar_prompts",
    ):
        _acao_na_lamina(request, post, acao)
    elif acao in ("fundo_da_lamina", "tirar_fundo_da_lamina"):
        # A imagem de uma lamina (feita numa IA de imagem com o pedido do post).
        try:
            n = int(request.POST.get("n") or 0)
        except ValueError:
            n = 0
        extras = dict(post.extras or {})
        fundos = dict(extras.get("fundos") or {})
        if acao == "tirar_fundo_da_lamina":
            fundos.pop(str(n), None)
            if n == 1:
                extras.pop("imagem_propria", None)
                extras.pop("ajuste_da_capa", None)
        elif n >= 1 and (arquivo := request.FILES.get("imagem")):
            try:
                caminho = laminas.guardar_imagem_propria(
                    post, arquivo, "propria" if n == 1 else f"fundo-{n}"
                )
            except Exception:
                caminho = ""
            if not caminho:
                messages.error(request, _("Envie uma imagem (JPG, PNG ou WebP)."))
                return redirect(request.POST.get("voltar") or "social:inicio")
            if n == 1:
                extras["imagem_propria"] = caminho
                extras.pop("ajuste_da_capa", None)
            else:
                fundos[str(n)] = caminho
        extras["fundos"] = fundos
        post.extras = extras
        post.save(update_fields=["extras", "atualizado_em"])
        _refazer_imagens(post)
        messages.success(request, _("Imagens refeitas."))
    elif acao == "duplicar":
        copia = Post.objects.create(
            destino=post.destino,
            artigo_id=post.artigo_id,
            artigo_titulo=post.artigo_titulo,
            artigo_url=post.artigo_url,
            abordagem=post.abordagem,
            entrada=post.entrada,
            tema=post.tema,
            motivo=post.motivo,
            por_que=post.por_que,
            texto=post.texto,
            extras={**(post.extras or {}), "copia_de": str(post.pk)},
            material=post.material,
            situacao=Post.Situacao.RASCUNHO,
        )
        _refazer_imagens(copia)
        messages.success(
            request,
            _("Copia criada em Revisar: edite a foto, as laminas e o texto, e aprove para postar."),
        )
        return redirect(f"{reverse('social:inicio')}?aba=revisar#post-{copia.pk}")
    elif acao == "imagem_do_artigo":
        extras = dict(post.extras or {})
        extras.pop("imagem_propria", None)
        extras.pop("ajuste_da_capa", None)
        post.extras = extras
        post.save(update_fields=["extras", "atualizado_em"])
        _refazer_imagens(post)
        messages.success(request, _("De volta a capa do artigo."))
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
        fontes.na_fila(escrever_post, str(post.pk), rotulo="Escrever o post", alvo=post.pk)
        messages.info(request, _("Reescrevendo com a abordagem '%(a)s'.") % {"a": abordagem})
    elif acao == "tentar_de_novo":
        fora = [f"{nome}: {motivo}" for nome, ok, motivo in fontes.reabrir_modelo() if not ok]
        post.situacao = Post.Situacao.SUGERIDO
        post.erro = ""
        post.save(update_fields=["situacao", "erro", "atualizado_em"])
        fontes.na_fila(escrever_post, str(post.pk), rotulo="Escrever o post", alvo=post.pk)
        if fora:
            messages.warning(
                request,
                _(
                    "O servidor do modelo continua fora do ar (%(f)s). O post esta na fila e "
                    "sai sozinho quando ele voltar: confira se a maquina esta ligada e na rede."
                )
                % {"f": "; ".join(fora)},
            )
        else:
            messages.info(request, _("Escrevendo de novo: recarregue em instantes."))
    elif acao == "isca":
        post.isca = Isca.objects.filter(pk=request.POST.get("isca") or None).first()
        post.save(update_fields=["isca", "atualizado_em"])
        if post.situacao in (Post.Situacao.RASCUNHO, Post.Situacao.FALHOU):
            post.situacao = Post.Situacao.SUGERIDO
            post.save(update_fields=["situacao", "atualizado_em"])
            fontes.na_fila(escrever_post, str(post.pk), rotulo="Escrever o post", alvo=post.pk)
            messages.info(request, _("Reescrevendo com a oferta para quem comentar."))
        else:
            if post.situacao != Post.Situacao.PUBLICADO:
                from apps.social.iscas import garantir_chamada

                post.texto = garantir_chamada(post, post.texto)
                post.save(update_fields=["texto", "atualizado_em"])
            messages.success(request, _("Oferta do post trocada."))
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
        fontes.na_fila(escrever_post, str(post.pk), rotulo="Escrever o post", alvo=post.pk)
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
    from apps.social.escolha import pedir_ao_publicar

    voltar = request.POST.get("voltar") or ""
    voltar = voltar if voltar.startswith("/") and not voltar.startswith("//") else ""
    artigo = fontes.artigo(str(artigo_id))
    if artigo is None:
        raise Http404
    if not artigo.url:
        # Ainda nao esta no ar: o post precisa do link. Vai quando publicar.
        pedir_ao_publicar(str(artigo_id))
        messages.success(
            request,
            _("Combinado: os posts deste artigo saem para as redes quando ele for publicado."),
        )
        return redirect(voltar or reverse("social:inicio"))
    garantir_abordagens()
    criados = levar(str(artigo_id))
    if criados:
        messages.success(
            request,
            _("%(n)s post(s) sendo escritos: acompanhe em Redes.") % {"n": len(criados)},
        )
    else:
        messages.info(request, _("Este artigo ja foi para todas as contas nos ultimos dias."))
    return redirect(voltar or reverse("social:inicio"))


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
        # "Trocar a conta": pergunta qual, mesmo que a atual continue na lista.
        trocar=request.GET.get("trocar") == "1",
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
    atual = next((c for c in contas if c[0] == destino.conta_id), None)
    if len(contas) == 1 or (atual and not estado.get("trocar")):
        # Uma conta so, ou a reconexao (o acesso vence a cada 60 dias) que
        # continua alcancando a conta ja escolhida: nao pergunta de novo.
        destino.conta_id, destino.conta_nome = atual or contas[0]
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
    # As que a rede ofereceu: a escolha so aceita uma delas.
    request.session[f"contas_oferecidas_{destino.pk}"] = [list(c) for c in contas]
    return render(
        request, "social/conta.html", {"aba": "redes", "destino": destino, "contas": contas}
    )


@login_required
@require_POST
def escolher_conta(request: HttpRequest, pk) -> HttpResponse:
    destino = get_object_or_404(Destino, pk=pk)
    conta_id = request.POST.get("conta", "")
    oferecidas = dict(request.session.get(f"contas_oferecidas_{destino.pk}") or [])
    if conta_id not in oferecidas:
        messages.error(request, _("Essa conta nao veio da rede. Conecte de novo e escolha."))
        return redirect(f"{reverse('social:inicio')}?aba=configurar")
    request.session.pop(f"contas_oferecidas_{destino.pk}", None)
    nome = oferecidas[conta_id]
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


def isca_publica(request: HttpRequest, chave: str) -> HttpResponse:
    """O link da isca na resposta ao comentario: conta e segue para a entrega."""
    isca = Isca.objects.filter(chave_publica=chave).exclude(link="").first()
    if isca is None:
        raise Http404
    Isca.objects.filter(pk=isca.pk).update(cliques=F("cliques") + 1)
    return redirect(isca.link)


@login_required
def iscas(request: HttpRequest) -> HttpResponse:
    """Ofertas para quem comentar: cadastro, resultado e o que espera decisao."""
    from apps.social import iscas as modulo

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "salvar":
            isca = Isca.objects.filter(pk=request.POST.get("isca") or None).first() or Isca()
            for campo in ("nome", "descricao", "resposta", "link"):
                setattr(isca, campo, request.POST.get(campo, "").strip())
            isca.palavra = request.POST.get("palavra", "").strip().upper()[:40]
            isca.modo = request.POST.get("modo") or Isca.Modo.APROXIMADO
            isca.responder_sozinho = request.POST.get("responder_sozinho") == "1"
            isca.ativa = request.POST.get("ativa") == "1"
            isca.redes = request.POST.getlist("redes")
            if not isca.nome or not isca.resposta:
                messages.error(request, _("Diga o que a pessoa recebe e a resposta ao comentario."))
            elif isca.link and not isca.link.startswith(("http://", "https://")):
                messages.error(request, _("O link precisa comecar com http(s)://."))
            else:
                isca.save()
                messages.success(request, _("Isca salva."))
        elif acao in ("responder", "nao_e"):
            comentario = get_object_or_404(Comentario, pk=request.POST.get("comentario"))
            if acao == "nao_e":
                comentario.pedido = modulo.NAO
                comentario.save(update_fields=["pedido"])
            elif modulo.enviar(comentario):
                messages.success(request, _("Respondido."))
            else:
                messages.error(request, _("Nao deu para responder: %(e)s") % {"e": comentario.erro})
        elif acao == "resposta_ia":
            ideias, comentarios = modulo.ler_ideias(request.POST.get("resposta", ""))
            if not ideias:
                messages.error(
                    request,
                    _("Nao achei o bloco ISCAS na resposta. Peca 'pode gerar' e cole tudo."),
                )
            else:
                salvas = modulo.salvar_ideias(ideias)
                messages.success(
                    request,
                    _("%(n)s ideia(s) salvas como iscas paradas: ponha o link e ligue.")
                    % {"n": len(salvas)},
                )
                if comentarios:
                    messages.info(request, comentarios[:1000])
        return redirect("social:iscas")
    editar = Isca.objects.filter(pk=request.GET.get("editar") or None).first()
    return render(
        request,
        "social/iscas.html",
        {
            "aba": "redes",
            "aba_das_redes": "iscas",
            "contagens": _contagens(request),
            "painel": modulo.painel(_conta(request)),
            "a_decidir": modulo.a_decidir(_conta(request)),
            "editar": editar,
            "modos": Isca.Modo.choices,
            "redes": [("instagram", "Instagram"), ("linkedin", "LinkedIn")],
            "pedido": modulo.pedido(),
            "api_da_outra_ia": _api_da_outra_ia(),
        },
    )


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
    from apps.social.models import ConfiguracaoSocial

    return render(
        request,
        "social/bio.html",
        {
            "destino": destino,
            "posts": posts,
            "oferta": ConfiguracaoSocial.carregar().link_da_oferta,
        },
    )


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
    escolhido = _conta(request, exigir=True)
    return render(
        request,
        "social/estrategia.html",
        {
            "aba": "redes",
            "aba_das_redes": "estrategia",
            "contagens": _contagens(request, exigir=True),
            "destinos": destinos,
            "destino": escolhido,
            "plano": modulo.plano(escolhido) if escolhido else None,
            "api_da_outra_ia": _api_da_outra_ia(),
            "respostas_da_api": escolhido.respostas_ia.filter(tipo="propostas")[:5]
            if escolhido
            else [],
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
        from apps.social import anuncios

        try:
            dias = min(int(request.POST.get("dias") or 0), 90) or None
            inicio = parse_date(request.POST.get("inicio") or "")
        except ValueError:
            dias = inicio = None
        anuncios.registrar(
            post,
            valor,
            inicio=inicio,
            dias=dias,
            publico=_publico_escolhido(request, post),
            objetivo=request.POST.get("objetivo", ""),
        )
        messages.success(request, _("Impulso registrado: o resultado passa a contar como pago."))
    elif acao in ("publico_do_impulso", "apagar_impulso"):
        from apps.social import anuncios
        from apps.social.models import Anuncio

        anuncio = get_object_or_404(Anuncio, pk=request.POST.get("anuncio"))
        if acao == "apagar_impulso" and anuncio.origem == Anuncio.Origem.MANUAL:
            post_id = anuncio.post_id
            anuncio.delete()
            anuncios.somar([post_id] if post_id else [])
            messages.success(request, _("Impulso tirado."))
        elif acao == "publico_do_impulso" and anuncio.post is not None:
            anuncio.publico = _publico_escolhido(request, anuncio.post)
            anuncio.save(update_fields=["publico"])
            messages.success(request, _("Publico do impulso salvo."))
    return redirect(request.POST.get("voltar") or voltar)


def _publico_escolhido(request: HttpRequest, post: Post):
    from apps.social.models import PublicoDeImpulso

    pk = request.POST.get("publico")
    if not pk:
        return None
    return PublicoDeImpulso.objects.filter(pk=pk, destino=post.destino).first()


@login_required
def publicos(request: HttpRequest) -> HttpResponse:
    """Os publicos de impulso salvos de uma conta, com o resultado de cada um,
    e o pedido para outra IA sugerir o publico de um post."""
    from apps.social import publicos as modulo
    from apps.social.models import PublicoDeImpulso

    dados = request.POST if request.method == "POST" else request.GET
    destino = get_object_or_404(Destino, pk=dados.get("destino"))
    post = (
        Post.objects.filter(pk=dados.get("post"), destino=destino).first()
        if dados.get("post")
        else None
    )
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "resposta":
            lidos, comentarios = modulo.ler(request.POST.get("resposta", ""))
            if not lidos:
                messages.error(
                    request,
                    _(
                        "Nao achei o bloco PUBLICOS na resposta. Peca a IA 'pode gerar' e "
                        "cole a resposta inteira."
                    ),
                )
            else:
                salvos = modulo.salvar(destino, lidos)
                messages.success(
                    request,
                    _("Publicos salvos: %(n)s. Escolha o usado ao registrar o impulso.")
                    % {"n": ", ".join(p.nome for p in salvos)},
                )
                if comentarios:
                    messages.info(request, comentarios[:1000])
        elif acao == "novo" and request.POST.get("nome", "").strip():
            PublicoDeImpulso.objects.update_or_create(
                destino=destino,
                nome=request.POST["nome"].strip()[:80],
                defaults={"descricao": request.POST.get("descricao", "").strip(), "ativo": True},
            )
            messages.success(request, _("Publico salvo."))
        elif acao == "apagar":
            PublicoDeImpulso.objects.filter(pk=request.POST.get("publico"), destino=destino).update(
                ativo=False
            )
            messages.success(request, _("Publico tirado da lista (o historico fica)."))
        url = f"{reverse('social:publicos')}?destino={destino.pk}"
        return redirect(url + (f"&post={post.pk}" if post else ""))
    return render(
        request,
        "social/publicos.html",
        {
            "aba": "redes",
            "aba_das_redes": "estrategia",
            "contagens": _contagens(request),
            "destino": destino,
            "post": post,
            "quadro": modulo.quadro(destino),
            "pedido": modulo.pedido(post) if post else "",
            "api_da_outra_ia": _api_da_outra_ia(),
        },
    )


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
            "contagens": _contagens(request),
            "destino": destino,
            "leitura": leitura,
            "resposta": resposta,
        },
    )


def _acao_na_lamina(request: HttpRequest, post: Post, acao: str) -> None:
    """Uma lamina so (ou a foto, a "lamina 0"): refazer a imagem, gerar opcoes e
    escolher, e os prompts para copiar."""
    from apps.social import identidade, laminas
    from apps.social.tasks import gerar_imagens_das_laminas

    try:
        n = int(request.POST.get("n") or 0)
    except ValueError:
        n = 0
    if acao in ("refazer_imagem", "opcoes_de_imagem"):
        if not fontes.ha_modelo_de_imagem():
            messages.error(request, _("Nao ha gerador de imagem cadastrado."))
            return
        opcoes = identidade.OPCOES_POR_LAMINA if acao == "opcoes_de_imagem" else 1
        fontes.na_fila(
            gerar_imagens_das_laminas,
            str(post.pk),
            apenas=n,
            opcoes=opcoes,
            rotulo=(f"Imagem da lamina {n}" if n else "Foto do post")
            + (f" ({opcoes} opcoes)" if opcoes > 1 else ""),
            alvo=post.pk,
        )
        messages.info(
            request,
            _("Gerando %(q)s imagem(ns). Recarregue em alguns minutos.") % {"q": opcoes},
        )
    elif acao == "escolher_opcao":
        try:
            i = int(request.POST.get("i") or -1)
        except ValueError:
            i = -1
        if identidade.escolher_opcao(post, n, i):
            laminas.refazer_imagens(post)
            messages.success(request, _("Imagem escolhida."))
    elif acao == "preparar_prompts":
        try:
            for x in identidade.situacao_das_laminas(post):
                identidade.prompts_da_lamina(post, x["n"])
            messages.success(request, _("Prompts prontos em cada lamina, para copiar."))
        except fontes.modelo_indisponivel() as exc:
            messages.error(request, _("Modelo indisponivel agora: %(e)s") % {"e": exc})


def _acao_nas_cenas(request: HttpRequest, post: Post, acao: str) -> None:
    """O video por cenas: editar uma cena, gerar a imagem inicial e a animacao
    (uma cena ou todas), enviar a imagem feita fora e montar."""
    from apps.social import cenas, video
    from apps.social.tasks import gerar_cenas, montar_cenas

    try:
        n = int(request.POST.get("n") or 0)
    except ValueError:
        n = 0
    gera = acao in (
        "cena_gerar",
        "cena_refazer_imagem",
        "cena_sem_referencia",
        "cena_refazer_animacao",
        "cena_qualidade_maxima",
        "cenas_todas",
    )
    if (gera or acao == "cenas_imagens") and not fontes.ha_modelo_de_imagem():
        messages.error(
            request, _("Nao ha gerador de imagem cadastrado (Configuracao > Inferencia).")
        )
        return
    if gera and not fontes.tem_animacao():
        messages.error(
            request,
            _(
                "Nenhuma animacao cadastrada (manage.py configurar_api worker_animacao). Da "
                "para gerar as imagens iniciais e montar o video com elas paradas."
            ),
        )
        return
    try:
        if acao == "cena_salvar":
            cenas.editar(
                post,
                n,
                **{
                    c: request.POST.get(c, "")
                    for c in ("fala", "imagem", "movimento", "texto", "segundos")
                },
            )
            messages.success(request, _("Cena %(n)s salva.") % {"n": n})
        elif acao == "cena_enviar_imagem":
            arquivo = request.FILES.get("imagem")
            if arquivo is None:
                messages.error(request, _("Escolha a imagem."))
                return
            cenas.enviar_quadro(post, n, arquivo)
            messages.success(request, _("Imagem inicial da cena %(n)s trocada.") % {"n": n})
        elif acao == "cena_parar":
            cenas.parar_animacao(post, n)
            messages.success(
                request,
                _(
                    "Animacao da cena %(n)s parada: nada mais e pedido para ela. O worker "
                    "termina o que ja comecou (ou reinicie o worker para liberar a placa)."
                )
                % {"n": n},
            )
        elif acao in (
            "cena_gerar",
            "cena_refazer_imagem",
            "cena_sem_referencia",
            "cena_refazer_animacao",
            "cena_qualidade_maxima",
        ):
            so_imagem = acao in ("cena_refazer_imagem", "cena_sem_referencia")
            if so_imagem:
                # Imagem nova: a animacao da antiga (em curso ou na fila de uma
                # tarefa anterior) nao serve mais, e esta so anima quando pedirem.
                cenas.parar_animacao(post, n)
            else:
                cenas.liberar(post, n)
            na_tela = cenas.para_a_tela(post)
            tem_imagem = 1 <= n <= len(na_tela) and na_tela[n - 1]["tem_quadro"]
            rotulo = {
                "cena_refazer_imagem": "Imagem da cena {n}",
                "cena_sem_referencia": "Imagem da cena {n} (sem referencia)",
                "cena_refazer_animacao": "Animacao da cena {n} (outra variacao)",
                "cena_qualidade_maxima": "Animacao da cena {n} (qualidade maxima)",
            }.get(acao, "Animacao da cena {n}" if tem_imagem else "Imagem e animacao da cena {n}")
            fontes.na_fila(
                gerar_cenas,
                str(post.pk),
                n=n,
                # Refazer a imagem e SO a imagem: confira e depois anime ("Animar a cena").
                so_quadros=so_imagem,
                refazer_quadro=so_imagem,
                sem_referencia=acao == "cena_sem_referencia",
                nova_seed=acao == "cena_refazer_animacao",
                maior=acao == "cena_qualidade_maxima",
                rotulo=rotulo.format(n=n),
                alvo=post.pk,
            )
            messages.info(
                request, _("Gerando a cena %(n)s (minutos). Recarregue depois.") % {"n": n}
            )
        elif acao in ("cenas_todas", "cenas_imagens"):
            if acao == "cenas_todas":
                cenas.liberar(post)
            fontes.na_fila(
                gerar_cenas,
                str(post.pk),
                so_quadros=acao == "cenas_imagens",
                rotulo="Imagens das cenas" if acao == "cenas_imagens" else "Cenas do video",
                alvo=post.pk,
            )
            messages.info(request, _("Gerando, uma cena por vez. Recarregue depois."))
        elif acao == "cenas_montar":
            fontes.na_fila(montar_cenas, str(post.pk), rotulo="Montar o video", alvo=post.pk)
            messages.info(request, _("Montando o video (voz, legendas, transicoes)."))
    except video.VideoIndisponivel as exc:
        messages.error(request, str(exc))


@login_required
def midia_da_cena(request: HttpRequest, pk, n: int, tipo: str) -> HttpResponse:
    """A imagem inicial ou a animacao de uma cena do video (com login)."""
    from django.core.files.storage import default_storage
    from django.http import FileResponse

    from apps.social import cenas

    post = get_object_or_404(Post, pk=pk)
    lista = cenas.cenas(post)
    campo = {"quadro": "quadro", "clipe": "clipe", "referencia": "referencia"}.get(tipo)
    caminho = lista[n - 1].get(campo, "") if campo and 1 <= n <= len(lista) else ""
    if not caminho or not default_storage.exists(caminho):
        raise Http404
    return FileResponse(default_storage.open(caminho, "rb"), filename=caminho.rsplit("/", 1)[-1])


@login_required
def midia_da_lamina(request: HttpRequest, pk, n: int, tipo: str) -> HttpResponse:
    """Uma opcao de imagem de uma lamina (ou da foto, a "lamina 0"), so para
    quem esta logado."""
    from django.core.files.storage import default_storage
    from django.http import FileResponse

    from apps.social import identidade

    post = get_object_or_404(Post, pk=pk)
    if tipo.startswith("opcao-") and tipo[6:].isdigit():
        lista = identidade.opcoes_da_lamina(post, n)
        i = int(tipo[6:])
        caminho = lista[i] if i < len(lista) else ""
    else:
        caminho = ""
    if not caminho or not default_storage.exists(caminho):
        raise Http404
    return FileResponse(default_storage.open(caminho, "rb"), filename=caminho.rsplit("/", 1)[-1])


@login_required
@require_POST
def pedir_a_outra_ia(request: HttpRequest) -> HttpResponse:
    """Manda o pedido (o mesmo de copiar e colar) para a outra IA por API, em
    segundo plano."""
    from apps.social import outra_ia_por_api
    from apps.social.models import RespostaDaOutraIA

    tipo = request.POST.get("tipo")
    voltar = request.POST.get("voltar") or reverse("social:estrategia")
    if not outra_ia_por_api.disponivel():
        messages.error(
            request,
            _(
                "Nenhuma outra IA por API cadastrada. No servidor: manage.py configurar_api "
                "anthropic (ou openai, gemini) --chave ..."
            ),
        )
        return redirect(voltar)
    if tipo not in RespostaDaOutraIA.Tipo.values:
        raise Http404
    destino = Destino.objects.filter(pk=request.POST.get("destino") or None).first()
    post = Post.objects.filter(pk=request.POST.get("post") or None).first()
    if tipo in ("post", "publicos") and post is None:
        raise Http404
    if tipo == "propostas" and destino is None:
        raise Http404
    outra_ia_por_api.pedir(tipo, destino=destino, post=post)
    messages.info(
        request,
        _(
            "Pedido enviado a outra IA (%(api)s). Leva de segundos a alguns minutos: "
            "recarregue a pagina."
        )
        % {"api": outra_ia_por_api.disponivel()},
    )
    return redirect(voltar)


@login_required
def ver_resposta_da_api(request: HttpRequest, pk) -> HttpResponse:
    """A previa de uma resposta de propostas que veio pela API (a pessoa marca
    o que aplicar, como na colada)."""
    from apps.social import outra_ia
    from apps.social.models import RespostaDaOutraIA

    registro = get_object_or_404(RespostaDaOutraIA, pk=pk, tipo="propostas")
    return render(
        request,
        "social/resposta_ia.html",
        {
            "aba": "redes",
            "aba_das_redes": "estrategia",
            "contagens": _contagens(request),
            "destino": registro.destino,
            "leitura": outra_ia.ler(registro.resposta),
            "resposta": registro.resposta,
            "registro": registro,
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
    if request.POST.get("registro"):
        from apps.social.models import RespostaDaOutraIA

        RespostaDaOutraIA.objects.filter(pk=request.POST["registro"]).update(
            situacao=RespostaDaOutraIA.Situacao.APLICADA
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
    escolhido = _conta(request) or next(
        (d for d in destinos if d.conectado), destinos[0] if destinos else None
    )
    contexto = {
        "aba": "redes",
        "aba_das_redes": "diagnostico",
        "contagens": _contagens(request, exigir=True),
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
            "contagens": _contagens(request),
            "destinos": destinos,
            "marcadas": {_conta(request).pk} if _conta(request) else {d.pk for d in destinos},
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
            "contagens": _contagens(request),
            "grupos": list(grupos.items()),
            "destinos": destinos,
            "estoques": [(d, proprio.estoque(d)) for d in destinos if d.fotos_por_cento],
            "config": ConfiguracaoSocial.carregar(),
        },
    )


@login_required
def fundo_privado(request: HttpRequest, pk) -> HttpResponse:
    """Um fundo padrao das laminas, para a tela (com login)."""
    from apps.social.models import FundoDeLamina

    fundo = get_object_or_404(FundoDeLamina, pk=pk)
    return FileResponse(fundo.arquivo.open("rb"))


@login_required
def identidade(request: HttpRequest, pk) -> HttpResponse:
    """A identidade visual da conta: cores e estilo das imagens, personagens e
    cenarios fixos, e os fundos padrao das laminas (capa, meio por tipo,
    final)."""
    from apps.social.forms import IdentidadeForm
    from apps.social.identidade import prompts_de_folha
    from apps.social.models import ElementoVisual, FundoDeLamina

    destino = get_object_or_404(Destino, pk=pk)
    trocou = request.GET.get("destino")
    if trocou and trocou != str(destino.pk):  # a barra de contas trocou a conta
        _conta(request)
        return redirect("social:identidade", trocou)
    request.session[CONTA_NA_SESSAO] = str(destino.pk)
    form = IdentidadeForm(instance=destino)
    com_ia: dict = {}
    if request.method == "POST":
        from apps.social import identidade_ia

        acao = request.POST.get("acao") or "fundo"
        if acao == "pedido_ia":
            ideia = request.POST.get("ideia", "").strip()[:1000]
            com_ia = {"ideia": ideia, "pedido": identidade_ia.pedido(destino, ideia)}
        elif acao in ("ler_ia", "aplicar_ia"):
            resposta = request.POST.get("resposta", "")
            dados = identidade_ia.ler(resposta)
            if acao == "aplicar_ia":
                n = identidade_ia.aplicar(destino, dados)
                messages.success(request, _("Identidade aplicada: %(n)s mudanca(s).") % {"n": n})
                return redirect("social:identidade", destino.pk)
            com_ia = {
                "resposta": resposta,
                "previa": identidade_ia.previa(destino, dados),
                "comentarios": dados.get("comentarios", ""),
            }
            if not com_ia["previa"]:
                messages.info(request, _("Nada novo na resposta (ou os rotulos nao vieram)."))
        elif acao == "visual":
            form = IdentidadeForm(request.POST, instance=destino)
            if form.is_valid():
                form.save()
                messages.success(request, _("Identidade visual salva."))
                return redirect("social:identidade", destino.pk)
            messages.error(request, _("Confira os campos: %(e)s") % {"e": form.errors.as_text()})
        elif acao == "elemento":
            nome = request.POST.get("nome", "").strip()[:60]
            tipo = request.POST.get("tipo")
            descricao = request.POST.get("descricao", "").strip()
            sem_foto = (
                tipo == ElementoVisual.Tipo.LUGAR
                and not request.FILES.get("imagem")
                and not ElementoVisual.objects.filter(destino=destino, nome=nome)
                .exclude(imagem="")
                .exists()
            )
            if not nome or not descricao or tipo not in ElementoVisual.Tipo.values:
                messages.error(request, _("Diga o nome, o tipo e como ele e."))
            elif sem_foto:
                messages.error(request, _("Lugar real precisa da foto: envie a imagem."))
            else:
                elemento, _criado = ElementoVisual.objects.update_or_create(
                    destino=destino,
                    nome=nome,
                    defaults={"tipo": tipo, "descricao": descricao, "ativo": True},
                )
                if request.FILES.get("imagem"):
                    elemento.imagem = request.FILES["imagem"]
                    elemento.save(update_fields=["imagem"])
                messages.success(request, _("'%(n)s' salvo.") % {"n": nome})
            return redirect("social:identidade", destino.pk)
        elif acao == "apagar_elemento":
            ElementoVisual.objects.filter(pk=request.POST.get("elemento"), destino=destino).delete()
            messages.success(request, _("Tirado."))
            return redirect("social:identidade", destino.pk)
        elif acao == "apagar":
            FundoDeLamina.objects.filter(pk=request.POST.get("fundo"), destino=destino).delete()
            messages.success(request, _("Fundo tirado."))
            return redirect("social:identidade", destino.pk)
        else:
            arquivo = request.FILES.get("arquivo")
            papel = request.POST.get("papel")
            if not arquivo or papel not in FundoDeLamina.Papel.values:
                messages.error(request, _("Escolha a imagem e onde ela vai."))
            else:
                FundoDeLamina.objects.create(
                    destino=destino,
                    papel=papel,
                    etiqueta=request.POST.get("etiqueta", "").strip()[:40]
                    if papel == FundoDeLamina.Papel.MEIO
                    else "",
                    arquivo=arquivo,
                    cor_texto=_cor(request.POST.get("cor_texto")),
                    cor_destaque=_cor(request.POST.get("cor_destaque")),
                )
                messages.success(request, _("Fundo salvo: entra nos proximos posts."))
            return redirect("social:identidade", destino.pk)
    return render(
        request,
        "social/identidade.html",
        {
            "aba": "redes",
            "aba_das_redes": "configurar",
            "contagens": _contagens(request, exigir=True),
            "conta": destino,
            "parte": "identidade",
            "destino": destino,
            "form": form,
            "fundos": destino.fundos.all(),
            "papeis": FundoDeLamina.Papel.choices,
            "elementos": destino.elementos.all(),
            "folha_modelo": prompts_de_folha(),
            "tipos": ElementoVisual.Tipo.choices,
            "com_ia": com_ia,
        },
    )


@login_required
def elemento_privado(request: HttpRequest, pk) -> HttpResponse:
    """A imagem de referencia de um personagem ou cenario (com login)."""
    from apps.social.models import ElementoVisual

    elemento = get_object_or_404(ElementoVisual, pk=pk)
    if not elemento.imagem:
        raise Http404
    return FileResponse(elemento.imagem.open("rb"))


def _api_da_outra_ia() -> str:
    from apps.social import outra_ia_por_api

    return outra_ia_por_api.disponivel()


def _cor(valor) -> str:
    import re

    valor = (valor or "").strip()
    return valor if re.fullmatch(r"#[0-9a-fA-F]{6}", valor) else ""


@login_required
def midia_privada(request: HttpRequest, pk) -> HttpResponse:
    """A foto ou video do banco, para a tela (com login)."""
    import mimetypes

    from apps.social.models import Midia

    midia = get_object_or_404(Midia, pk=pk)
    tipo = mimetypes.guess_type(midia.arquivo.name)[0] or "application/octet-stream"
    return FileResponse(midia.arquivo.open("rb"), content_type=tipo)
