"""Geracao das opcoes de imagem de capa, e a escolha entre elas.

Tres decisoes moram aqui, e as tres sao sobre o mesmo ponto: **quem escolhe a
imagem e uma pessoa.**

**Varias opcoes, nao uma.** Escolher exige comparar. Com uma opcao unica a
revisao vira "aceita ou pede de novo" — mais lenta, e o que costuma sair e a
primeira que nao incomodou, nao a melhor.

**Nada e descartado.** Pedir mais exemplos acrescenta um lote; as opcoes
anteriores continuam disponiveis. A terceira do primeiro lote pode ser melhor
que tudo o que veio depois, e apaga-la para "limpar a tela" jogaria fora uma
imagem ja paga.

**A imagem nunca entra sozinha.** Nao ha capa escolhida por padrao. Um artigo
publicado sem que ninguem tenha olhado a imagem e como um artigo publicado sem
que ninguem tenha lido o texto.

Respostas de Q&A nao passam por aqui: sao textos curtos numa listagem de
perguntas, e ilustrar cada uma custaria uma inferencia por pergunta para algo
que ninguem pediu.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.content.imagens import ImagemInvalida, converter_para_webp
from apps.content.models import Article, ArticleImage

logger = logging.getLogger("publibot.content")

# Tres e o menor numero em que comparar significa alguma coisa. Com duas, a
# escolha vira "esta ou aquela"; acima de tres o custo cresce e a atencao de
# quem revisa nao acompanha.
OPCOES_POR_LOTE = 3

# Teto de lotes por artigo. Nao e economia mesquinha: sem limite, "gerar mais"
# vira um caca-niquel, e o proximo lote nunca e o ultimo.
MAXIMO_DE_LOTES = 5

TAMANHO = "1024x1024"


class SemConexaoDeImagem(RuntimeError):
    """Nenhuma conexao de geracao de imagem cadastrada e ativa."""


class LimiteDeLotes(RuntimeError):
    """O artigo ja tem lotes demais."""


def descrever_capa(article: Article, *, site=None) -> tuple[str, object]:
    """Pede ao modelo de texto uma descricao concreta para a imagem.

    Duas etapas — descrever, depois gerar — e nao uma so: modelos de imagem
    leem mal um artigo inteiro, e mandar o titulo cru produz sempre a mesma
    foto generica de banco de imagens.
    """
    from apps.content.inference import executar_prompt

    resultado = executar_prompt(
        key="image_prompt",
        variaveis={
            "titulo": article.title,
            "resumo": article.excerpt or article.meta_description or article.title,
        },
        site=site,
    )
    return resultado.texto.strip(), resultado.prompt_run


def gerar_opcoes(
    article: Article, *, quantidade: int = OPCOES_POR_LOTE, site=None
) -> list[ArticleImage]:
    """Gera um lote novo de opcoes, sem tocar nos lotes anteriores."""
    from apps.inference.leases import gerar_owner_key, reserva
    from apps.inference.providers.base import get_image_provider

    lote = _proximo_lote(article)
    if lote > MAXIMO_DE_LOTES:
        raise LimiteDeLotes(
            f"este artigo ja tem {MAXIMO_DE_LOTES} lotes de imagem. "
            f"Escolha uma das opcoes existentes ou ajuste o texto do artigo."
        )

    conexao = _conexao_de_imagem()
    descricao, prompt_run = descrever_capa(article, site=site)

    modelo = conexao.default_model
    if not modelo:
        raise SemConexaoDeImagem(f"a conexao {conexao.name!r} nao tem modelo padrao configurado.")

    cliente = get_image_provider(conexao)
    with reserva(conexao, owner_key=gerar_owner_key(), model_name=modelo):
        geradas = cliente.generate(
            model=modelo, prompt=descricao, quantidade=quantidade, tamanho=TAMANHO
        )

    _registrar_uso(conexao, modelo, len(geradas))
    criadas = _gravar(article, geradas, lote=lote, descricao=descricao, prompt_run=prompt_run)

    logger.info("Artigo %s: lote %s com %s opcao(oes) de capa.", article.pk, lote, len(criadas))
    return criadas


def _conexao_de_imagem():
    from apps.content.inference import _tenant_atual
    from apps.inference.leases import escolher_conexao
    from apps.inference.models import InferenceConnection

    conexao = escolher_conexao(
        workload=InferenceConnection.Workload.IMAGE, tenant=_tenant_atual(), model_name=""
    )
    if conexao is None:
        raise SemConexaoDeImagem(
            "nenhuma conexao de geracao de imagem disponivel. Cadastre uma em "
            "Configuracao > Inferencia, do tipo 'image'."
        )
    return conexao


def _registrar_uso(conexao, modelo: str, quantas: int) -> None:
    from apps.inference.leases import registrar_sucesso
    from apps.inference.models import InferenceConnection
    from apps.ops.models import InferenceLog

    registrar_sucesso(conexao)
    InferenceLog.objects.create(
        connection=conexao,
        model_name=modelo,
        workload=InferenceConnection.Workload.IMAGE,
        succeeded=True,
        # Imagem nao tem token. O numero de opcoes e o que da para contabilizar,
        # e e o que decide o custo neste provedor.
        output_tokens=quantas,
    )


def _proximo_lote(article: Article) -> int:
    ultimo = article.images.order_by("-batch").values_list("batch", flat=True).first()
    return (ultimo or 0) + 1


@transaction.atomic
def _gravar(article, geradas, *, lote: int, descricao: str, prompt_run) -> list[ArticleImage]:
    from django.core.files.base import ContentFile

    criadas = []
    for posicao, gerada in enumerate(geradas, start=1):
        try:
            arquivo = converter_para_webp(_memoria(gerada.conteudo), nome=f"capa-{lote}-{posicao}")
        except ImagemInvalida:
            # Uma opcao ilegivel nao derruba as outras: duas boas valem mais
            # que um lote inteiro perdido.
            logger.warning("Artigo %s: opcao %s do lote %s nao abriu.", article.pk, posicao, lote)
            continue

        imagem = ArticleImage(
            article=article,
            batch=lote,
            order=posicao,
            prompt=gerada.prompt_revisado or descricao,
            prompt_run=prompt_run,
            alt_text=article.title[:300],
        )
        imagem.image.save(arquivo.name, ContentFile(arquivo.read()), save=False)
        imagem.save()
        criadas.append(imagem)
    return criadas


def _memoria(conteudo: bytes):
    import io

    return io.BytesIO(conteudo)


@transaction.atomic
def escolher_capa(article: Article, imagem: ArticleImage) -> ArticleImage:
    """Marca uma opcao como a capa, desmarcando a anterior.

    Desmarcar antes de marcar, na mesma transacao: a restricao do banco aceita
    UMA escolhida por artigo, e inverter a ordem faria a troca de capa falhar
    com erro de unicidade em vez de funcionar.
    """
    if imagem.article_id != article.pk:
        raise ValueError("a imagem nao pertence a este artigo.")

    article.images.filter(is_chosen=True).exclude(pk=imagem.pk).update(is_chosen=False)
    imagem.is_chosen = True
    imagem.save(update_fields=["is_chosen"])
    return imagem


def capa_escolhida(article: Article) -> ArticleImage | None:
    return article.images.filter(is_chosen=True).first()


def url_publica_da_capa(imagem: ArticleImage) -> str:
    """A URL absoluta que o site de destino usa para buscar a capa.

    Montada a partir do dominio primario do tenant, e nao de uma configuracao
    separada: um segundo lugar guardando o mesmo endereco divergiria, e o
    sintoma seria uma imagem quebrada no site do cliente — descoberta dias
    depois, por outra pessoa.

    Devolve string vazia quando o tenant nao tem dominio resolvido (o caso de
    um comando rodando fora de requisicao, por exemplo). Quem chama decide o
    que fazer: o payload simplesmente sai sem capa, em vez de sair com um link
    quebrado.
    """
    from django.conf import settings
    from django.urls import reverse

    from apps.content.inference import _tenant_atual

    tenant = _tenant_atual()
    if tenant is None:
        return ""

    dominio = tenant.domains.filter(is_primary=True).first() or tenant.domains.first()
    if dominio is None:
        return ""

    caminho = reverse("content:capa_publica", args=[imagem.pk], urlconf="core.urls_tenants")
    return f"{settings.ESQUEMA_PUBLICO}://{dominio.domain}{caminho}"


def digest_da_capa(imagem: ArticleImage) -> str:
    """SHA-256 do arquivo, para o site conferir o que baixou."""
    import hashlib

    digest = hashlib.sha256()
    with imagem.image.open("rb") as arquivo:
        for pedaco in iter(lambda: arquivo.read(65_536), b""):
            digest.update(pedaco)
    return digest.hexdigest()
