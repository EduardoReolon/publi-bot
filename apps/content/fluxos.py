"""Os dois fluxos de um artigo, e o disparo de cada um.

A · Fontes curadas: o artigo usa o acervo que a pessoa curou (paginas, videos,
artigos, documentos). B · Pesquisa cientifica: artigos achados para a pauta por
sentido, escrito a partir dos resumos (`knowledge.pesquisa`).

Uma pauta pode ter um artigo de cada, para comparar; so um vai ao ar — ao
aprovar um, o outro e arquivado (`arquivar_o_outro`). Com os dois fluxos ligados
na configuracao, "Gerar" dispara os dois: o que estiver pronto vai para a fila,
e o B sem pesquisa comeca por ela e gera sozinho quando ela terminar.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.content.models import Article, Fluxo
from apps.ops.models import GenerationJob

A = Fluxo.CURADAS.value
B = Fluxo.PESQUISA.value
NOMES = {A: _("A · Fontes curadas"), B: _("B · Pesquisa cientifica")}
# O que cada fluxo e, para quem nao acompanhou a construcao. Um lugar so: a
# pauta e a pagina do artigo mostram o mesmo texto.
DESCRICOES = {
    A: _(
        "Escrito com o acervo que voce cura (paginas, videos, artigos e documentos). "
        "Falta fonte, a busca sugere e a geracao pede a curadoria."
    ),
    B: _(
        "Escrito a partir de artigos cientificos achados por sentido para a pauta, "
        "pelos resumos; o PDF so e pedido quando o texto precisa de um detalhe. "
        "Traz contrapontos quando a literatura diverge."
    ),
}
EM_CURSO = [
    GenerationJob.Status.PENDING,
    GenerationJob.Status.RUNNING,
    GenerationJob.Status.WAITING_CAPACITY,
]


def ligados() -> list[str]:
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    return [
        f for f, ligado in ((A, config.fluxo_do_acervo), (B, config.fluxo_da_pesquisa)) if ligado
    ]


def artigo_do_fluxo(pauta, fluxo: str):
    """O artigo deste fluxo (o rejeitado nao conta: pode-se gerar de novo)."""
    return (
        pauta.articles.filter(fluxo=fluxo)
        .exclude(status=Article.Status.REJECTED)
        .order_by("-created_at")
        .first()
    )


def em_andamento(pauta, fluxo: str):
    trabalhos = GenerationJob.objects.filter(
        kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(pauta.pk), status__in=EM_CURSO
    )
    # Trabalho antigo, sem a chave, e do fluxo A. (Nao usar `exclude(fluxo=B)`:
    # com a chave ausente o SQL da NULL e a linha some.)
    deste = Q(step_payloads__fluxo=fluxo)
    if fluxo == A:
        deste |= ~Q(step_payloads__has_key="fluxo")
    return trabalhos.filter(deste).first()


def disparar(pauta, fluxo: str) -> tuple[str, str]:
    """Poe a geracao do fluxo na fila, se der. Devolve (nivel, mensagem) para a tela."""
    from apps.content.tasks import gerar_artigo
    from apps.ops.orchestrator import devolver_a_fila

    nome = NOMES[fluxo]
    trabalho = em_andamento(pauta, fluxo)
    if trabalho is not None:
        return "info", _("%(f)s: ja esta sendo gerado (trabalho %(id)s).") % {
            "f": nome,
            "id": str(trabalho.pk)[:8],
        }
    # Uma geracao que falhou continua do passo em que parou, no MESMO artigo:
    # pedir de novo nunca cria um segundo artigo nem um segundo trabalho.
    falhou = retomavel(pauta, fluxo)
    if falhou is not None:
        devolver_a_fila(falhou)
        return "success", _("%(f)s: geracao retomada do passo %(n)s.") % {
            "f": nome,
            "n": falhou.current_step,
        }
    if artigo_do_fluxo(pauta, fluxo) is not None:
        return "info", _("%(f)s: esta pauta ja tem artigo neste fluxo.") % {"f": nome}

    if fluxo == B:
        return _disparar_b(pauta)

    # A: poucos videos perto da pauta, a primeira tentativa busca no YouTube e
    # para, para a pessoa olhar. Gerar de novo segue.
    from apps.knowledge.referencias import videos_antes_de_gerar

    achados = videos_antes_de_gerar(pauta)
    if achados:
        return "warning", _(
            "%(f)s: achei %(n)s video(s) para a pauta, em Fontes sugeridas. Gere de novo "
            "para seguir sem eles (ou depois de aprova-los)."
        ) % {"f": nome, "n": achados}
    trabalho = gerar_artigo(pauta)
    return "success", _("%(f)s: geracao iniciada (trabalho %(id)s).") % {
        "f": nome,
        "id": str(trabalho.pk)[:8],
    }


# Rascunho que pode ser refeito do zero (ainda nao publicado, ja escrito).
RASCUNHOS = (
    Article.Status.PENDING_REVIEW,
    Article.Status.PUSH_FAILED,
    Article.Status.APPROVED_SCHEDULED,
)


def gerar_de_novo(artigo) -> tuple[str, str]:
    """Gera o artigo de novo, do zero, com as regras atuais (conferencia de
    citacoes, filtro de area...). No B, com uma pesquisa de artigos nova.

    * publicado -> versao nova (mesma pagina; so substitui ao aprovar);
    * rascunho -> texto novo que arquiva este quando nascer (ate la, este fica).
    """
    from apps.content.tasks import gerar_artigo

    pauta = artigo.topic
    fluxo = artigo.fluxo or A
    publicado = artigo.status == Article.Status.PUBLISHED and bool(artigo.remote_id)
    if pauta is None or not (publicado or artigo.status in RASCUNHOS):
        return "error", _(
            "So artigo publicado ou ja escrito (ligado a uma pauta) pode ser gerado de novo."
        )
    if publicado and artigo.versao_em_aberto is not None:
        return "info", _("Ja ha uma versao nova deste artigo em andamento.")
    if em_andamento(pauta, fluxo) is not None:
        return "info", _("Ja ha uma geracao desta pauta em andamento.")
    chave = "versao_de" if publicado else "substitui"
    if fluxo == B:
        from apps.knowledge.referencias import registrar

        pesquisa = situacao_da_pesquisa(pauta)
        dados = (pauta.busca_de_fontes or {}).get("pesquisa") or {}
        if dados.get(chave) == str(artigo.pk) and dados.get("situacao") == "pronta":
            from apps.knowledge.pesquisa import pedidos_em_aberto

            if pedidos_em_aberto(pauta):
                return "info", _(
                    "A pesquisa ja terminou e o texto novo espera os PDFs que ela "
                    "pediu: abra 'Conferir os PDFs pedidos' e envie ou siga com o "
                    "resumo. Ele e gerado sozinho quando o ultimo for resolvido."
                )
        if pesquisa["rodando"] and not pesquisa["parada"]:
            # Segundo clique: a pesquisa ja esta na fila; nada novo e disparado.
            return "info", _(
                "Ja esta pesquisando os artigos para o texto novo (desde %(h)s). "
                "Ele e gerado sozinho quando a pesquisa terminar."
            ) % {
                "h": timezone.localtime(pesquisa["desde"]).strftime("%H:%M")
                if pesquisa["desde"]
                else "-"
            }

        registrar(pauta, "pesquisa", **{"versao_de": "", "substitui": "", chave: str(artigo.pk)})
        iniciar_pesquisa(pauta, gerar_depois=True)
        return "success", _(
            "Pesquisando artigos de novo; o texto novo e gerado sozinho quando a "
            "pesquisa terminar (alguns minutos)."
        )
    trabalho = gerar_artigo(pauta, fluxo=fluxo, **{chave: str(artigo.pk)})
    return "success", _("Gerando o texto novo (trabalho %(id)s).") % {"id": str(trabalho.pk)[:8]}


def _disparar_b(pauta) -> tuple[str, str]:
    from apps.content.tasks import gerar_artigo, gerar_b_quando_pronta
    from apps.knowledge.pesquisa import pronta_para_gerar
    from apps.knowledge.referencias import registrar

    nome = NOMES[B]
    situacao = ((pauta.busca_de_fontes or {}).get("pesquisa") or {}).get("situacao")
    if situacao in (None, "erro") or situacao_da_pesquisa(pauta)["parada"]:
        iniciar_pesquisa(pauta, gerar_depois=True)
        return "info", _(
            "%(f)s: pesquisando artigos; o artigo e gerado sozinho quando a pesquisa terminar."
        ) % {"f": nome}
    if situacao in ("na_fila", "pesquisando"):
        registrar(pauta, "pesquisa", gerar_depois=True)
        return "info", _("%(f)s: a pesquisa esta rodando; o artigo sai quando ela terminar.") % {
            "f": nome
        }
    motivo = pronta_para_gerar(pauta)
    if motivo:
        registrar(pauta, "pesquisa", gerar_depois=True)
        gerar_b_quando_pronta.apply_async((str(pauta.pk),), countdown=60)
        return "info", _("%(f)s: %(m)s Gera sozinho quando ficar pronto.") % {
            "f": nome,
            "m": motivo,
        }
    trabalho = gerar_artigo(pauta, fluxo=B)
    return "success", _("%(f)s: geracao iniciada (trabalho %(id)s).") % {
        "f": nome,
        "id": str(trabalho.pk)[:8],
    }


def iniciar_pesquisa(pauta, *, gerar_depois: bool | None = False) -> None:
    """Poe a pesquisa do B na fila. `gerar_depois=None` mantem o pedido de
    gerar ao terminar que ja houver (recomecar uma pesquisa parada)."""
    from django.db import transaction
    from django.utils import timezone

    from apps.knowledge.referencias import registrar
    from apps.knowledge.tasks import pesquisar_pauta

    if gerar_depois is None:
        gerar_depois = situacao_da_pesquisa(pauta)["gerar_depois"]
    registrar(
        pauta,
        "pesquisa",
        situacao="na_fila",
        em=timezone.now().isoformat(),
        erro="",
        gerar_depois=gerar_depois,
    )
    transaction.on_commit(lambda: pesquisar_pauta.delay(str(pauta.pk)))


def irmaos(artigo) -> list:
    """Os artigos dos outros fluxos da mesma pauta (o que se compara)."""
    if not artigo.topic_id:
        return []
    return list(
        Article.objects.filter(topic_id=artigo.topic_id)
        .exclude(pk=artigo.pk)
        .exclude(status__in=[Article.Status.REJECTED, Article.Status.SUPERSEDED])
        .exclude(fluxo=artigo.fluxo)
    )


def arquivar_o_outro(artigo) -> int:
    """Aprovado um, o do outro fluxo nao vai ao ar: dois textos da mesma pauta
    competiriam pela mesma busca. Fica como rejeitado, com o motivo."""
    arquivados = 0
    for outro in irmaos(artigo):
        if outro.status in (Article.Status.PUBLISHED, Article.Status.APPROVED_SCHEDULED):
            continue
        tese = dict(outro.thesis_json or {})
        tese["arquivado_por"] = str(artigo.pk)
        outro.thesis_json = tese
        outro.status = Article.Status.REJECTED
        outro.save(update_fields=["status", "thesis_json"])
        arquivados += 1
    return arquivados


def pesquisas_paradas() -> int:
    """Recomeca as pesquisas do B que a tarefa abandonou (servidor reiniciado
    no meio, por exemplo). Roda com a varredura de pedidos parados."""
    from apps.content.models import Topic

    recomecadas = 0
    for pauta in Topic.objects.filter(
        busca_de_fontes__pesquisa__situacao__in=["na_fila", "pesquisando"]
    ):
        if situacao_da_pesquisa(pauta)["parada"]:
            iniciar_pesquisa(pauta, gerar_depois=None)
            recomecadas += 1
    return recomecadas


def desistir(trabalho, motivo: str) -> None:
    """Encerra uma geracao em curso (placa desligada ha horas, por exemplo):
    o fluxo volta a poder ser gerado. O que ja foi feito fica no trabalho."""
    from django.utils import timezone

    GenerationJob.objects.filter(pk=trabalho.pk).update(
        status=GenerationJob.Status.FAILED,
        last_error=motivo,
        lease_token=None,
        lease_expires_at=None,
        next_attempt_at=None,
        finished_at=timezone.now(),
    )


def trabalhos_em_curso(pautas) -> dict:
    """{(pauta_id, fluxo): trabalho} das geracoes em curso, numa consulta so."""
    saida = {}
    trabalhos = GenerationJob.objects.filter(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id__in=[p.pk for p in pautas],
        status__in=EM_CURSO,
    ).order_by("created_at")
    for trabalho in trabalhos:
        fluxo = (trabalho.step_payloads or {}).get("fluxo", A)
        saida[(trabalho.target_object_id, fluxo)] = trabalho
    return saida


def retomavel(pauta, fluxo: str):
    """A ultima geracao do fluxo que falhou, se ela ainda e o caminho: sem
    artigo pronto depois dela, e com o rascunho dela (se ja criou um) vivo."""
    falhou = ultimas_falhas([pauta]).get((pauta.pk, fluxo))
    if falhou is None:
        return None
    artigo_do_trabalho = ((falhou.step_payloads or {}).get("1") or {}).get("article_id")
    for artigo in pauta.articles.filter(fluxo=fluxo).exclude(status=Article.Status.REJECTED):
        if str(artigo.pk) != artigo_do_trabalho or artigo.status != Article.Status.DRAFTING:
            return None  # ha outro artigo, ou este ja andou: nada a retomar
    if (
        artigo_do_trabalho
        and not pauta.articles.filter(
            pk=artigo_do_trabalho, status=Article.Status.DRAFTING
        ).exists()
    ):
        return None  # o rascunho dela foi descartado: comeca de novo
    return falhou


def ultimas_falhas(pautas) -> dict:
    """{(pauta_id, fluxo): trabalho} da geracao mais recente que falhou."""
    saida = {}
    falhas = GenerationJob.objects.filter(
        kind=GenerationJob.Kind.PILLAR_ARTICLE,
        target_object_id__in=[p.pk for p in pautas],
        status=GenerationJob.Status.FAILED,
    ).order_by("updated_at")
    for trabalho in falhas:
        fluxo = (trabalho.step_payloads or {}).get("fluxo", A)
        saida[(trabalho.target_object_id, fluxo)] = trabalho
    return saida


def situacao_da_pesquisa(pauta) -> dict:
    """A pesquisa do B como a tela mostra: situacao, desde quando e se parou."""
    import datetime

    from django.utils import timezone

    pesquisa = (pauta.busca_de_fontes or {}).get("pesquisa") or {}
    situacao = pesquisa.get("situacao") or ""
    desde = None
    try:
        desde = datetime.datetime.fromisoformat(pesquisa.get("em") or "")
    except ValueError:
        pass
    if desde and timezone.is_naive(desde):
        desde = timezone.make_aware(desde)
    rodando = situacao in ("na_fila", "pesquisando")
    return {
        "situacao": situacao,
        "rodando": rodando,
        "desde": desde,
        "parada": bool(
            rodando
            and desde
            and timezone.now() - desde > datetime.timedelta(minutes=PESQUISA_PARADA_MINUTOS)
        ),
        "gerar_depois": bool(pesquisa.get("gerar_depois")),
        "erro": pesquisa.get("erro") or pesquisa.get("erro_ao_gerar") or "",
    }


# Uma pesquisa leva minutos (algumas buscas e uma chamada ao modelo). Passado
# isto sem terminar, a tarefa morreu (o servidor reiniciou, por exemplo).
PESQUISA_PARADA_MINUTOS = 30


def para_a_tela(
    pauta, config, trabalhos: dict | None = None, falhas: dict | None = None
) -> list[dict]:
    """Por fluxo ligado: o artigo (se houver), o trabalho em curso, a pesquisa
    (no B) e se ainda da para gerar."""
    saida = []
    for fluxo, ligado in ((A, config.fluxo_do_acervo), (B, config.fluxo_da_pesquisa)):
        if not ligado:
            continue
        artigos = [a for a in pauta.articles.all() if a.fluxo == fluxo]
        vivos = [a for a in artigos if a.status != Article.Status.REJECTED]
        trabalho = (
            trabalhos.get((pauta.pk, fluxo))
            if trabalhos is not None
            else em_andamento(pauta, fluxo)
        )
        saida.append(
            {
                "fluxo": fluxo,
                "nome": NOMES[fluxo],
                "descricao": DESCRICOES[fluxo],
                "artigos": artigos,
                "falta": not vivos,
                "trabalho": trabalho,
                # A falha so importa enquanto nao ha artigo que andou depois dela.
                "falhou": (falhas or {}).get((pauta.pk, fluxo))
                if all(
                    a.status in (Article.Status.DRAFTING, Article.Status.REJECTED) for a in artigos
                )
                else None,
                "pesquisa": situacao_da_pesquisa(pauta) if fluxo == B else None,
            }
        )
    return saida
