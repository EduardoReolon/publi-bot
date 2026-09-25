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


def _tamanho() -> str:
    """O tamanho pedido ao gerador, lido no momento da chamada.

    Funcao e nao constante de modulo: assim `override_settings` alcanca, e
    trocar o tamanho no `.env` nao exige entender que o valor foi congelado na
    importacao.
    """
    from django.conf import settings

    return getattr(settings, "IMAGEM_TAMANHO", "1344x704")


def _negativo() -> str:
    """O prompt negativo, lido no momento da chamada (mesmo motivo de `_tamanho`)."""
    from django.conf import settings

    return getattr(settings, "IMAGEM_NEGATIVO", "")


class SemConexaoDeImagem(RuntimeError):
    """Nenhuma conexao de geracao de imagem cadastrada e ativa."""


class GeradorDeImagemOcupado(RuntimeError):
    """Ha conexao de imagem, mas a maquina esta sem vaga agora.

    Separada de `SemConexaoDeImagem` porque as duas pedem acoes opostas: uma e
    "cadastre uma conexao", a outra e "espere". Enquanto as duas eram a mesma
    excecao, uma placa ocupada mandava a pessoa cadastrar uma conexao que ja
    existia e ja funcionava.
    """


class LimiteDeLotes(RuntimeError):
    """O artigo ja tem lotes demais."""


# Palavras curtas que praticamente so existem em portugues. Nao e deteccao de
# idioma — e um detector de UM caso: a descricao que voltou em portugues.
#
# Criterio de escolha: cada uma tem de ser improvavel num texto ingles. Por
# isso `de`, `no` e `a` ficaram de fora (existem em ingles ou em nomes
# proprios), e `com` tambem (`.com`).
_PISTAS_DE_PORTUGUES = frozenset(
    {
        "uma",
        "um",
        "sobre",
        "para",
        "que",
        "mesa",
        "luz",
        "fundo",
        "sem",
        "dos",
        "das",
        "numa",
        "imagem",
        "foto",
        "ambiente",
    }
)


def parece_portugues(texto: str) -> bool:
    """Se a descricao saiu no idioma errado.

    O SDXL codifica o prompt com o CLIP, treinado so em ingles. Um prompt em
    portugues nao da erro: ele e ignorado quase inteiro, e o modelo desenha a
    partir das poucas palavras que reconheceu. A imagem sai — generica,
    desconexa, com aquela cara de IA — e nao ha nada no log dizendo por que.

    Por isso isto e um aviso e nao uma recusa: o lote continua saindo, e quem
    revisa continua escolhendo. O que muda e haver uma linha de log apontando
    para a causa, em vez de uma tarde procurando defeito no modelo de imagem.
    """
    palavras = {palavra.strip(".,;:()[]\"'").lower() for palavra in texto.split()}
    return len(palavras & _PISTAS_DE_PORTUGUES) >= 2


def descrever_capa(article: Article, *, site=None, job=None) -> tuple[str, object]:
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
        job=job,
    )
    descricao = resultado.texto.strip()

    if parece_portugues(descricao):
        logger.warning(
            "Artigo %s: a descricao da capa parece estar em portugues, e o SDXL "
            "so entende ingles — a imagem vai sair generica. Revise o prompt "
            "'image_prompt' em Configuracao > Prompts. Descricao: %r",
            article.pk,
            descricao[:200],
        )

    return descricao, resultado.prompt_run


def gerar_opcoes(
    article: Article, *, quantidade: int = OPCOES_POR_LOTE, site=None, job=None
) -> list[ArticleImage]:
    """Gera um lote novo de opcoes, sem tocar nos lotes anteriores.

    Uma falha do provedor e registrada (disjuntor e `InferenceLog` com o
    `job`) antes de subir. O log com o trabalho e o que permite ao passo contar
    quantas vezes a imagem ja falhou e parar de tentar
    (`falhas_de_imagem_do_job`).
    """
    from apps.inference.leases import gerar_owner_key, registrar_falha, reserva
    from apps.inference.providers.base import (
        ProviderPermanentError,
        ProviderTransientError,
        get_image_provider,
    )

    lote = _proximo_lote(article)
    if lote > MAXIMO_DE_LOTES:
        raise LimiteDeLotes(
            f"este artigo ja tem {MAXIMO_DE_LOTES} lotes de imagem. "
            f"Escolha uma das opcoes existentes ou ajuste o texto do artigo."
        )

    conexao = _conexao_de_imagem()
    descricao, prompt_run = descrever_capa(article, site=site, job=job)

    modelo = conexao.default_model
    if not modelo:
        raise SemConexaoDeImagem(f"a conexao {conexao.name!r} nao tem modelo padrao configurado.")

    cliente = get_image_provider(conexao)
    try:
        with reserva(conexao, owner_key=gerar_owner_key(), model_name=modelo):
            geradas = cliente.generate(
                model=modelo,
                prompt=descricao,
                quantidade=quantidade,
                tamanho=_tamanho(),
                negativo=_negativo(),
            )
    except (ProviderTransientError, ProviderPermanentError) as exc:
        _registrar_falha_de_imagem(conexao, modelo, exc, job=job)
        # Um 503 com `error.code` e o worker respondendo "nao e a hora" — ele
        # esta de pe e decidindo. Pelo contrato dele, isso nao abre disjuntor:
        # alguns minutos de disputa pela placa tirariam a conexao do ar para
        # todos os trabalhos da fila.
        if not (isinstance(exc, ProviderTransientError) and exc.code):
            registrar_falha(conexao)
        raise

    _registrar_uso(conexao, modelo, len(geradas))
    criadas = _gravar(article, geradas, lote=lote, descricao=descricao, prompt_run=prompt_run)

    logger.info("Artigo %s: lote %s com %s opcao(oes) de capa.", article.pk, lote, len(criadas))
    return criadas


def _conexao_de_imagem():
    """A conexao de imagem com vaga agora.

    `escolher_conexao` devolve `None` por dois motivos que nao se parecem em
    nada: **nao ha conexao de imagem nenhuma**, ou **ha e esta sem vaga** (a
    placa ocupada, ou o disjuntor aberto). Enquanto os dois caiam na mesma
    excecao, a segunda situacao mandava a pessoa "cadastrar uma conexao do tipo
    image" — que ja existia, respondia ao `--testar` e estava gerando imagem no
    pedido anterior.

    O caminho do texto (`apps/content/inference.py`) ja separava os dois. Este
    nao, e a divergencia custou uma investigacao.
    """
    from apps.content.inference import _tenant_atual
    from apps.inference.leases import descrever_ocupacao, escolher_conexao
    from apps.inference.models import InferenceConnection

    conexao = escolher_conexao(
        workload=InferenceConnection.Workload.IMAGE, tenant=_tenant_atual(), model_name=""
    )
    if conexao is not None:
        return conexao

    if not ha_conexao_de_imagem():
        raise SemConexaoDeImagem(
            "nenhuma conexao de geracao de imagem disponivel. Cadastre uma em "
            "Configuracao > Inferencia, do tipo 'image'."
        )

    # `descrever_ocupacao` nomeia quem segura a reserva e ha quanto tempo, ou
    # explica o disjuntor. E a diferenca entre esperar e agir.
    raise GeradorDeImagemOcupado(descrever_ocupacao())


def ha_conexao_de_imagem() -> bool:
    """Se existe alguma conexao de imagem ativa — sem olhar se tem vaga.

    Publica porque a tela precisa dela ANTES de enfileirar: sem gerador
    cadastrado, mandar o trabalho para a fila so adia a mesma mensagem, e quem
    clicou fica esperando um lote que nunca vem.
    """
    from apps.inference.models import InferenceConnection

    return any(
        candidata.atende(InferenceConnection.Workload.IMAGE)
        for candidata in InferenceConnection.objects.filter(is_active=True)
    )


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


def _registrar_falha_de_imagem(conexao, modelo: str, exc: Exception, *, job=None) -> None:
    from apps.inference.models import InferenceConnection
    from apps.ops.models import InferenceLog

    InferenceLog.objects.create(
        connection=conexao,
        job=job,
        model_name=modelo,
        workload=InferenceConnection.Workload.IMAGE,
        succeeded=False,
        error=str(exc)[:2000],
    )


def falhas_de_imagem_do_job(job) -> int:
    """Quantas chamadas de imagem deste trabalho ja falharam.

    Contado no `InferenceLog`, e nao num campo do trabalho: o adiamento nao
    grava payload, e o log ja existe e ja diz o que aconteceu em cada vez.
    """
    from apps.inference.models import InferenceConnection
    from apps.ops.models import InferenceLog

    if job is None or job._state.adding:
        return 0
    return InferenceLog.objects.filter(
        job=job, workload=InferenceConnection.Workload.IMAGE, succeeded=False
    ).count()


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
