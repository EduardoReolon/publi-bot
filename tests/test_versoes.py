"""Versoes de artigo publicado e a rota de atualizacao.

* a versao nova e uma linha nova, com copia de secoes, citacoes e FAQ, que
  herda o remote_id e aponta para a anterior;
* publicada, vai por PUT /publications/{remote_id}/ — a mesma pagina — e as
  anteriores ficam como substituidas;
* sem o recurso `update` no site, nao tenta;
* nunca duas versoes em aberto do mesmo artigo;
* da tela de atualizacoes, a versao nasce com o motivo anotado.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Article, ArticleFaq, ArticleSection
from tests.test_integrations import site, tenant_integracoes  # noqa: F401
from tests.test_interface import ambiente  # noqa: F401


def _publicado(**campos):
    artigo = Article.objects.create(
        title="Como calcular o LTV",
        body_markdown="## LTV\n\nTexto.",
        status=Article.Status.PUBLISHED,
        published_at=timezone.now(),
        published_url="https://exemplo.com.br/blog/ltv/",
        remote_id="9c1d",
        author_name="Ana",
        **campos,
    )
    ArticleSection.objects.create(article=artigo, order=1, heading="LTV", body_markdown="Texto.")
    ArticleFaq.objects.create(
        article=artigo, order=1, question="O que e LTV?", answer="Valor do cliente."
    )
    return artigo


@pytest.mark.django_db
def test_versao_nova_copia_tudo_e_herda_o_id_remoto(site):  # noqa: F811
    from apps.content.versoes import criar_nova_versao

    v1 = _publicado()

    v2 = criar_nova_versao(v1, notas="Responder: LTV em SaaS")

    assert v2.pk != v1.pk
    assert v2.previous_version == v1
    assert v2.version_number == 2
    assert v2.status == Article.Status.PENDING_REVIEW
    assert v2.remote_id == "9c1d" and v2.published_url == v1.published_url
    assert v2.idempotency_key != v1.idempotency_key
    assert v2.sections.get().heading == "LTV"
    assert v2.faq.get().question == "O que e LTV?"
    assert v2.revisions.get().body_markdown == v1.body_markdown
    assert v2.e_atualizacao

    # Pedir de novo nao cria uma terceira: devolve a em aberto, com a nota.
    mesma = criar_nova_versao(v1, notas="Responder: LTV em varejo")
    assert mesma.pk == v2.pk
    assert "varejo" in mesma.update_notes and "SaaS" in mesma.update_notes


@pytest.mark.django_db
def test_so_artigo_publicado_ganha_versao(site):  # noqa: F811
    from apps.content.versoes import VersaoRecusada, criar_nova_versao

    rascunho = Article.objects.create(title="x", status=Article.Status.PENDING_REVIEW)
    with pytest.raises(VersaoRecusada):
        criar_nova_versao(rascunho)


@pytest.mark.django_db
def test_versao_publicada_vai_pela_rota_de_atualizacao(site, settings, monkeypatch):  # noqa: F811
    from apps.content.versoes import criar_nova_versao
    from apps.integrations.client import SiteClient
    from apps.integrations.publishing import publicar_artigo

    settings.PUBLISH_DRY_RUN = False
    settings.PUBLISHING_ENABLED = True
    site.capabilities = ["idempotency", "update"]
    site.save()
    v1 = _publicado()
    v2 = criar_nova_versao(v1)
    v2.status = Article.Status.APPROVED_SCHEDULED
    v2.save()
    chamadas = []

    def requisitar(self, metodo, rota, **kwargs):
        chamadas.append((metodo, rota, kwargs["headers"]["Idempotency-Key"]))
        return {
            "status": "updated",
            "remote_id": "9c1d",
            "url": "https://exemplo.com.br/blog/ltv/",
            "version": 2,
        }

    monkeypatch.setattr(SiteClient, "_requisitar", requisitar)

    publicar_artigo(v2, site)

    assert chamadas == [("PUT", "/publications/9c1d/", str(v2.idempotency_key))]
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v2.status == Article.Status.PUBLISHED
    assert v2.published_url == "https://exemplo.com.br/blog/ltv/"
    assert v1.status == Article.Status.SUPERSEDED


@pytest.mark.django_db
def test_site_sem_update_nao_recebe_versao(site, settings):  # noqa: F811
    from apps.content.versoes import criar_nova_versao
    from apps.integrations.publishing import PublicacaoBloqueada, publicar_artigo

    site.capabilities = ["idempotency"]
    site.save()
    v2 = criar_nova_versao(_publicado())
    v2.status = Article.Status.APPROVED_SCHEDULED
    v2.save()

    with pytest.raises(PublicacaoBloqueada, match="update"):
        publicar_artigo(v2, site)


@pytest.mark.django_db
def test_da_sugestao_de_atualizacao_para_a_versao(ambiente):  # noqa: F811
    from apps.radar.models import SugestaoDeAtualizacao

    _, _, client = ambiente
    v1 = _publicado()
    sugestao = SugestaoDeAtualizacao.objects.create(
        url=v1.published_url,
        titulo=v1.title,
        artigo=v1,
        tipo="acrescentar",
        evidencia={"tema": "ltv saas", "sinais": [{"texto": "LTV em SaaS", "volume": 300}]},
    )

    resposta = client.post(
        reverse("radar:decidir_atualizacao", args=[sugestao.pk], urlconf="core.urls_tenants"),
        {"decisao": "versao"},
    )

    v2 = Article.objects.get(previous_version=v1)
    assert resposta["Location"].endswith(f"/artigos/{v2.pk}/")
    assert "LTV em SaaS (300/mes)" in v2.update_notes
    sugestao.refresh_from_db()
    assert sugestao.situacao == "feita"
    pagina = client.get(resposta["Location"]).content.decode()
    assert "versao 2" in pagina.lower() and "LTV em SaaS" in pagina


@pytest.mark.django_db
def test_botao_de_versao_na_revisao_do_publicado(ambiente):  # noqa: F811
    _, _, client = ambiente
    v1 = _publicado()
    url = reverse("content:revisar", args=[v1.pk], urlconf="core.urls_tenants")
    assert "Criar versao nova" in client.get(url).content.decode()

    client.post(reverse("content:nova_versao", args=[v1.pk], urlconf="core.urls_tenants"))

    assert Article.objects.filter(previous_version=v1).count() == 1
    assert "versao nova deste artigo em andamento" in client.get(url).content.decode()


@pytest.mark.django_db
def test_bloqueio_vira_falha_visivel_e_nao_repete(site, settings):  # noqa: F811
    from apps.content.versoes import criar_nova_versao
    from apps.integrations.tasks import publish_content

    site.capabilities = ["idempotency"]
    site.save()
    v2 = criar_nova_versao(_publicado())
    v2.status = Article.Status.APPROVED_SCHEDULED
    v2.save()

    assert publish_content.run("article", str(v2.pk)) == "bloqueado"
    v2.refresh_from_db()
    assert v2.status == Article.Status.PUSH_FAILED
    assert "update" in v2.last_publish_error
