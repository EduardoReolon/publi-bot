"""A "outra IA" por API, no lugar de copiar e colar.

O pedido e o MESMO de copiar e colar (propostas, post inteiro, iscas, publico
do impulso), com um aviso no fim para gerar os blocos sem conversar antes
(`fontes.AVISO_DA_API`). A resposta passa pelos mesmos leitores da colada:

* post inteiro, ideias de iscas e publicos: aplicados direto (entram para
  revisar, como quando colados);
* propostas: ficam prontas para a previa, onde a pessoa marca o que aplicar.

Qual API: a conexao com a carga "outra IA por API" (`manage.py configurar_api
anthropic|openai|gemini|openrouter|deepseek|groq --chave ...`). Roda em segundo
plano (`tasks.perguntar_a_outra_ia`): a IA grande leva de segundos a minutos.
"""

from __future__ import annotations

import logging

from apps.social import fontes
from apps.social.models import Destino, Post, RespostaDaOutraIA

logger = logging.getLogger("publibot.social")


def disponivel() -> str:
    """O nome da API configurada ("" sem nenhuma)."""
    try:
        return fontes.outra_ia_por_api()
    except Exception:  # sem conexao nenhuma, a tela so nao mostra o botao
        return ""


def pedir(
    tipo: str, *, destino: Destino | None = None, post: Post | None = None
) -> RespostaDaOutraIA:
    """Monta o pedido (o mesmo de copiar) e manda para a fila."""
    from apps.social import criacao, iscas, outra_ia, publicos
    from apps.social.tasks import perguntar_a_outra_ia

    if tipo == RespostaDaOutraIA.Tipo.PROPOSTAS:
        texto = outra_ia.pedido(destino)
    elif tipo == RespostaDaOutraIA.Tipo.POST:
        texto = criacao.pedido(post)
    elif tipo == RespostaDaOutraIA.Tipo.ISCAS:
        texto = iscas.pedido()
    else:
        texto = publicos.pedido(post)
    registro = RespostaDaOutraIA.objects.create(
        tipo=tipo, destino=destino or (post.destino if post else None), post=post, pedido=texto
    )
    fontes.na_fila(
        perguntar_a_outra_ia,
        str(registro.pk),
        rotulo=f"Outra IA por API: {registro.get_tipo_display().lower()}",
        alvo=registro.pk,
    )
    return registro


def responder(registro: RespostaDaOutraIA) -> None:
    """Chama a API e aplica a resposta como se tivesse sido colada."""
    from apps.inference.providers.base import ProviderTransientError
    from apps.social import criacao, iscas, publicos

    try:
        registro.resposta = fontes.perguntar_a_outra_ia(registro.pedido)
    except ProviderTransientError:
        raise
    except Exception as exc:
        registro.situacao = RespostaDaOutraIA.Situacao.ERRO
        registro.erro = str(exc)[:2000]
        registro.save(update_fields=["situacao", "erro", "atualizado_em"])
        logger.info("Outra IA por API falhou (%s): %s", registro.tipo, exc)
        _post_falhou(registro)
        return

    tipo, situacao = registro.tipo, RespostaDaOutraIA.Situacao.APLICADA
    if tipo == RespostaDaOutraIA.Tipo.POST:
        aplicou, _avisos, _comentarios = criacao.aplicar(registro.post, registro.resposta)
        if not aplicou:
            situacao, registro.erro = RespostaDaOutraIA.Situacao.ERRO, "resposta sem os blocos"
    elif tipo == RespostaDaOutraIA.Tipo.ISCAS:
        ideias, _comentarios = iscas.ler_ideias(registro.resposta)
        iscas.salvar_ideias(ideias)
    elif tipo == RespostaDaOutraIA.Tipo.PUBLICOS:
        lidos, _comentarios = publicos.ler(registro.resposta)
        publicos.salvar(registro.destino, lidos)
    else:
        situacao = RespostaDaOutraIA.Situacao.PRONTA
    registro.situacao = situacao
    registro.save(update_fields=["resposta", "situacao", "erro", "atualizado_em"])
    if situacao == RespostaDaOutraIA.Situacao.ERRO:
        _post_falhou(registro)


def _post_falhou(registro: RespostaDaOutraIA) -> None:
    """O post que esperava a IA grande nao fica "Escrevendo..." para sempre (e
    nao e pedido de novo a cada meia hora): vira falho, com o motivo."""
    post = registro.post
    if registro.tipo != RespostaDaOutraIA.Tipo.POST or post is None:
        return
    if post.situacao in (Post.Situacao.SUGERIDO, Post.Situacao.GERANDO):
        post.situacao = Post.Situacao.FALHOU
        post.erro = f"a IA grande nao escreveu: {registro.erro}"[:2000]
        post.save(update_fields=["situacao", "erro", "atualizado_em"])
