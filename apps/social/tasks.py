from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("publibot.social")


@shared_task(bind=True, max_retries=12)
def escrever_post(self, post_id: str, trabalho: str | None = None) -> str:
    """Escreve o post. Sem modelo agora (nao configurado, placa ocupada):
    tenta de novo em 10 minutos, ate 2 horas. `trabalho`: o registro na tela
    de Operacao (ver `fontes.na_fila`)."""
    from apps.social import fontes
    from apps.social.models import Post
    from apps.social.redacao import escrever

    with fontes.acompanhar(trabalho) as andamento:
        post = Post.objects.select_related("destino", "abordagem").filter(pk=post_id).first()
        if post is None or post.situacao not in {Post.Situacao.SUGERIDO, Post.Situacao.GERANDO}:
            return "nada a fazer"
        if _pela_ia_grande(post):
            return "pedido a outra IA"
        try:
            escrever(post)
        except fontes.modelo_indisponivel() as exc:
            post.situacao = Post.Situacao.SUGERIDO
            post.erro = f"esperando o modelo: {exc}"[:2000]
            post.save(update_fields=["situacao", "erro", "atualizado_em"])
            raise self.retry(exc=exc, countdown=600) from exc
        if post.situacao == Post.Situacao.FALHOU:
            andamento.falhou(post.erro or "o modelo nao escreveu")
        return post.situacao


def _pela_ia_grande(post) -> bool:
    """A conta quer a IA grande e ha API: o pedido vai a ela (outra tarefa, que
    aplica a resposta como a colada). Sem API, segue o modelo local."""
    from apps.social import outra_ia_por_api
    from apps.social.models import Destino, Post, RespostaDaOutraIA

    if post.destino.escritor != Destino.Escritor.OUTRA_IA or not outra_ia_por_api.disponivel():
        return False
    if post.respostas_ia.filter(
        tipo=RespostaDaOutraIA.Tipo.POST, situacao=RespostaDaOutraIA.Situacao.ESPERANDO
    ).exists():
        return True  # ja pedido: nao pede duas vezes
    post.situacao = Post.Situacao.GERANDO
    post.save(update_fields=["situacao", "atualizado_em"])
    outra_ia_por_api.pedir(RespostaDaOutraIA.Tipo.POST, post=post)
    return True


@shared_task(bind=True, max_retries=12)
def gerar_imagens_das_laminas(
    self, post_id: str, apenas: int | None = None, opcoes: int = 1, trabalho: str | None = None
) -> int:
    """As imagens das laminas pelo gerador do PubliBot (todas as que faltam, ou
    so a `apenas`; com `opcoes`, varias para escolher). Gerador ocupado: tenta
    de novo em 10 minutos (o que ja saiu fica)."""
    from apps.social import fontes, identidade, laminas
    from apps.social.models import Post

    with fontes.acompanhar(trabalho):
        post = Post.objects.select_related("destino").filter(pk=post_id).first()
        if post is None:
            return 0
        try:
            feitas = identidade.gerar_imagens(post, apenas=apenas, opcoes=opcoes)
        except fontes.imagem_indisponivel() + fontes.modelo_indisponivel() as exc:
            laminas.refazer_imagens(post)
            raise self.retry(exc=exc, countdown=600) from exc
        laminas.refazer_imagens(post)
        return feitas


@shared_task(bind=True, max_retries=6)
def gerar_video(self, post_id: str, trabalho: str | None = None) -> str:
    """O video do post (narracao + laminas). Voz ou servidor ocupados: tenta
    de novo em 10 minutos; erro de verdade vira aviso no post."""
    from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError
    from apps.social import fontes, video
    from apps.social.models import Post

    with fontes.acompanhar(trabalho) as andamento:
        post = Post.objects.select_related("destino").filter(pk=post_id).first()
        if post is None:
            return "sem post"
        try:
            video.montar(post)
        except ProviderTransientError as exc:
            raise self.retry(exc=exc, countdown=600) from exc
        except (video.VideoIndisponivel, ProviderPermanentError) as exc:
            post.avisos = [*(post.avisos or []), f"video nao gerado: {exc}"]
            post.save(update_fields=["avisos", "atualizado_em"])
            andamento.falhou(f"video nao gerado: {exc}")
            return "erro"
        destino = post.destino
        if (
            post.situacao == Post.Situacao.RASCUNHO
            and not post.avisos
            and destino.aprovacao == destino.Aprovacao.AUTOMATICO
        ):
            from apps.social.publicacao import aprovar

            aprovar(post, por=None)
        return "ok"


@shared_task(bind=True, max_retries=12)
def animar_laminas(
    self, post_id: str, apenas: int | None = None, trabalho: str | None = None
) -> int:
    """Anima as laminas que tem imagem e ainda nao tem clipe (ou so a `apenas`).
    Placa ocupada: tenta de novo em 10 minutos (o que ja saiu fica). No fim,
    se o post ja e video, monta de novo com os clipes."""
    from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError
    from apps.social import fontes, laminas, video
    from apps.social.models import Post

    with fontes.acompanhar(trabalho) as andamento:
        post = Post.objects.select_related("destino").filter(pk=post_id).first()
        if post is None:
            return 0
        total = len(
            laminas.com_a_chamada(
                (post.extras or {}).get("laminas") or [],
                post.destino.chamada_final or laminas.CHAMADA,
            )
        )
        com_clipe = laminas.clipes_das_laminas(post)
        feitas, falhas = 0, []
        for n in [apenas] if apenas else range(1, total + 1):
            if apenas is None and (n in com_clipe or not video.imagem_da_lamina(post, n)):
                continue
            try:
                video.animar_lamina(post, n)
            except ProviderTransientError as exc:
                raise self.retry(exc=exc, countdown=600) from exc
            except (video.VideoIndisponivel, ProviderPermanentError) as exc:
                post.avisos = [*(post.avisos or []), f"lamina {n} nao animada: {exc}"]
                post.save(update_fields=["avisos", "atualizado_em"])
                falhas.append(f"lamina {n}: {exc}")
                continue
            feitas += 1
        if falhas and not feitas:
            andamento.falhou("; ".join(falhas))
        if feitas and (post.extras or {}).get("formato") == "video":
            fontes.na_fila(gerar_video, str(post.pk), rotulo="Video do post", alvo=post.pk)
        return feitas


@shared_task(bind=True, max_retries=3)
def perguntar_a_outra_ia(self, resposta_id: str, trabalho: str | None = None) -> str:
    """O pedido vai a outra IA por API; a resposta passa pelos mesmos leitores
    da colada. Post, iscas e publicos ja se aplicam (entram para revisar);
    propostas esperam a pessoa escolher na previa."""
    from apps.inference.providers.base import ProviderTransientError
    from apps.social import fontes, outra_ia_por_api
    from apps.social.models import RespostaDaOutraIA

    with fontes.acompanhar(trabalho) as andamento:
        pedido = RespostaDaOutraIA.objects.filter(pk=resposta_id).first()
        if pedido is None:
            return "sem pedido"
        try:
            outra_ia_por_api.responder(pedido)
        except ProviderTransientError as exc:
            raise self.retry(exc=exc, countdown=300) from exc
        if pedido.situacao == RespostaDaOutraIA.Situacao.ERRO:
            andamento.falhou(pedido.erro or "a outra IA nao respondeu")
        return pedido.situacao


@shared_task
def sugerir_posts() -> int:
    """Uma vez por dia: a escolha do dia em cada conta (com 'sugerir sozinho')."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.escolha import rodada

    return para_cada_tenant(rodada, "sugerir_posts")


@shared_task
def publicar_posts() -> int:
    """De 5 em 5 minutos: publica os aprovados que venceram, nas contas conectadas,
    responde os comentarios cuja pergunta ja foi respondida e quem pediu a isca."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social import iscas
    from apps.social.comentarios import responder_aprovadas
    from apps.social.escolha import atender_pedidos
    from apps.social.publicacao import publicar_vencidos

    return para_cada_tenant(
        lambda: publicar_vencidos()
        + responder_aprovadas()
        + iscas.responder()
        + retomar_escrita()
        + atender_pedidos(),
        "publicar_posts",
    )


# Post esperando o modelo (placa desligada, disjuntor aberto) ha mais que isto
# volta para a fila: as tentativas da propria tarefa acabam em 2 horas, e o
# post ficava "Escrevendo..." para sempre. Alcance: os sugeridos recentes.
RETOMAR_DEPOIS_DE_MINUTOS = 30
RETOMAR_ATE_DIAS = 14


def retomar_escrita() -> int:
    """Os posts parados esperando escrita voltam para a fila (um por vez a cada
    meia hora): quando o modelo volta, saem sozinhos."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.social.models import Post

    agora = timezone.now()
    parados = Post.objects.filter(
        situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.GERANDO],
        atualizado_em__lt=agora - timedelta(minutes=RETOMAR_DEPOIS_DE_MINUTOS),
        criado_em__gte=agora - timedelta(days=RETOMAR_ATE_DIAS),
    )
    ids = [str(pk) for pk in parados.values_list("pk", flat=True)[:20]]
    # "Toca" o post: a proxima retomada so depois de mais meia hora.
    Post.objects.filter(pk__in=ids).update(atualizado_em=agora)
    for pk in ids:
        escrever_post.delay(pk)
    return len(ids)


@shared_task
def medir_posts() -> int:
    """Uma vez por dia: resultado, comentarios e placar das abordagens."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.medicao import rodada

    return para_cada_tenant(rodada, "medir_posts")


@shared_task
def recalcular_temas() -> int:
    """Uma vez por dia: os temas que atravessam os artigos, com a nota."""
    from apps.accounts.varredura import para_cada_tenant
    from apps.social.temas import recalcular

    return para_cada_tenant(recalcular, "recalcular_temas")


@shared_task
def consolidar_placar_coletivo() -> int:
    """Uma vez por dia: o placar das abordagens somado entre TODOS os clientes
    (so contagens por rede e nome de abordagem; nenhum texto sai do cliente).
    E o ponto de partida de quem esta comecando."""
    from django_tenants.utils import schema_context

    from apps.accounts.varredura import schemas_ativos
    from apps.social.experimentos import consolidar_coletivo, placar_deste_cliente

    placares = []
    for schema in schemas_ativos():
        with schema_context(schema):
            try:
                placares.append(placar_deste_cliente())
            except Exception:
                logger.exception("Placar do tenant %s indisponivel.", schema)
    total = consolidar_coletivo(placares)
    return sum(s + f for nomes in total.values() for s, f in nomes.values())


@shared_task
def recalcular_temas_do_cliente(schema: str) -> int:
    """Os temas de UM cliente, agora (primeira visita a Estrategia sem temas)."""
    from django_tenants.utils import schema_context

    from apps.social.temas import recalcular

    with schema_context(schema):
        return recalcular()


@shared_task
def importar_historico(schema: str, destino_id: str) -> dict:
    """O passado de uma conta recem-conectada: posts, resultado, comentarios e
    o gasto com anuncios. Roda em lotes (a medicao diaria continua o resto)."""
    from django_tenants.utils import schema_context

    from apps.social import anuncios, historico
    from apps.social.models import Destino
    from apps.social.redes.base import ErroDaRede

    with schema_context(schema):
        destino = Destino.objects.filter(pk=destino_id).first()
        if destino is None or not historico.suporta(destino):
            return {}
        try:
            feito = historico.importar(destino)
        except ErroDaRede as exc:
            logger.info("Historico de %s nao importado: %s", destino, exc)
            return {"erro": str(exc)}
        if destino.anuncios_conta_id:
            feito["anuncios"] = anuncios.sincronizar(destino)
        return feito


@shared_task(bind=True, max_retries=24)
def transcrever_entrada(self, schema: str, entrada_id: str) -> str:
    """O audio de um post proprio vira texto (worker da placa) e os posts sao
    criados. Placa ocupada: tenta de novo em 10 minutos, por ate 4 horas."""
    from django_tenants.utils import schema_context

    from apps.social import fontes, proprio
    from apps.social.models import Entrada

    with schema_context(schema):
        entrada = Entrada.objects.filter(pk=entrada_id).first()
        if entrada is None or entrada.situacao_do_audio != Entrada.Transcricao.ESPERANDO:
            return "nada a fazer"
        try:
            proprio.transcrever(entrada)
        except fontes.TranscricaoAdiada as exc:
            raise self.retry(exc=exc, countdown=600) from exc
        return entrada.situacao_do_audio


@shared_task
def ler_links(schema: str, entrada_id: str) -> int:
    """As paginas coladas num post proprio (noticia, estudo) sao lidas; depois
    vem a transcricao do audio (se houver) e os posts."""
    from django_tenants.utils import schema_context

    from apps.social import proprio
    from apps.social.models import Entrada

    with schema_context(schema):
        entrada = Entrada.objects.filter(pk=entrada_id).first()
        if entrada is None:
            return 0
        proprio.ler_referencias(entrada)
        return sum(1 for r in entrada.referencias if r.get("texto"))


@shared_task
def descrever_fotos(schema: str) -> int:
    """Fotos do banco sem nota ganham descricao (modelo de visao, opcional)."""
    from django_tenants.utils import schema_context

    from apps.social.proprio import descrever_pendentes

    with schema_context(schema):
        return descrever_pendentes()


@shared_task
def preparar_material(post_id: str, trabalho: str | None = None) -> str:
    """O material completo (com vetores) do post que ainda nao tem, para a tela
    da outra IA nao vetorizar dentro da requisicao (ver criacao)."""
    from apps.social import fontes
    from apps.social.models import Post
    from apps.social.redacao import artigo_e_material

    with fontes.acompanhar(trabalho):
        post = Post.objects.select_related("destino").filter(pk=post_id).first()
        if post is None or post.material:
            return "nada a fazer"
        _artigo, mat = artigo_e_material(post)
        if mat:
            Post.objects.filter(pk=post.pk, material={}).update(material=mat)
        return "pronto"
