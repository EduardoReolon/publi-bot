"""A UNICA porta entre as redes sociais e o resto do PubliBot.

Tudo o que o modulo le do nucleo (artigo, negocio, metricas do site, guia
editorial, modelo de linguagem, perguntas) passa por aqui, em formas simples
(dataclass, dict). Se um dia as redes virarem um servico separado, e este
arquivo que vira cliente de API — o resto do modulo nao muda. O teste
`test_fronteira.py` garante que nenhum outro arquivo do modulo importa o nucleo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger("publibot.social")

_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú\"“(0-9])")
_MARCAS = re.compile(r"\[[^\]]*\]\([^)]*\)|<[^>]+>|\*\*|__|`")


def _corpo(markdown: str) -> str:
    """O markdown sem a lista de referencias do fim."""
    return re.split(r"\n#+\s*Refer[eê]ncias|\n<details", markdown or "")[0]


def _ancora(achado: re.Match) -> str:
    texto = achado.group(0)
    return re.sub(r"^\[([^\]]*)\]\([^)]*\)$", r"\1", texto) if texto.startswith("[") else ""


# Abreviacoes que terminam em ponto sem terminar a frase: "Silva et al. 2024",
# "Dr. Carlos", "p. 12". Partir ali cortava o achado no meio.
_ABREVIACOES = re.compile(
    r"(?:\bet al|\b(?:dr|dra|sr|sra|prof|profa|p|pp|n|nº|ex|aprox|cf|vol|ed|fig|tab|"
    r"jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez))\.$",
    re.I,
)


def dividir_em_frases(texto: str, *, minimo: int = 6) -> list[str]:
    """As frases do texto (de `minimo` palavras para cima), sem cortar numa
    abreviacao."""
    frases = []
    for paragrafo in texto.split("\n"):
        paragrafo = paragrafo.strip().lstrip("#-* ").strip()
        if len(paragrafo.split()) < minimo:
            continue
        pedacos = []
        for pedaco in _FIM_DE_FRASE.split(paragrafo):
            if pedacos and _ABREVIACOES.search(pedacos[-1]):
                pedacos[-1] = f"{pedacos[-1]} {pedaco}"
            else:
                pedacos.append(pedaco)
        frases += [f.strip() for f in pedacos if len(f.split()) >= minimo]
    return frases


def _texto_limpo(markdown: str) -> str:
    """Markdown do artigo sem links, HTML e lista de referencias."""
    return _MARCAS.sub(_ancora, _corpo(markdown))


_LINK = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")


def fontes_das_frases(markdown: str) -> list[tuple[str, str]]:
    """[(frase limpa, rotulo)] das frases com citacao: o texto do link que o
    artigo pos nelas ("Silva et al., 2021"). Algoritmo, sem modelo."""
    saida = []
    for paragrafo in _corpo(markdown).split("\n"):
        paragrafo = paragrafo.strip().lstrip("#-* ").strip()
        for frase in _FIM_DE_FRASE.split(paragrafo):
            rotulos = [m.group(1).strip() for m in _LINK.finditer(frase)]
            if rotulos:
                limpa = _MARCAS.sub(_ancora, frase).strip()
                saida.append((limpa, "; ".join(dict.fromkeys(rotulos))))
    return saida


@dataclass
class ArtigoParaRedes:
    id: str
    titulo: str
    url: str
    resumo: str
    palavra_chave: str
    secoes: list[tuple[str, str]] = field(default_factory=list)
    frases: list[str] = field(default_factory=list)
    texto: str = ""
    publicado_em: object = None
    remote_id: str = ""
    capa: str = ""  # caminho no storage
    capa_url: str = ""  # URL publica
    # [(frase limpa, "Autor, ano")]: a citacao que o artigo pos naquela frase
    # (o texto do link). E de onde a lamina com numero tira a fonte.
    fontes_das_frases: list = field(default_factory=list)


def artigo(artigo_id) -> ArtigoParaRedes | None:
    from apps.content.capas import capa_escolhida, url_publica_da_capa
    from apps.content.models import Article

    obj = Article.objects.filter(pk=artigo_id).first()
    if obj is None:
        return None
    secoes = [
        (s.heading, _texto_limpo(s.body_markdown))
        for s in obj.sections.all()
        if (s.body_markdown or "").strip()
    ]
    texto = _texto_limpo(obj.body_markdown)
    frases = list(dict.fromkeys(dividir_em_frases(texto)))  # o artigo repete frase as vezes
    capa = capa_escolhida(obj)
    return ArtigoParaRedes(
        id=str(obj.pk),
        titulo=obj.title,
        url=obj.published_url,
        resumo=obj.meta_description or obj.excerpt,
        palavra_chave=obj.focus_keyword,
        secoes=secoes,
        frases=frases,
        texto=texto,
        publicado_em=obj.published_at,
        remote_id=obj.remote_id,
        capa=capa.image.name if capa and capa.image else "",
        capa_url=url_publica_da_capa(capa) if capa else "",
        fontes_das_frases=fontes_das_frases(obj.body_markdown),
    )


def artigos_no_ar(desde=None) -> list[str]:
    """Ids dos artigos publicados (a versao que esta no ar), mais novos primeiro."""
    from apps.content.models import Article

    consulta = Article.objects.filter(status=Article.Status.PUBLISHED).exclude(published_url="")
    if desde is not None:
        consulta = consulta.filter(published_at__gte=desde)
    return [str(pk) for pk in consulta.order_by("-published_at").values_list("pk", flat=True)]


PAISES = {2076: "Brasil", 2620: "Portugal", 2840: "Estados Unidos"}


def _nome_do_pais(codigo: int) -> str:
    """O pais do Radar pelo nome (a lista de locais guardada, ou os mais comuns)."""
    from apps.radar.models import LocalDisponivel

    local = LocalDisponivel.objects.filter(codigo=codigo).first()
    return (local.nome if local else PAISES.get(codigo, "")) or "o pais inteiro"


def negocio() -> dict:
    """Tema, publico, oferta, dores e regioes, como o Negocio e o Radar guardam."""
    from apps.editorial.models import PerfilDoNegocio
    from apps.radar.models import ConfiguracaoDoRadar

    perfil = PerfilDoNegocio.objects.first()
    radar = ConfiguracaoDoRadar.carregar()
    regioes = []
    for regiao in radar.regioes or []:
        nome = str(regiao.get("nome") or "").split(",")[0].strip()
        if nome:
            regioes.append(nome)
    # O que a pessoa escreveu no Negocio vale mais que as regioes do Radar (que
    # dizem de onde vem o volume de busca, nao como o cliente chama o lugar).
    onde = (getattr(perfil, "onde_atende", "") or "").strip()
    local = onde or ", ".join(regioes[:3])
    if onde and onde not in regioes:
        regioes.insert(0, onde)
    if not regioes:  # nenhuma regiao no Radar: o pais inteiro
        regioes = [_nome_do_pais(radar.codigo_de_local)]
    return {
        "tema": getattr(perfil, "tema", "") or "",
        "publico": getattr(perfil, "publico", "") or "",
        "oferta": getattr(perfil, "oferta", "") or "",
        "dores": radar.lista_de_dores,
        "regioes": regioes,
        # Vazio quando o negocio atende o pais inteiro: o post nao fala de lugar.
        "local": local,
        "idioma": getattr(site(), "content_language", "") or "pt-BR",
    }


def site():
    from apps.integrations.models import Site

    return Site.objects.first()


def sinais(artigo: ArtigoParaRedes) -> dict:
    """O que o resto do PubliBot ja sabe do artigo: quase na primeira pagina do
    Google (sugestao aberta do Radar) e conversoes no site (90 dias)."""
    from apps.integrations.models import ConversaoDoSite
    from apps.radar.models import SugestaoDeAtualizacao

    quase_la = SugestaoDeAtualizacao.objects.filter(
        url=artigo.url, tipo="quase_la", situacao="aberta"
    ).exists()
    conversoes = 0
    if artigo.remote_id:
        desde = timezone.localdate() - timedelta(days=90)
        conversoes = ConversaoDoSite.objects.filter(
            dia__gte=desde, jornada__contains=[{"remote_id": artigo.remote_id}]
        ).count()
    return {"quase_la": quase_la, "conversoes": conversoes}


def demanda(limite: int = 300) -> list[dict]:
    """Os grupos de demanda do Radar (o que o publico busca), com volume e o
    vetor que o Radar ja calculou: [{"rotulo", "volume", "vetor"}]."""
    from apps.radar.models import GrupoDeDemanda

    grupos = (
        GrupoDeDemanda.objects.exclude(centroide__isnull=True)
        .exclude(situacao="descartado")
        .order_by("-volume_total")[:limite]
    )
    return [
        {"rotulo": g.rotulo, "volume": g.volume_total, "vetor": list(g.centroide)} for g in grupos
    ]


def o_que_procuram(limite: int = 15) -> dict:
    """O que o publico busca no Google, para os pedidos a outra IA:
    {"grupos": [(rotulo, volume)], "perguntas": [texto], "consultas": [(consulta,
    impressoes)]} — grupos de demanda do Radar (por volume), perguntas reais
    ("as pessoas tambem perguntam", visitantes, YouTube) e as buscas que ja
    trazem gente ao site (ultima coleta do Search Console)."""
    from django.db.models import Sum

    from apps.radar.models import ColetaDoConsole, GrupoDeDemanda, SinalDeDemanda

    grupos = (
        GrupoDeDemanda.objects.exclude(situacao="descartado")
        .exclude(avaliacao_ia="ruim")
        .filter(volume_total__gt=0)
        .order_by("-volume_total")[:limite]
    )
    perguntas = (
        SinalDeDemanda.objects.filter(situacao="ativo", fonte__in=["paa", "visitante", "youtube"])
        .order_by("-volume", "-criado_em")
        .values_list("texto", flat=True)[: limite * 3]
    )
    coleta = ColetaDoConsole.objects.order_by("-coletada_em").first()
    consultas = (
        list(
            coleta.linhas_set.values("consulta")
            .annotate(n=Sum("impressoes"))
            .order_by("-n")
            .values_list("consulta", "n")[:limite]
        )
        if coleta
        else []
    )
    vistas, unicas = set(), []
    for texto in perguntas:
        chave = texto.strip().lower()
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(texto.strip())
    return {
        "grupos": [(g.rotulo, g.volume_total) for g in grupos],
        "perguntas": unicas[:limite],
        "consultas": consultas,
    }


def endereco_do_site() -> str:
    """A pagina inicial do site do cliente (destino de post sem artigo)."""
    alvo = site()
    return (getattr(alvo, "base_url", "") or "").strip()


def conversoes_pelas_redes(remote_id: str, desde) -> int:
    """Conversoes que entraram (ou voltaram) por rede social e passaram pelo
    artigo, desde a data: o que o post daquele artigo provavelmente trouxe."""
    from django.db.models import Q

    from apps.integrations.models import ConversaoDoSite

    if not remote_id:
        return 0
    return (
        ConversaoDoSite.objects.filter(dia__gte=desde, jornada__contains=[{"remote_id": remote_id}])
        .filter(Q(canal_de_entrada="social") | Q(canal_final="social"))
        .count()
    )


# A fonte (utm_source, ou quem mandou) com que a visita de cada rede chega ao
# site. O link do post leva utm_source=<rede>; sem ele (o anuncio, o link na
# bio aberto fora do PubliBot), o site reconhece a rede por quem mandou.
FONTES_DA_REDE = {
    "instagram": ("instagram", "ig"),
    "linkedin": ("linkedin",),
    "gmn": ("gmn",),
}


def leitura_vinda_da_rede(remote_id: str, rede: str, desde) -> dict:
    """Quem veio desta rede ao artigo, desde a data, contado pelo site:
    aberturas, quantos leram de verdade (10 s+), o tempo ativo medio, quantos
    passaram da metade e quantos chegaram ao fim. Vazio sem dado (site antigo)."""
    from django.db.models import Sum

    from apps.integrations.models import LeituraPorOrigem

    if not remote_id:
        return {}
    soma = LeituraPorOrigem.objects.filter(
        remote_id=remote_id, dia__gte=desde, fonte__in=FONTES_DA_REDE.get(rede, (rede,))
    ).aggregate(
        aberturas=Sum("views"),
        leram=Sum("engaged_views"),
        segundos=Sum("engaged_seconds"),
        metade=Sum("half_read"),
        fim=Sum("read_to_end"),
    )
    if not soma["aberturas"]:
        return {}
    return {
        "aberturas": soma["aberturas"],
        "leram": soma["leram"] or 0,
        "segundos_por_leitura": round((soma["segundos"] or 0) / soma["aberturas"]),
        "metade": soma["metade"] or 0,
        "fim": soma["fim"] or 0,
    }


# -- Modelo de linguagem e vetores -------------------------------------------------
def _conexao_externa(carga: str):
    """A conexao ativa desta carga (outra IA, voz) que vale para este cliente."""
    from apps.content.inference import _tenant_atual
    from apps.inference.leases import escolher_conexao

    return escolher_conexao(workload=carga, tenant=_tenant_atual(), model_name="")


def outra_ia_por_api() -> str:
    """O nome da conexao da "outra IA" por API ("" sem nenhuma)."""
    from apps.inference.models import InferenceConnection

    conexao = _conexao_externa(InferenceConnection.Workload.OUTRA_IA)
    return f"{conexao.name} ({conexao.default_model})" if conexao else ""


AVISO_DA_API = (
    "\n\n(Voce esta sendo chamado por API, sem conversa: nao pergunte nada antes; gere "
    "agora os blocos pedidos, no formato exato, e termine com FIM.)"
)


def perguntar_a_outra_ia(pedido: str) -> str:
    """Manda o pedido (o mesmo de copiar e colar) para a outra IA por API e
    devolve a resposta, para os mesmos leitores da resposta colada."""
    from apps.inference.leases import gerar_owner_key, registrar_falha, registrar_sucesso, reserva
    from apps.inference.models import InferenceConnection
    from apps.inference.providers.base import ProviderPermanentError, get_provider

    conexao = _conexao_externa(InferenceConnection.Workload.OUTRA_IA)
    if conexao is None:
        raise ProviderPermanentError(
            "nenhuma outra IA por API cadastrada (manage.py configurar_api anthropic --chave ...)."
        )
    try:
        with reserva(conexao, owner_key=gerar_owner_key(), model_name=conexao.default_model):
            resposta = get_provider(conexao, timeout=600).chat(
                model=conexao.default_model,
                system="Voce e um estrategista de conteudo. Responda em portugues do Brasil.",
                user=pedido + AVISO_DA_API,
                temperature=0.7,
                max_tokens=8000,
            )
    except Exception:
        registrar_falha(conexao)
        raise
    registrar_sucesso(conexao)
    return resposta.text


def _tem(carga: str) -> bool:
    from apps.inference.models import InferenceConnection

    return any(c.atende(carga) for c in InferenceConnection.objects.filter(is_active=True))


def tem_voz() -> bool:
    from apps.inference.models import InferenceConnection

    return _tem(InferenceConnection.Workload.VOZ)


def tem_animacao() -> bool:
    from apps.inference.models import InferenceConnection

    return _tem(InferenceConnection.Workload.ANIMACAO)


def animacao_e_da_sora() -> bool:
    """A conexao de animacao e a Sora (quadro 9:16 obrigatorio) ou outra (o
    worker aceita a imagem como ela e)?"""
    from apps.inference.models import InferenceConnection

    conexao = _conexao_externa(InferenceConnection.Workload.ANIMACAO)
    return bool(conexao and "sora" in (conexao.default_model or "").lower())


def _animacao(*, para_pedir: bool = True):
    """A conexao de animacao. Para PEDIR, a escolhida pela vaga (a maquina pode
    estar ocupada com outra coisa, ou com falhas recentes: ai e "tente daqui a
    pouco", e nao "nao existe"). Para conferir ou baixar um trabalho ja pedido,
    qualquer conexao de animacao ativa serve: nao ocupa a placa."""
    from django.db.models import Q

    from apps.content.inference import _tenant_atual
    from apps.inference.models import InferenceConnection
    from apps.inference.providers.base import ProviderPermanentError, ProviderTransientError

    carga = InferenceConnection.Workload.ANIMACAO
    conexao = _conexao_externa(carga) if para_pedir else None
    if conexao is not None:
        return conexao
    tenant = _tenant_atual()
    cadastradas = sorted(
        (
            c
            for c in InferenceConnection.objects.filter(is_active=True).filter(
                Q(tenant=tenant) | Q(tenant__isnull=True)
            )
            if c.atende(carga)
        ),
        key=lambda c: c.tenant_id is None,  # a do cliente primeiro
    )
    if not cadastradas:
        raise ProviderPermanentError(
            "nenhuma animacao cadastrada (manage.py configurar_api worker_animacao, ou "
            "openai_video --chave ...)."
        )
    if para_pedir:
        raise ProviderTransientError(
            "a maquina da animacao esta ocupada (ou com falhas recentes): tenta de novo "
            "em alguns minutos"
        )
    return cadastradas[0]


def pedir_animacao(
    imagem: bytes,
    prompt: str,
    *,
    segundos: int,
    tamanho: str,
    seed: int | None = None,
    maior: bool = False,
) -> dict:
    """Pede a animacao da imagem e devolve o trabalho ({"id", "seed", "model"}:
    a seed e o modelo usados, quando o servidor diz), sem esperar: quem pediu
    volta para conferir (`situacao_da_animacao`). Assim nada fica preso esperando
    a placa, e desligar o worker no meio perde so este trabalho. No worker, o
    modelo e o ANIMACAO_MODELO_MENOR (ou o MAIOR, com `maior`)."""
    from django.conf import settings

    from apps.inference.leases import gerar_owner_key, registrar_falha, reserva
    from apps.inference.providers.animacao import get_animation_provider
    from apps.inference.providers.base import ProviderTransientError

    conexao = _animacao()
    sora = "sora" in (conexao.default_model or "").lower()
    modelo = conexao.default_model
    if not sora:
        modelo = settings.ANIMACAO_MODELO_MAIOR if maior else settings.ANIMACAO_MODELO_MENOR
    try:
        with reserva(conexao, owner_key=gerar_owner_key(), model_name=modelo):
            trabalho = get_animation_provider(conexao).criar_trabalho(
                imagem=imagem,
                prompt=prompt,
                modelo=modelo,
                segundos=segundos,
                tamanho=tamanho,
                seed=None if sora else seed,
            )
            return {**trabalho, "model": trabalho.get("model") or modelo}
    except ProviderTransientError:
        raise  # placa ocupada nao e falha da conexao (contrato do worker)
    except Exception:
        registrar_falha(conexao)
        raise


def situacao_da_animacao(identificador: str) -> dict:
    """{"status": queued | in_progress | completed | failed | sumiu, ...}."""
    from apps.inference.providers.animacao import get_animation_provider

    return get_animation_provider(_animacao(para_pedir=False)).situacao(identificador)


def baixar_animacao(identificador: str) -> bytes:
    from apps.inference.leases import registrar_sucesso
    from apps.inference.providers.animacao import get_animation_provider

    conexao = _animacao(para_pedir=False)
    clipe = get_animation_provider(conexao).baixar(identificador)
    registrar_sucesso(conexao)
    return clipe


# A voz do Kokoro (o worker) tem o idioma e o genero no nome: "pm_alex" (pt,
# masculina), "af_bella". A da ElevenLabs e um id de 20 letras; a da OpenAI, um
# nome ("nova"); a do Google, "pt-BR-Neural2-B".
_VOZ_DO_KOKORO = re.compile(r"^[a-z][fm]_[a-z]+$")
_VOZ_DA_ELEVENLABS = re.compile(r"^[A-Za-z0-9]{20}$")
_VOZ_DO_GOOGLE = re.compile(r"^[a-z]{2}-[A-Z]{2}-")


def _servico_da_voz(voz: str, conexao) -> bool:
    """A voz tem cara de ser deste servico (pelo formato do id)?"""
    from apps.inference.models import InferenceConnection

    tipo = InferenceConnection.Kind
    if _VOZ_DO_KOKORO.match(voz):
        return "kokoro" in (conexao.default_model or "").lower()
    if _VOZ_DA_ELEVENLABS.match(voz):
        return conexao.kind == tipo.ELEVENLABS
    if _VOZ_DO_GOOGLE.match(voz):
        return conexao.kind == tipo.GOOGLE_TTS
    return False


def _conexoes_de_voz(voz: str) -> list:
    """As conexoes de voz que valem para este cliente, a mais provavel de ter a
    voz primeiro: a do servico que o formato do id indica (Kokoro, ElevenLabs,
    Google), depois a escolhida pela vaga."""
    from django.db.models import Q

    from apps.content.inference import _tenant_atual
    from apps.inference.models import InferenceConnection

    carga = InferenceConnection.Workload.VOZ
    escolhida = _conexao_externa(carga)
    tenant = _tenant_atual()
    todas = [
        c
        for c in InferenceConnection.objects.filter(is_active=True).filter(
            Q(tenant=tenant) | Q(tenant__isnull=True)
        )
        if c.atende(carga)
    ]

    def ordem(c) -> tuple:
        return (not _servico_da_voz(voz or "", c), c != escolhida, c.tenant_id is None)

    return sorted(todas, key=ordem)


def falar(texto: str, voz: str = "") -> bytes:
    """O audio (MP3) do texto, pela conexao de voz (ElevenLabs, OpenAI, Google,
    o Kokoro do worker). Com mais de uma, vai na que tem a voz: a que recusa
    (voz que ela nao conhece) passa para a proxima."""
    from apps.inference.leases import gerar_owner_key, registrar_falha, registrar_sucesso, reserva
    from apps.inference.providers.base import ProviderPermanentError
    from apps.inference.providers.voz import get_voice_provider

    conexoes = _conexoes_de_voz(voz)
    if not conexoes:
        raise ProviderPermanentError(
            "nenhuma voz cadastrada (manage.py configurar_api elevenlabs --chave ...)."
        )
    recusas = []
    for conexao in conexoes:
        try:
            with reserva(conexao, owner_key=gerar_owner_key(), model_name=conexao.default_model):
                audio = get_voice_provider(conexao).falar(
                    texto=texto, voz=voz, modelo=conexao.default_model
                )
        except ProviderPermanentError as exc:
            recusas.append(f"{conexao.name}: {exc}")
            continue  # a voz e de outro servico: tenta o proximo
        except Exception:
            registrar_falha(conexao)
            raise
        registrar_sucesso(conexao)
        return audio
    raise ProviderPermanentError(
        f"nenhuma conexao de voz aceitou a voz {voz!r}: " + " | ".join(recusas)
    )


def ha_modelo_de_imagem() -> bool:
    from apps.content.capas import ha_conexao_de_imagem

    return ha_conexao_de_imagem()


def imagem_indisponivel() -> tuple[type[Exception], ...]:
    """As excecoes de "gerador de imagem sem vaga agora" (tentar mais tarde)."""
    from apps.content.capas import GeradorDeImagemOcupado
    from apps.inference.providers.base import ProviderTransientError

    return (GeradorDeImagemOcupado, ProviderTransientError)


def gerar_imagem(prompt: str, *, tamanho: str = "896x1152") -> bytes:
    """Uma imagem do gerador do PubliBot (o mesmo das capas), sem texto."""
    return gerar_imagens(prompt, quantidade=1, tamanho=tamanho)[0]


def gerar_quadro(
    prompt: str,
    *,
    tamanho: str,
    referencias: list[bytes] | tuple = (),
    base: bytes | None = None,
    mascara: bytes | None = None,
    forca: float = 0.6,
    negativo: str = "",
) -> tuple[bytes, str]:
    """(imagem, aviso) da imagem inicial de uma cena: com as referencias dos
    personagens e a foto real mantida pela mascara, quando o gerador tem a rota
    de edicao; sem ela (worker de versao anterior), so pelo texto, e o aviso
    diz o que ficou de fora."""
    from apps.content.capas import editar_imagem
    from apps.inference.providers.base import RotaInexistente

    if referencias or base is not None:
        try:
            imagem, avisos = editar_imagem(
                prompt,
                tamanho=tamanho,
                base=base,
                mascara=mascara,
                referencias=referencias,
                forca=forca,
                negativo=negativo,
            )
            return imagem, "; ".join(avisos)
        except RotaInexistente:
            aviso = (
                "o gerador de imagem ainda nao aceita referencia nem foto na cena (worker "
                "sem /images/edits): a imagem saiu so pela descricao"
            )
            return gerar_imagem(prompt, tamanho=tamanho), aviso
    return gerar_imagem(prompt, tamanho=tamanho), ""


def gerar_imagens(prompt: str, *, quantidade: int, tamanho: str = "896x1152") -> list[bytes]:
    """Varias opcoes da mesma cena (para escolher a melhor), sem texto."""
    from apps.content.capas import gerar_imagens as gerar

    geradas = gerar(
        prompt,
        quantidade=quantidade,
        tamanho=tamanho,
        negativo="text, letters, words, watermark, logo",
    )
    return [g.conteudo for g in geradas]


def reabrir_modelo() -> list[tuple[str, bool, str]]:
    """Testa as conexoes de modelo com o disjuntor aberto e fecha o das que
    respondem (ver `leases.testar_e_reabrir`)."""
    from apps.inference.leases import testar_e_reabrir

    return testar_e_reabrir()


def modelo_indisponivel() -> tuple[type[Exception], ...]:
    """As excecoes de "sem modelo agora" (nao configurado, ou placa ocupada)."""
    from apps.content.inference import SemModeloConfigurado
    from apps.ops.orchestrator import PassoAdiado

    return (SemModeloConfigurado, PassoAdiado, LookupError)


def executar(
    chave: str,
    variaveis: dict,
    *,
    json_schema: dict | None = None,
    imagens: list[tuple[str, bytes]] | None = None,
) -> str:
    """`imagens`: [(tipo, bytes)] para modelo que enxerga (descrever uma foto)."""
    from apps.content.inference import executar_prompt

    return executar_prompt(
        key=chave,
        variaveis=variaveis,
        site=site(),
        json_schema=json_schema,
        com_convite=False,
        imagens=imagens,
    ).texto


class TranscricaoAdiada(Exception):
    """O worker da placa esta ocupado (ou desligado): tentar mais tarde."""


class TranscricaoImpossivel(Exception):
    """Sem worker de transcricao, ou o audio nao serviu."""


def transcrever(nome: str, conteudo: bytes, *, dono: str) -> str:
    """Audio -> texto, pelo mesmo worker (Whisper) que transcreve o acervo."""
    from apps.knowledge.extraction import (
        ConversorOcupado,
        ExtracaoIndisponivel,
        transcrever_audio,
    )

    idioma = (getattr(site(), "content_language", "") or "pt")[:2]
    try:
        segmentos, _duracao = transcrever_audio(
            nome, conteudo, idioma=idioma, dono=dono, timeout=600.0
        )
    except ConversorOcupado as exc:
        raise TranscricaoAdiada(str(exc)) from exc
    except ExtracaoIndisponivel as exc:
        raise TranscricaoImpossivel(str(exc)) from exc
    return " ".join(texto.strip() for _inicio, texto in segmentos).strip()


class LinkIlegivel(Exception):
    """A pagina nao abriu, nao e publica ou nao tem texto aproveitavel."""


def ler_link(url: str) -> dict:
    """O texto principal de uma pagina de terceiros (noticia, estudo), com
    titulo e nome do site. Mesma busca protegida do acervo (so endereco
    publico, redirecionamentos conferidos)."""
    from apps.knowledge.web import PaginaIndisponivel, baixar, extrair_pagina

    try:
        conteudo, final, tipo = baixar(url)
        if "pdf" in (tipo or "").lower():
            raise LinkIlegivel("e um PDF: cole o trecho que importa no texto.")
        pagina = extrair_pagina(conteudo, url=final)
    except PaginaIndisponivel as exc:
        raise LinkIlegivel(str(exc)) from exc
    texto = _texto_limpo(pagina.markdown)
    if len(texto.split()) < 80:
        raise LinkIlegivel(
            "quase sem texto (pagina com login, paywall ou feita so de imagem): cole o "
            "trecho que importa no texto."
        )
    return {
        "url": final,
        "titulo": (pagina.titulo or "")[:300],
        "site": (pagina.site or "")[:120],
        "data": pagina.data.isoformat() if pagina.data else "",
        "texto": texto[:12000],
    }


def registrar_fala(texto: str, rede: str, *, certo: bool = False) -> None:
    """Um comentario da rede que diz dor vai para as Dores do Radar (ver
    radar/dores.py). `certo`: a classificacao do comentario ja disse que e
    reclamacao ou relato. Falha aqui nunca derruba a leitura dos comentarios."""
    from apps.radar import dores
    from apps.radar.models import FalaDoPublico

    fonte = rede if rede in FalaDoPublico.Fonte.values else FalaDoPublico.Fonte.MANUAL
    try:
        dores.registrar(texto, fonte, tipo=FalaDoPublico.Tipo.DESABAFO if certo else "", rede=rede)
    except Exception:
        logger.exception("Fala do comentario nao registrada.")


def dores_para(texto: str, *, rede: str = "", quantas: int = 3) -> list[str]:
    """As dores ligadas ao texto, com as falas do publico (as da rede primeiro),
    prontas para o prompt. Sem nada colhido ainda: as dores digitadas."""
    from apps.radar import dores

    return dores.em_linhas(dores.dores_para(texto, rede=rede, quantas=quantas))


def vetores(textos: list[str], *, consulta: bool = False) -> list[list[float]]:
    from apps.knowledge.embeddings import get_embedding_client

    cliente = get_embedding_client()
    if consulta:
        return [cliente.embed_query(t) for t in textos]
    return cliente.embed_passage(textos)


def termos_a_evitar(texto: str) -> list[str]:
    """Termos proibidos do guia editorial que aparecem no texto."""
    from apps.editorial.services import conferir_texto, perfil_atual

    conferencia = conferir_texto(texto, perfil_atual())
    return [
        f'"{a.expressao}"' + (f' (use "{a.sugestao}")' if a.sugestao else "")
        for a in conferencia.proibidos
    ]


def cuidados_de_linguagem(texto: str) -> list[str]:
    """Linguagem que estigmatiza e temas sensiveis sem o cuidado (aviso)."""
    from apps.editorial.linguagem import cuidados

    return [
        f'"{a.expressao}"' + (f' (prefira "{a.sugestao}")' if a.sugestao else "") + f": {a.motivo}"
        for a in cuidados(texto)
    ]


def instrucoes_sensiveis(texto: str) -> str:
    """As instrucoes dos temas sensiveis presentes no material."""
    from apps.editorial.linguagem import INSTRUCAO_GERAL, instrucoes_sensiveis

    return "\n".join(x for x in [INSTRUCAO_GERAL.lstrip("- "), instrucoes_sensiveis(texto)] if x)


# -- Perguntas ------------------------------------------------------------------------
def criar_pergunta(texto: str, referencia: str) -> str | None:
    """Uma pergunta vinda de comentario vira Pergunta do PubliBot (a mesma fila
    das perguntas do site). Sem nome de quem perguntou. Devolve o id."""
    from apps.content.models import Question

    alvo = site()
    if alvo is None:
        return None
    pergunta, _ = Question.objects.get_or_create(
        site=alvo,
        remote_id=referencia[:120],
        defaults={
            "question_text": texto[:500],
            "submitted_at": timezone.now(),
            "retention_until": timezone.now() + timedelta(days=90),
        },
    )
    return str(pergunta.pk)


def respostas(pergunta_ids: list[str]) -> dict[str, dict]:
    """As respostas aprovadas (ou publicadas) das perguntas: {id: {texto, url}}."""
    from apps.content.models import Answer

    prontas = Answer.objects.filter(
        question_id__in=pergunta_ids, status__in=["approved_scheduled", "published"]
    )
    return {
        str(r.question_id): {"texto": _texto_limpo(r.body_markdown), "url": r.published_url}
        for r in prontas
    }


# -- O artigo no PubliBot e no ar -------------------------------------------------------
def pagina_do_artigo(artigo_id) -> str:
    """A pagina do artigo DENTRO do PubliBot (revisar), que existe antes de ele ir
    ao ar: e para la que o link do post leva quem esta montando as redes."""
    from django.urls import reverse

    return reverse("content:revisar", args=[artigo_id]) if artigo_id else ""


def situacao_do_artigo(artigo_id) -> dict:
    """{"no_ar": bool, "agendado_para": datetime | None, "aprovado": bool}."""
    from apps.content.models import Article

    obj = Article.objects.filter(pk=artigo_id).only("status", "scheduled_for").first()
    if obj is None:
        return {"no_ar": False, "agendado_para": None, "aprovado": False}
    return {
        "no_ar": obj.status == Article.Status.PUBLISHED,
        "aprovado": obj.status == Article.Status.APPROVED_SCHEDULED,
        "agendado_para": obj.scheduled_for
        if obj.status == Article.Status.APPROVED_SCHEDULED
        else None,
    }


def adiantar_artigo(artigo_id) -> str:
    """O post do artigo chegou na hora e o artigo ainda nao esta no ar: o
    artigo fura a fila e sai agora (o horario dele na cadencia fica livre para o
    proximo). "no_ar", "adiantado", "sem_aprovar" (nao da para publicar algo
    que ninguem aprovou: o post espera) ou "sem_artigo" (foi apagado: o post
    segue com o link que tem)."""
    from django.utils import timezone

    from apps.content.models import Article
    from apps.integrations.models import PublicationSlot
    from apps.integrations.tasks import publicar_vencidos

    obj = Article.objects.filter(pk=artigo_id).first()
    if obj is None:
        return "sem_artigo"
    if obj.status not in (
        Article.Status.PUBLISHED,
        Article.Status.APPROVED_SCHEDULED,
    ):
        return "sem_aprovar"
    if obj.status == Article.Status.PUBLISHED:
        return "no_ar"
    agora = timezone.now()
    if obj.scheduled_for is None or obj.scheduled_for > agora:
        obj.scheduled_for = agora
        obj.save(update_fields=["scheduled_for"])
        PublicationSlot.objects.filter(article=obj, slot_at__gt=agora).update(article=None)
    publicar_vencidos(limite=50)
    return "adiantado"


# -- Enderecos ------------------------------------------------------------------------
def endereco_publico(caminho: str) -> str:
    """URL absoluta no dominio do tenant (link rastreado, imagens, link na bio)."""
    from django.conf import settings
    from django.db import connection

    tenant = getattr(connection, "tenant", None)
    dominios = getattr(tenant, "domains", None)
    dominio = None
    if dominios is not None:
        dominio = dominios.filter(is_primary=True).first() or dominios.first()
    if dominio is None:
        return caminho
    return f"{settings.ESQUEMA_PUBLICO}://{dominio.domain}{caminho}"


def ideias_aprovadas() -> list[dict]:
    """As ideias aprovadas na caixa de ideias (outro modulo, pela extensao):
    [{"id", "titulo", "texto", "referencias"}]."""
    from apps.ops.extensoes import ideias_para_as_redes

    return ideias_para_as_redes()


LIMIAR_DO_ARTIGO_PARECIDO = 0.5


def artigo_mais_parecido(texto: str) -> tuple[str, str] | None:
    """(titulo, url) do artigo publicado mais perto do texto, se perto o
    bastante; o destino sugerido do link de um post sem artigo (a pessoa troca)."""
    import numpy as np

    publicados = [a for a in (artigo(pk) for pk in artigos_no_ar()[:200]) if a and a.url]
    if not publicados or not (texto or "").strip():
        return None
    try:
        vetor, *dos_artigos = vetores(
            [texto[:2000], *[f"{a.titulo}. {a.resumo}" for a in publicados]], consulta=True
        )
    except Exception:  # sem embedding agora: fica a pagina inicial
        return None
    v = np.asarray(vetor, dtype=float)
    melhor, nota = None, -1.0
    for item, outro in zip(publicados, dos_artigos, strict=True):
        o = np.asarray(outro, dtype=float)
        cos = float(v @ o / ((np.linalg.norm(v) * np.linalg.norm(o)) + 1e-9))
        if cos > nota:
            melhor, nota = item, cos
    if melhor is None or nota < LIMIAR_DO_ARTIGO_PARECIDO:
        return None
    return melhor.titulo, melhor.url


def destinos_do_link(limite: int = 50) -> list[tuple[str, str]]:
    """[(rotulo, url)] para onde o link de um post pode levar: a pagina inicial,
    a pagina da oferta (se cadastrada) e os artigos publicados."""
    from apps.social.models import ConfiguracaoSocial

    saida = []
    if inicio := endereco_do_site():
        saida.append(("Pagina inicial do site", inicio))
    if oferta := ConfiguracaoSocial.carregar().link_da_oferta:
        saida.append(("Pagina da oferta (landing page)", oferta))
    for pk in artigos_no_ar()[:limite]:
        a = artigo(pk)
        if a and a.url:
            saida.append((f"Artigo: {a.titulo}", a.url))
    return saida


# -- A tela de Operacao ---------------------------------------------------------------------
def __getattr__(nome: str):
    # `Conferindo` (apps.ops): o retry que so volta para conferir um trabalho
    # em curso fica "Executando" na Operacao, com o motivo.
    if nome == "Conferindo":
        from apps.ops.acompanhamento import Conferindo

        return Conferindo
    raise AttributeError(nome)


def na_fila(tarefa, *args, rotulo: str, alvo=None, **kwargs) -> str:
    """Despacha a tarefa e a mostra na tela de Operacao desde ja (Aguardando,
    Executando, Aguardando capacidade, Concluido, Falhou)."""
    from apps.ops.acompanhamento import na_fila as registrar

    return registrar(tarefa, *args, rotulo=rotulo, alvo=alvo, **kwargs)


def acompanhar(trabalho: str | None):
    """Dentro da tarefa: marca o andamento do registro (ver `na_fila`)."""
    from apps.ops.acompanhamento import acompanhar as marcar

    return marcar(trabalho)
