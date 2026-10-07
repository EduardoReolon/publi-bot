"""Qual artigo vai para qual conta, e quando — por regras, sem modelo.

Uma vez por dia (com "sugerir sozinho" ligado), cada conta recebe ate o teto
da semana. Uma ideia aprovada na caixa de ideias (ultimos 30 dias, sem artigo
publicado) vem antes de tudo: e um pedido seu. Depois, os candidatos:

* **novo** — artigo no ar ha menos de 30 dias que ainda nao foi para a conta;
* **subindo** — o Radar marcou o artigo como quase na primeira pagina do
  Google: um empurrao de visitas ajuda;
* **converte** — o artigo aparece na jornada de quem virou cliente: vale
  repostar (com outra abordagem) depois de N meses.

A nota de cada candidato, por conta: o quanto o artigo combina com o publico
DA CONTA (proximidade por vetor), mais o peso do motivo, mais o que a rede
favorece (o Google, o local; o Instagram, conteudo em lista; o LinkedIn,
dado com numero). O "por que" vai para a tela, como nas pautas.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta

import numpy as np
from django.db import transaction
from django.utils import timezone

from apps.social import fontes, proprio
from apps.social.models import ConfiguracaoSocial, Destino, Post
from apps.social.redes import rede

logger = logging.getLogger("publibot.social")

JANELA_DE_NOVOS = timedelta(days=30)
PESO = {Post.Motivo.NOVO: 0.10, Post.Motivo.SUBINDO: 0.15, Post.Motivo.CONVERTE: 0.20}
HORARIO_PADRAO = {"linkedin": "08:30", "instagram": "12:00", "gmn": "10:00"}
DIAS_UTEIS = [0, 1, 2, 3, 4]
_LISTA = re.compile(r"^\s*\d|sinais|passos|erros|dicas|mitos|cuidados|maneiras|motivos", re.I)


# -- Agenda -----------------------------------------------------------------------------
def _inicio_da_semana(quando: datetime) -> datetime:
    local = timezone.localtime(quando)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def ocupacao_da_semana(destino: Destino, quando: datetime) -> int:
    """Posts da conta marcados (ou ja publicados) na semana de `quando`."""
    inicio = _inicio_da_semana(quando)
    fim = inicio + timedelta(days=7)
    return (
        destino.posts.filter(
            situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICADO],
        )
        .filter(_entre(inicio, fim))
        .count()
    )


def _entre(inicio, fim):
    """Marcado para a semana, ou publicado nela (o "Ja postei" nao tem horario)."""
    from django.db.models import Q

    return Q(agendado_para__gte=inicio, agendado_para__lt=fim) | Q(
        agendado_para__isnull=True, publicado_em__gte=inicio, publicado_em__lt=fim
    )


def proximo_horario(destino: Destino, depois: datetime | None = None) -> datetime:
    """O proximo horario livre da conta: nos dias e horarios dela, um post por
    horario, sem passar do teto da semana."""
    depois = depois or timezone.now()
    dias = destino.dias or DIAS_UTEIS
    horarios = sorted(destino.horarios or [HORARIO_PADRAO.get(destino.rede, "09:00")])
    ocupados = set(
        destino.posts.filter(
            situacao=Post.Situacao.APROVADO, agendado_para__gte=depois
        ).values_list("agendado_para", flat=True)
    )
    hoje = timezone.localtime(depois).date()
    for desloca in range(0, 120):
        dia = hoje + timedelta(days=desloca)
        if dia.weekday() not in dias:
            continue
        for horario in horarios:
            hora, minuto = (int(x) for x in horario.split(":"))
            quando = timezone.make_aware(datetime.combine(dia, time(hora, minuto)))
            if quando <= depois or quando in ocupados:
                continue
            if ocupacao_da_semana(destino, quando) >= max(destino.teto_semanal, 1):
                break  # semana cheia: vai para a proxima
            return quando
    return depois + timedelta(days=1)


# -- Nota ---------------------------------------------------------------------------------
def _cos(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def nota(artigo: fontes.ArtigoParaRedes, destino: Destino, negocio: dict) -> tuple[float, list]:
    """(nota, motivos) do artigo para a conta. Os motivos vao para o "por que"."""
    r = rede(destino.rede)
    publico = destino.publico or r.publico_padrao
    motivos = []
    try:
        va, vp = fontes.vetores([f"{artigo.titulo}. {artigo.resumo}", publico], consulta=True)
        base = _cos(va, vp)
        motivos.append(f"combina com o publico desta conta ({base:.2f})")
    except Exception as exc:
        logger.info("Nota sem vetores: %s", exc)
        base = 0.5
    bonus = 0.0
    texto = artigo.texto.lower()
    if destino.rede == "gmn":
        locais = [r_ for r_ in negocio.get("regioes", []) if r_.lower() in texto]
        if locais:
            bonus += 0.15
            motivos.append(f"fala de {', '.join(locais[:2])} (busca local)")
    if destino.rede == "instagram":
        em_lista = sum(1 for titulo, _t in artigo.secoes if _LISTA.search(titulo))
        if len(artigo.secoes) >= 4 or em_lista:
            bonus += 0.1
            motivos.append("vira carrossel facil (secoes curtas, em lista)")
    if destino.rede == "linkedin":
        com_numero = sum(1 for f in artigo.frases if re.search(r"\d", f))
        if com_numero >= 2:
            bonus += 0.1
            motivos.append(f"tem {com_numero} frases com dado (o LinkedIn gosta)")
    return base + bonus, motivos


# -- Candidatos e sugestao ----------------------------------------------------------------
def _ultimo_post(destino: Destino, artigo_id: str, *, com_descartados: bool = True):
    """O post mais recente do artigo na conta. Descartado conta para a escolha
    automatica (a pessoa disse nao: nao volta amanha), mas nao para o pedido
    explicito ("Levar as redes")."""
    consulta = destino.posts.filter(artigo_id=artigo_id)
    if not com_descartados:
        consulta = consulta.exclude(situacao=Post.Situacao.DESCARTADO)
    return consulta.order_by("-criado_em").first()


def candidatos(destino: Destino, config: ConfiguracaoSocial, limite: int = 60) -> list[tuple]:
    """[(artigo, motivo, detalhe)] que podem ir para a conta agora."""
    agora = timezone.now()
    saida = []
    for artigo_id in fontes.artigos_no_ar()[:limite]:
        artigo = fontes.artigo(artigo_id)
        if artigo is None or not artigo.url:
            continue
        ultimo = _ultimo_post(destino, artigo_id)
        if ultimo is not None and ultimo.criado_em > agora - timedelta(
            days=config.espacamento_dias
        ):
            continue
        sinais = fontes.sinais(artigo)
        novo = artigo.publicado_em and artigo.publicado_em >= agora - JANELA_DE_NOVOS
        if ultimo is None and novo:
            saida.append((artigo, Post.Motivo.NOVO, "artigo novo"))
        elif sinais["conversoes"] and (
            ultimo is None
            or ultimo.criado_em <= agora - timedelta(days=30 * config.reciclar_apos_meses)
        ):
            saida.append(
                (artigo, Post.Motivo.CONVERTE, f"{sinais['conversoes']} conversao(oes) em 90 dias")
            )
        elif sinais["quase_la"]:
            saida.append((artigo, Post.Motivo.SUBINDO, "quase na primeira pagina do Google"))
    return saida


def sugerir(
    destino: Destino,
    artigo,
    motivo: str,
    por_que: str,
    *,
    tema=None,
    versoes: int | None = None,
    ideia: str = "",
    entrada=None,
) -> list[Post]:
    """Cria o(s) post(s) sugerido(s) e pede a redacao. Com 2 versoes, cada uma
    com uma abordagem diferente, ligadas uma a outra (o par do teste A/B).
    `ideia`: um angulo sugerido (pela outra IA) que vai para quem escreve.
    `entrada`: o material proprio (caso, fotos) quando o post nao e de artigo."""
    from apps.social import experimentos
    from apps.social.tasks import escrever_post

    config = ConfiguracaoSocial.carregar()
    versoes = versoes or max(config.variantes, 1)
    abordagens = experimentos.escolher(destino, versoes) or [None]
    # O recado em teste vale para as versoes do par por igual: o A/B continua
    # comparando so a abordagem.
    recado = experimentos.sortear_recado(destino)
    extras = {
        **({"ideia": ideia[:500]} if ideia else {}),
        **(
            {"recado": {"id": str(recado.pk), "nome": recado.nome, "instrucao": recado.instrucao}}
            if recado
            else {}
        ),
    }
    posts = []
    with transaction.atomic():
        for abordagem in abordagens:
            post = Post.objects.create(
                destino=destino,
                artigo_id=artigo.id,
                artigo_titulo=artigo.titulo[:300],
                artigo_url=artigo.url,
                abordagem=abordagem,
                tema=tema,
                entrada=entrada,
                motivo=motivo,
                por_que=por_que,
                variante_de=posts[0] if posts else None,
                extras=dict(extras),
            )
            posts.append(post)
        for post in posts:
            transaction.on_commit(lambda pk=str(post.pk): escrever_post.delay(pk))
    return posts


# Redes em que o post nasce de um TEMA (varios artigos) quando ha tema bom.
REDES_DE_TEMA = {"instagram"}


def tema_para(destino: Destino, config: ConfiguracaoSocial):
    """O tema de nota mais alta que a conta ainda nao usou no espacamento."""
    from apps.social.models import Tema

    recentes = destino.posts.filter(
        tema__isnull=False,
        criado_em__gt=timezone.now() - timedelta(days=config.espacamento_dias),
    ).values_list("tema__titulo", flat=True)
    return (
        Tema.objects.filter(ativo=True).exclude(titulo__in=list(recentes)).order_by("-nota").first()
    )


def sugerir_tema(
    destino: Destino,
    tema,
    *,
    versoes: int | None = None,
    motivo: str = "",
    por_que: str = "",
    ideia: str = "",
) -> list[Post]:
    artigo = fontes.artigo(tema.artigo_principal)
    if artigo is None:
        return []
    explicacao = "; ".join(tema.sinais.get("explicacao", []))
    motivo = motivo or (Post.Motivo.TESTE if versoes == 2 else Post.Motivo.TEMA)
    return sugerir(
        destino,
        artigo,
        motivo,
        por_que or f"Tema (nota {tema.nota:.2f}): {explicacao}.",
        tema=tema,
        versoes=versoes,
        ideia=ideia,
    )


def rodada() -> int:
    """A sugestao do dia, em cada conta ligada. Devolve quantos posts criou."""
    config = ConfiguracaoSocial.carregar()
    if not config.ligado:
        return 0
    negocio = fontes.negocio()
    criados = 0
    for destino in Destino.objects.filter(ligado=True):
        semana = timezone.now()
        livres = destino.teto_semanal - ocupacao_da_semana(destino, semana)
        pendentes = destino.posts.filter(
            situacao__in=[Post.Situacao.SUGERIDO, Post.Situacao.GERANDO, Post.Situacao.RASCUNHO]
        ).count()
        # Nao empilha sugestao: o que ja espera revisao conta como vaga ocupada.
        vagas = min(livres - pendentes, 1)
        if vagas <= 0:
            continue
        # Conta que vive de foto real: a vez e do banco de fotos (na proporcao dela).
        if proprio.tipo_da_vez(destino) == "fotos":
            novos = proprio.sugerir_do_banco(destino)
            if novos or destino.fotos_por_cento >= 100:
                criados += len(novos)
                continue
        # Ideia que voce aprovou (caixa de ideias) e pedido explicito: vem antes.
        ideia = proprio.ideia_da_vez(destino)
        if ideia is not None:
            novos = proprio.post_de_ideia(destino, ideia)
            if novos:
                criados += len(novos)
                continue
        if destino.rede in REDES_DE_TEMA:
            tema = tema_para(destino, config)
            novos = sugerir_tema(destino, tema) if tema is not None else []
            if novos:
                criados += len(novos)
                continue
        pontuados = []
        for artigo, motivo, detalhe in candidatos(destino, config):
            valor, motivos = nota(artigo, destino, negocio)
            valor += PESO.get(motivo, 0)
            pontuados.append((valor, artigo, motivo, [detalhe, *motivos]))
        pontuados.sort(key=lambda x: -x[0])
        for _valor, artigo, motivo, motivos in pontuados[:vagas]:
            criados += len(sugerir(destino, artigo, motivo, "; ".join(motivos).capitalize() + "."))
        if not pontuados and destino.fotos_por_cento > 0:
            # Sem artigo para levar hoje: o banco de fotos cobre a vaga.
            criados += len(proprio.sugerir_do_banco(destino))
    return criados


def levar_as_redes(artigo_id: str, destinos=None) -> list[Post]:
    """Pedido da pessoa (botao no artigo): um post em cada conta ligada, sem
    esperar a rodada, respeitando so o espacamento do mesmo artigo."""
    artigo = fontes.artigo(artigo_id)
    if artigo is None:
        return []
    config = ConfiguracaoSocial.carregar()
    criados = []
    for destino in destinos or Destino.objects.filter(ligado=True):
        ultimo = _ultimo_post(destino, artigo_id, com_descartados=False)
        if ultimo is not None and ultimo.criado_em > timezone.now() - timedelta(
            days=config.espacamento_dias
        ):
            continue
        criados += sugerir(destino, artigo, Post.Motivo.PEDIDO, "Pedido por voce, no artigo.")
    return criados
