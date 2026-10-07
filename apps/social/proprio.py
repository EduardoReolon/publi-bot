"""Material proprio: o caso real, a novidade, as fotos do trabalho.

Nem todo post nasce de um artigo. Uma consultoria quer contar um caso; uma
barbearia vive de foto real. Aqui o "artigo" do post e a `Entrada` — o que a
pessoa mandou (texto, audio transcrito, notas e fotos) — e o resto do modulo
(abordagem com placar, conferencia, agenda, publicacao, medicao) segue igual.

A regra continua a de sempre: o post so afirma o que esta no material. O
numero que a pessoa nao escreveu e barrado; o detalhe que identifica alguem
(nome, idade, telefone...) vira aviso na revisao.

**Banco de fotos** — a pessoa sobe as fotos quando tiver (do celular, em
lote); o PubliBot descarta as ruins e as repetidas, junta as do mesmo
atendimento num carrossel e as agenda sozinho, na proporcao de cada conta
(`Destino.fotos_por_cento`). Entre 1% e 99%, a proporcao anda para o tipo de
post que funciona mais na conta (`ajustar_mistura`).
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import numpy as np
from django.db import connection, transaction
from django.utils import timezone

from apps.social import fontes, fotos, parametros
from apps.social.models import ConfiguracaoSocial, Destino, Entrada, Midia, Post

logger = logging.getLogger("publibot.social")

# Artigo relacionado escolhido sozinho so acima desta proximidade (cosseno).
PROXIMIDADE_DO_ARTIGO = 0.83
ARTIGOS_COMPARADOS = 80
JANELA_DA_MISTURA = timedelta(days=30)
MINIMO_JULGADOS = 5
DIFERENCA_DA_MISTURA = 0.20
PASSO_DA_MISTURA = 10
DIAS_DE_ESTOQUE = 7

_FRASE = re.compile(r"(?<=[.!?…])\s+")
IDENTIFICA = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "e-mail"),
    (re.compile(r"(?<!\w)@\w{3,}"), "@ de perfil"),
    (re.compile(r"\(?\d{2}\)?\s?9?\d{4}-?\d{4}"), "telefone"),
    (re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"), "CPF"),
    (re.compile(r"\b\d{1,3}\s*anos\b", re.I), "idade"),
    (
        re.compile(r"\b(?i:sr|sra|dr|dra|dona|seu|paciente|cliente)\.?\s+[A-ZÀ-Ú][a-zà-ú]{2,}"),
        "nome",
    ),
]


# -- Conferencias --------------------------------------------------------------------
def identificacao(texto: str) -> list[str]:
    """O que pode identificar uma pessoa no texto (vira aviso na revisao)."""
    achados = []
    for padrao, rotulo in IDENTIFICA:
        trecho = padrao.search(texto or "")
        if trecho:
            achados.append(f'{rotulo} ("{trecho.group(0).strip()}")')
    return achados


# -- A Entrada como "artigo" do post -----------------------------------------------------
def como_artigo(entrada: Entrada) -> fontes.ArtigoParaRedes:
    """O material da Entrada no formato que a redacao ja entende. Numero do post
    vale se estiver no relato ou no artigo relacionado."""
    relato = entrada.relato
    relacionado = fontes.artigo(entrada.artigo_id) if entrada.artigo_id else None
    frases = []
    for paragrafo in relato.splitlines():
        frases += [f.strip() for f in _FRASE.split(paragrafo) if len(f.split()) >= 3]
    url = (
        relacionado.url
        if relacionado and relacionado.url
        else (entrada.link or fontes.endereco_do_site())
    )
    return fontes.ArtigoParaRedes(
        id=None,
        titulo=entrada.titulo,
        url=url,
        resumo=relato[:400],
        palavra_chave="",
        secoes=[],
        frases=frases,
        texto="\n".join(
            x
            for x in [relato, relacionado.texto if relacionado else "", _referencias(entrada)]
            if x
        ),
        capa=relacionado.capa if relacionado else "",
        capa_url=relacionado.capa_url if relacionado else "",
    )


def _referencias(entrada: Entrada) -> str:
    """As paginas de terceiros lidas, com a origem de cada uma."""
    blocos = []
    for i, ref in enumerate(r for r in entrada.referencias or [] if r.get("texto")):
        origem = " — ".join(x for x in [ref.get("site"), ref.get("titulo")] if x) or ref["url"]
        if ref.get("frente"):
            origem = f"{origem}; frente '{ref['frente']}'"
        if ref.get("papel") == "discurso":
            # O "o que se diz" (caixa de ideias): citado e analisado, nunca prova.
            rotulo = "DISCURSO, NAO E EVIDENCIA: cite como o que se diz, sem tirar conclusao dele"
            blocos.append(
                f"REFERENCIA {i + 1} — {rotulo} ({origem}; {ref['url']}):\n{ref['texto']}"
            )
        else:
            blocos.append(f"REFERENCIA {i + 1} ({origem}; {ref['url']}):\n{ref['texto']}")
    return "\n\n".join(blocos)


def instrucao_para_quem_escreve(entrada: Entrada, artigo: fontes.ArtigoParaRedes) -> str:
    """O que muda no pedido ao modelo quando o post nao vem de artigo."""
    o_que = {
        Entrada.Tipo.CASO: "um CASO REAL do negocio, contado pela propria pessoa",
        Entrada.Tipo.NOVIDADE: "uma NOVIDADE ou bastidor do negocio",
        Entrada.Tipo.COMENTARIO: (
            "um COMENTARIO do negocio sobre material de terceiros (noticia, estudo, "
            "publicacao) que esta nas REFERENCIAS do MATERIAL"
        ),
        Entrada.Tipo.FOTOS: "FOTOS do trabalho do negocio (o post acompanha as fotos)",
    }[entrada.tipo]
    partes = [
        f"ESTE POST NAO E DE UM ARTIGO: e {o_que}. O MATERIAL e o relato dela; conte "
        "isso no formato da rede, sem acrescentar fato, numero, nome ou resultado que nao "
        "esteja no relato."
    ]
    if entrada.tipo == Entrada.Tipo.CASO:
        partes.append(
            "Nao identifique ninguem (nome, idade, empresa, cidade pequena): diga 'um cliente', "
            "'uma paciente'."
        )
    if any(r.get("texto") for r in entrada.referencias or []):
        partes.append(
            "Diga de onde vem cada informacao das REFERENCIAS (nome do veiculo, da "
            "instituicao ou dos autores). Nao atribua a uma fonte o que ela nao disse, nao "
            "aumente nem diminua o que ela diz, e separe o que a fonte afirma do que e "
            "a leitura do negocio (o relato da pessoa)."
        )
    if any(r.get("papel") == "discurso" for r in entrada.referencias or []):
        partes.append(
            "Ha REFERENCIAS marcadas como DISCURSO: sao o 'o que se diz' em debate. Cite-as "
            "como opiniao a analisar ('segundo a materia X'), nunca como prova; a conclusao "
            "sai das outras referencias. Critique a afirmacao, nunca as pessoas."
        )
    if entrada.midias.exists():
        partes.append(
            "As imagens sao as fotos reais: nao descreva o que nao esta no relato e nao "
            "escreva laminas (deixe 'laminas' vazio)."
        )
    if not entrada.relato.strip():
        partes.append(
            "Nao ha texto: escreva uma legenda curta sobre o trabalho do negocio, sem "
            "detalhes que as fotos podem nao mostrar."
        )
    if artigo.url:
        partes.append("No fim, convide para saber mais no link.")
    return " ".join(partes)


def artigo_relacionado(texto: str) -> str | None:
    """O artigo publicado mais proximo do texto, se for bem proximo."""
    if not texto.strip():
        return None
    ids = fontes.artigos_no_ar()[:ARTIGOS_COMPARADOS]
    artigos = [a for i in ids if (a := fontes.artigo(i)) is not None]
    if not artigos:
        return None
    try:
        alvo = np.asarray(fontes.vetores([texto], consulta=True)[0], dtype=float)
        vetores = np.asarray(fontes.vetores([f"{a.titulo}. {a.resumo}" for a in artigos]), float)
    except Exception as exc:
        logger.info("Artigo relacionado sem vetores: %s", exc)
        return None
    alvo /= np.linalg.norm(alvo) + 1e-9
    vetores /= np.linalg.norm(vetores, axis=1, keepdims=True) + 1e-9
    notas = vetores @ alvo
    melhor = int(np.argmax(notas))
    return artigos[melhor].id if notas[melhor] >= PROXIMIDADE_DO_ARTIGO else None


# -- Post proprio (caso, novidade) -------------------------------------------------------
class EntradaInvalida(ValueError):
    pass


def criar(
    *,
    tipo: str,
    texto: str,
    arquivos: list,
    audio=None,
    artigo: str = "auto",
    link: str = "",
    links: list[str] | None = None,
    autorizado: bool = False,
    destinos: list[str],
    por=None,
) -> Entrada:
    """Cria a Entrada e os posts (ou espera a leitura dos links e a transcricao
    do audio, nesta ordem, em segundo plano)."""
    texto = (texto or "").strip()
    links = _links(links or [])
    if not (texto or arquivos or audio or links):
        raise EntradaInvalida(
            "Escreva o que aconteceu, grave um audio, mande fotos ou cole um link."
        )
    if (arquivos or tipo == Entrada.Tipo.CASO) and not autorizado:
        raise EntradaInvalida(
            "Confirme que quem aparece ou e citado autorizou (ou que nao aparece ninguem)."
        )
    if not destinos:
        raise EntradaInvalida("Escolha pelo menos uma conta.")
    midias = [fotos.receber(a, banco=False, autorizada=True) for a in arquivos]
    artigo_id = None
    if artigo == "auto":
        artigo_id = artigo_relacionado(texto) if not link else None
    elif artigo:
        artigo_id = artigo
    entrada = Entrada.objects.create(
        tipo=tipo,
        texto=texto[:10000],
        artigo_id=artigo_id,
        link=link[:500],
        referencias=[{"url": u} for u in links],
        autorizado=autorizado,
        destinos=[str(d) for d in destinos],
        criada_por=por,
    )
    entrada.midias.set(midias)
    if audio is not None:
        from django.core.files.base import ContentFile

        entrada.audio.save(audio.name[-80:], ContentFile(audio.read()), save=False)
        entrada.situacao_do_audio = Entrada.Transcricao.ESPERANDO
        entrada.save(update_fields=["audio", "situacao_do_audio"])
    from apps.social.tasks import ler_links, transcrever_entrada

    schema = connection.schema_name
    if links:
        transaction.on_commit(lambda: ler_links.delay(schema, str(entrada.pk)))
    elif audio is not None:
        transaction.on_commit(lambda: transcrever_entrada.delay(schema, str(entrada.pk)))
    else:
        levar(entrada)
    return entrada


MAXIMO_DE_LINKS = 5


def _links(linhas: list[str]) -> list[str]:
    """Os enderecos colados (um por linha), sem repetir, ate `MAXIMO_DE_LINKS`."""
    saida: list[str] = []
    for linha in linhas:
        for pedaco in (linha or "").split():
            if not pedaco.startswith(("http://", "https://")):
                raise EntradaInvalida(f"Link invalido: {pedaco[:80]} (comece com https://).")
            if pedaco not in saida:
                saida.append(pedaco[:500])
    if len(saida) > MAXIMO_DE_LINKS:
        raise EntradaInvalida(f"No maximo {MAXIMO_DE_LINKS} links por post.")
    return saida


def ler_referencias(entrada: Entrada) -> None:
    """Le cada link (o que falhar fica com o motivo, para a revisao) e segue:
    transcricao do audio, se houver; senao, os posts."""
    lidas = []
    for ref in entrada.referencias or []:
        if ref.get("texto") or ref.get("erro"):
            lidas.append(ref)
            continue
        try:
            lidas.append(fontes.ler_link(ref["url"]))
        except fontes.LinkIlegivel as exc:
            lidas.append({"url": ref["url"], "erro": str(exc)[:300]})
    entrada.referencias = lidas
    entrada.save(update_fields=["referencias"])
    if entrada.situacao_do_audio == Entrada.Transcricao.ESPERANDO:
        from apps.social.tasks import transcrever_entrada

        transcrever_entrada.delay(connection.schema_name, str(entrada.pk))
        return
    if entrada.relato.strip() or entrada.midias.exists() or _referencias(entrada):
        levar(entrada)
    else:
        logger.warning("Entrada %s sem material: nenhum link abriu.", entrada.pk)


def criar_comentario(
    *, texto: str, referencias: list[dict], destinos: list[str], por=None, origem: str = ""
) -> Entrada:
    """Post de "noticia ou estudo comentado" com o material ja lido (vem da
    caixa de ideias, com o papel de cada referencia: discurso ou evidencia)."""
    if not destinos:
        raise EntradaInvalida("Escolha pelo menos uma conta.")
    if not referencias:
        raise EntradaInvalida("Nenhuma fonte aprovada ainda: faca a curadoria primeiro.")
    # O link leva ao artigo publicado mais parecido (a pessoa troca no post).
    parecido = fontes.artigo_mais_parecido(texto)
    entrada = Entrada.objects.create(
        tipo=Entrada.Tipo.COMENTARIO,
        texto=texto[:10000],
        referencias=referencias,
        destinos=[str(d) for d in destinos],
        criada_por=por,
        origem=origem[:80],
        link=parecido[1] if parecido else "",
    )
    levar(entrada)
    return entrada


def ideia_da_vez(destino: Destino) -> dict | None:
    """A ideia aprovada que ainda nao virou post nesta conta (a mais recente)."""
    for ideia in fontes.ideias_aprovadas():
        if not Post.objects.filter(
            destino=destino, entrada__origem=f"ideia:{ideia['id']}"
        ).exists():
            return ideia
    return None


def post_de_ideia(destino: Destino, ideia: dict) -> list[Post]:
    entrada = criar_comentario(
        texto=ideia["texto"],
        referencias=ideia["referencias"],
        destinos=[str(destino.pk)],
        origem=f"ideia:{ideia['id']}",
    )
    return list(Post.objects.filter(entrada=entrada))


def contas_para_comentar() -> list[tuple[str, str]]:
    destinos = list(Destino.objects.filter(ligado=True)) or list(Destino.objects.all())
    return [(str(d.pk), d.nome) for d in destinos]


def links_que_falharam(entrada: Entrada) -> list[str]:
    return [f"{r['url']}: {r['erro']}" for r in entrada.referencias or [] if r.get("erro")]


def levar(entrada: Entrada) -> list[Post]:
    """Um post da Entrada em cada conta escolhida."""
    from apps.social import escolha

    artigo = como_artigo(entrada)
    motivo = (
        Post.Motivo.FOTOS
        if entrada.tipo == Entrada.Tipo.FOTOS
        else Post.Motivo.IDEIA
        if entrada.origem.startswith("ideia:")
        else Post.Motivo.CASO
    )
    por_que = {
        Entrada.Tipo.CASO: "Caso real que voce mandou.",
        Entrada.Tipo.NOVIDADE: "Novidade que voce mandou.",
        Entrada.Tipo.COMENTARIO: "Ideia aprovada, com as fontes curadas."
        if entrada.origem.startswith("ideia:")
        else "Noticia ou estudo que voce mandou comentar.",
        Entrada.Tipo.FOTOS: f"Fotos do banco ({entrada.midias.count()}).",
    }[entrada.tipo]
    posts = []
    for destino in Destino.objects.filter(pk__in=entrada.destinos):
        posts += escolha.sugerir(destino, artigo, motivo, por_que, entrada=entrada, versoes=1)
    return posts


def transcrever(entrada: Entrada) -> None:
    """O audio vira texto (worker da placa) e os posts sao criados. Levanta
    fontes.TranscricaoAdiada quando a placa esta ocupada (a tarefa repete)."""
    with entrada.audio.open("rb") as arquivo:
        conteudo = arquivo.read()
    try:
        texto = fontes.transcrever(
            entrada.audio.name.rsplit("/", 1)[-1], conteudo, dono=f"social:{entrada.pk}"
        )
    except fontes.TranscricaoImpossivel as exc:
        entrada.situacao_do_audio = Entrada.Transcricao.FALHOU
        entrada.transcricao = ""
        entrada.save(update_fields=["situacao_do_audio", "transcricao"])
        logger.warning("Audio da entrada %s nao transcrito: %s", entrada.pk, exc)
        if entrada.texto.strip() or entrada.midias.exists() or _referencias(entrada):
            levar(entrada)  # o resto do material ainda serve
        return
    entrada.transcricao = texto[:20000]
    entrada.situacao_do_audio = Entrada.Transcricao.PRONTA
    if entrada.artigo_id is None and not entrada.link:
        entrada.artigo_id = artigo_relacionado(f"{entrada.texto}\n{texto}")
    entrada.save(update_fields=["transcricao", "situacao_do_audio", "artigo_id"])
    levar(entrada)


# -- Banco de fotos ----------------------------------------------------------------------
def receber_no_banco(arquivos: list, *, nota: str = "", autorizada: bool = False) -> dict:
    """Recebe um lote: grava, avalia, tira as quase iguais e agrupa."""
    config = ConfiguracaoSocial.carregar()
    minima = parametros.valor("nitidez_minima", config)
    brilho = (parametros.valor("brilho_minimo", config), parametros.valor("brilho_maximo", config))
    recebidas, erros = [], []
    for arquivo in arquivos:
        try:
            midia = fotos.receber(arquivo, banco=True, nota=nota, autorizada=autorizada)
        except fotos.MidiaInvalida as exc:
            erros.append(str(exc))
            continue
        recebidas.append(fotos.avaliar(midia, nitidez_minima=minima, brilho=brilho))
    repetidas = fotos.tirar_quase_iguais(recebidas)
    fotos.agrupar()
    if config.descrever_fotos and recebidas:
        from apps.social.tasks import descrever_fotos

        schema = connection.schema_name
        transaction.on_commit(lambda: descrever_fotos.delay(schema))
    ruins = sum(
        1
        for m in recebidas
        if m.situacao == Midia.Situacao.DESCARTADA and not m.motivo.startswith("quase")
    )
    return {"recebidas": len(recebidas), "ruins": ruins, "repetidas": repetidas, "erros": erros}


def grupos_disponiveis(destino: Destino) -> list[list[Midia]]:
    """Os atendimentos do banco que esta conta ainda nao postou, do mais antigo."""
    usados = set(
        Midia.objects.filter(entradas__posts__destino=destino)
        .exclude(grupo=None)
        .values_list("grupo", flat=True)
    )
    grupos: dict = {}
    for midia in (
        Midia.objects.filter(banco=True, grupo__isnull=False)
        .exclude(situacao=Midia.Situacao.DESCARTADA)
        .order_by("tirada_em", "criada_em")
    ):
        if midia.grupo not in usados:
            grupos.setdefault(midia.grupo, []).append(midia)
    return list(grupos.values())


def estoque(destino: Destino) -> dict:
    """Quantos atendimentos faltam postar e para quantos dias dao."""
    grupos = len(grupos_disponiveis(destino))
    por_semana = destino.teto_semanal * destino.fotos_por_cento / 100
    dias = round(grupos / por_semana * 7) if por_semana else None
    return {"grupos": grupos, "dias": dias, "baixo": dias is not None and dias < DIAS_DE_ESTOQUE}


def tipo_da_vez(destino: Destino) -> str:
    """'fotos' ou 'artigos': o que falta para a conta chegar na proporcao."""
    if destino.fotos_por_cento <= 0:
        return "artigos"
    if destino.fotos_por_cento >= 100:
        return "fotos"
    recentes = (
        destino.posts.filter(criado_em__gte=timezone.now() - JANELA_DA_MISTURA)
        .exclude(situacao=Post.Situacao.DESCARTADO)
        .exclude(motivo__in=[Post.Motivo.HISTORICO, Post.Motivo.CASO])
    )
    total = recentes.count()
    de_fotos = recentes.filter(motivo=Post.Motivo.FOTOS).count()
    parte = de_fotos / total if total else 0
    return "fotos" if parte * 100 < destino.fotos_por_cento else "artigos"


def sugerir_do_banco(destino: Destino) -> list[Post]:
    """O proximo atendimento do banco vira post desta conta."""
    grupos = grupos_disponiveis(destino)
    if not grupos:
        return []
    grupo = grupos[0]
    entrada = Entrada.objects.create(
        tipo=Entrada.Tipo.FOTOS,
        autorizado=all(m.autorizada for m in grupo),
        destinos=[str(destino.pk)],
    )
    entrada.midias.set(grupo)
    Midia.objects.filter(pk__in=[m.pk for m in grupo]).update(situacao=Midia.Situacao.USADA)
    return levar(entrada)


# -- O que funciona: artigo, caso ou foto ---------------------------------------------------
def _tipo_do_post(motivo: str) -> str:
    if motivo == Post.Motivo.FOTOS:
        return "fotos"
    if motivo == Post.Motivo.CASO:
        return "casos"
    return "artigos"


def placar_por_tipo(destino: Destino, *, desde=None) -> dict:
    """{"artigos"|"casos"|"fotos": {"n", "taxa"}} dos posts julgados da conta."""
    julgados = destino.posts.filter(sucesso__isnull=False).exclude(motivo=Post.Motivo.HISTORICO)
    if desde is not None:
        julgados = julgados.filter(publicado_em__gte=desde)
    contas: dict = {}
    for motivo, sucesso in julgados.values_list("motivo", "sucesso"):
        par = contas.setdefault(_tipo_do_post(motivo), [0, 0])
        par[0] += int(bool(sucesso))
        par[1] += 1
    return {
        tipo: {"n": n, "taxa": round(100 * s / n) if n else None} for tipo, (s, n) in contas.items()
    }


def ajustar_mistura(destino: Destino) -> int | None:
    """Anda a proporcao de fotos para o tipo que funciona mais. Devolve a nova
    proporcao, ou None se nada mudou."""
    config = ConfiguracaoSocial.carregar()
    if not parametros.valor("ajustar_mistura", config):
        return None
    if not 0 < destino.fotos_por_cento < 100:
        return None
    agora = timezone.now()
    if destino.mistura_ajustada_em and destino.mistura_ajustada_em > agora - JANELA_DA_MISTURA:
        return None
    placar = placar_por_tipo(destino, desde=agora - timedelta(days=90))
    f, a = placar.get("fotos"), placar.get("artigos")
    if not f or not a or f["n"] < MINIMO_JULGADOS or a["n"] < MINIMO_JULGADOS:
        return None
    diferenca = (f["taxa"] - a["taxa"]) / 100
    if abs(diferenca) < DIFERENCA_DA_MISTURA:
        return None
    passo = PASSO_DA_MISTURA if diferenca > 0 else -PASSO_DA_MISTURA
    nova = min(max(destino.fotos_por_cento + passo, 10), 90)
    if nova == destino.fotos_por_cento:
        return None
    destino.fotos_por_cento = nova
    destino.mistura_ajustada_em = agora
    destino.save(update_fields=["fotos_por_cento", "mistura_ajustada_em"])
    return nova


# -- Descrever fotos (modelo de visao, opcional) ----------------------------------------------
DESCRICAO = {
    "type": "object",
    "properties": {"descricao": {"type": "string"}},
    "required": ["descricao"],
}


def descrever_pendentes(limite: int = 20) -> int:
    """Fotos do banco sem nota ganham uma descricao do que aparece. So com a
    opcao ligada; modelo sem visao (ou recusa) marca a foto como tentada."""
    import json

    config = ConfiguracaoSocial.carregar()
    if not config.descrever_fotos:
        return 0
    negocio = fontes.negocio()
    feitas = 0
    pendentes = Midia.objects.filter(
        banco=True, tipo=Midia.Tipo.FOTO, descricao_tentada=False, nota=""
    ).exclude(situacao=Midia.Situacao.DESCARTADA)[:limite]
    for midia in pendentes:
        try:
            imagem = fotos.versao_para(midia, "linkedin")
            resposta = fontes.executar(
                "social_foto",
                {"tema": negocio.get("tema") or "", "oferta": negocio.get("oferta") or ""},
                json_schema=DESCRICAO,
                imagens=[("image/jpeg", imagem)],
            )
            midia.descricao = str(json.loads(resposta).get("descricao") or "")[:600]
            feitas += 1
        except fontes.modelo_indisponivel():
            break  # tenta na proxima rodada
        except Exception as exc:
            logger.info("Foto %s nao descrita: %s", midia.pk, exc)
        midia.descricao_tentada = True
        midia.save(update_fields=["descricao", "descricao_tentada"])
    return feitas
