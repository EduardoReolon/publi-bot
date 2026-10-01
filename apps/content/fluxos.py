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
from django.utils.translation import gettext as _

from apps.content.models import Article, Fluxo
from apps.ops.models import GenerationJob

A = Fluxo.CURADAS.value
B = Fluxo.PESQUISA.value
NOMES = {A: _("A · Fontes curadas"), B: _("B · Pesquisa cientifica")}
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

    nome = NOMES[fluxo]
    if artigo_do_fluxo(pauta, fluxo) is not None:
        return "info", _("%(f)s: esta pauta ja tem artigo neste fluxo.") % {"f": nome}
    trabalho = em_andamento(pauta, fluxo)
    if trabalho is not None:
        return "info", _("%(f)s: ja esta sendo gerado (trabalho %(id)s).") % {
            "f": nome,
            "id": str(trabalho.pk)[:8],
        }

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


def _disparar_b(pauta) -> tuple[str, str]:
    from apps.content.tasks import gerar_artigo, gerar_b_quando_pronta
    from apps.knowledge.pesquisa import pronta_para_gerar
    from apps.knowledge.referencias import registrar

    nome = NOMES[B]
    situacao = ((pauta.busca_de_fontes or {}).get("pesquisa") or {}).get("situacao")
    if situacao in (None, "erro"):
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


def iniciar_pesquisa(pauta, *, gerar_depois: bool = False) -> None:
    from django.db import transaction
    from django.utils import timezone

    from apps.knowledge.referencias import registrar
    from apps.knowledge.tasks import pesquisar_pauta

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


def para_a_tela(pauta, config) -> list[dict]:
    """Por fluxo ligado: o artigo (se houver) e se ainda da para gerar."""
    saida = []
    for fluxo, ligado in ((A, config.fluxo_do_acervo), (B, config.fluxo_da_pesquisa)):
        if not ligado:
            continue
        artigos = [a for a in pauta.articles.all() if a.fluxo == fluxo]
        vivos = [a for a in artigos if a.status != Article.Status.REJECTED]
        saida.append({"fluxo": fluxo, "nome": NOMES[fluxo], "artigos": artigos, "falta": not vivos})
    return saida
