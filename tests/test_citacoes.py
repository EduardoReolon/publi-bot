"""Conferencia de citacoes: cada frase citada contra a fonte, corrigida sozinha.

* a fonte citada nao sustenta, outra da secao sustenta -> troca o marcador;
* nenhuma sustenta -> reescreve so a frase (ate 2 vezes), conferindo de novo;
* ainda nao -> tira o marcador e bloqueia a aprovacao ate aceitar ou editar.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.content import citacoes
from apps.content.models import Article, ArticleSection
from tests.test_interface import ambiente  # noqa: F401

FONTES = [
    SimpleNamespace(content="Estudo sobre escalar inovacoes em projetos de desenvolvimento."),
    SimpleNamespace(content="Revisao: escalar gera desigualdade e dano ambiental."),
]


def _modelo(monkeypatch, julgar, reescrever=None):
    from apps.content import inference

    chamadas = []

    def executar(*, key, variaveis, **kw):
        chamadas.append(key)
        if key == "citation_check":
            texto = json.dumps({"veredito": julgar(variaveis["frase"], variaveis["trecho"])})
        else:
            texto = reescrever(variaveis) if reescrever else "Frase nova."
        return SimpleNamespace(texto=texto)

    monkeypatch.setattr(inference, "executar_prompt", executar)
    return chamadas


def _secao(texto):
    artigo = Article.objects.create(title="Tema")
    secao = ArticleSection.objects.create(
        article=artigo, order=1, heading="Um", body_markdown=texto, citacoes_conferidas=False
    )
    return artigo, secao


@pytest.mark.django_db
def test_fonte_trocada_quando_outra_sustenta(
    ambiente,  # noqa: F811
    monkeypatch,
    conferencia_de_citacoes,
    embedding_falso,
):
    _modelo(monkeypatch, lambda frase, trecho: "sustenta" if "desigualdade" in trecho else "nao")
    artigo, secao = _secao("Escalar gera desigualdade [[FONTE_1]]. Outra frase sem citacao.")

    citacoes.conferir_secao(artigo, secao, FONTES)

    secao.refresh_from_db()
    assert secao.body_markdown == "Escalar gera desigualdade [[FONTE_2]]. Outra frase sem citacao."
    assert secao.citacoes_conferidas
    assert artigo.conferencia_citacoes[0]["acao"] == "trocada"


@pytest.mark.django_db
def test_frase_reescrita_ate_a_fonte_sustentar(
    ambiente,  # noqa: F811
    monkeypatch,
    conferencia_de_citacoes,
    embedding_falso,
):
    tentativas = []

    def reescrever(variaveis):
        tentativas.append(variaveis["dica"])
        return (
            "Projetos de desenvolvimento escalam inovacoes"
            if len(tentativas) == 2
            else "Ainda errado."
        )

    chamadas = _modelo(
        monkeypatch,
        lambda frase, trecho: "sustenta"
        if frase.startswith("Projetos de desenvolvimento")
        else "nao",
        reescrever,
    )
    artigo, secao = _secao("Empresas lucram mais ao escalar [[FONTE_1]].")

    citacoes.conferir_secao(artigo, secao, FONTES)

    secao.refresh_from_db()
    assert len(tentativas) == 2 and "mais literal" in tentativas[1]
    assert secao.body_markdown.startswith("Projetos de desenvolvimento escalam inovacoes [[FONTE_")
    assert artigo.conferencia_citacoes[0]["acao"] == "reescrita"
    assert chamadas.count("citation_fix") == 2


@pytest.mark.django_db
def test_sem_fonte_bloqueia_ate_aceitar(
    ambiente,  # noqa: F811
    monkeypatch,
    conferencia_de_citacoes,
    embedding_falso,
):
    from apps.content.services import pendencias_para_aprovar

    _, _, client = ambiente
    _modelo(monkeypatch, lambda frase, trecho: "nao", lambda v: "Continua sem base.")
    artigo, secao = _secao("Toda empresa quebra ao crescer rapido [[FONTE_1]].")

    citacoes.conferir_secao(artigo, secao, FONTES)

    secao.refresh_from_db()
    assert "[[FONTE_" not in secao.body_markdown
    artigo.body_markdown = secao.body_markdown
    artigo.save()
    assert any("afirmacao sem fonte" in p for p in pendencias_para_aprovar(artigo))

    client.post(reverse("content:aceitar_sem_fonte", args=[artigo.pk], urlconf="core.urls_tenants"))
    artigo.refresh_from_db()
    assert not any("afirmacao sem fonte" in p for p in pendencias_para_aprovar(artigo))


def test_pesquisa_tira_artigo_de_outra_area():
    import numpy as np

    from apps.knowledge.pesquisa import por_area

    def achado(n, area, sentido):
        return {"id": n, "_sentido": sentido, "primary_topic": {"field": {"display_name": area}}}

    achados = [achado(i, "Business", 0.9 - i / 100) for i in range(8)]
    achados += [achado(8, "Engineering", 0.5), achado(9, "Computer Science", 0.95)]
    ficam, vetores = por_area(achados, np.zeros((10, 2)))
    ids = [d["id"] for d in ficam]
    assert 8 not in ids  # outra area, longe da pauta: sai
    assert 9 in ids  # outra area, mas entre os mais proximos: fica
    assert len(vetores) == len(ficam)


def test_anotar_marca_a_frase_e_ordena_por_urgencia():
    corpo = (
        "<p>Primeira frase comum aqui. Nenhum estudo diz que toda empresa quebra ao crescer. "
        "Projetos de desenvolvimento escalam inovacoes "
        '<a href="https://x">Silva et al., 2024</a>.</p>'
    )
    registro = [
        {
            "secao": 1,
            "acao": "reescrita",
            "frase": "Antes",
            "nova": "Projetos de desenvolvimento escalam inovacoes",
        },
        {
            "secao": 1,
            "acao": "sem_fonte",
            "frase": "Nenhum estudo diz que toda empresa quebra ao crescer.",
        },
    ]
    html, notas = citacoes.anotar(corpo, registro)
    assert [n["nivel"] for n in notas] == ["urgente", "atencao"]
    assert all(n["marcada"] for n in notas)
    assert '<mark class="nota-no-texto urgente" id="nota-2">Nenhum estudo' in html
    assert html.count("</a>") == 1 and html.count("<mark") == 2


@pytest.mark.django_db
def test_gerar_de_novo_vira_versao_do_publicado(ambiente, monkeypatch):  # noqa: F811
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.knowledge.tasks import pesquisar_pauta

    pedidos = []
    monkeypatch.setattr(pesquisar_pauta, "delay", lambda pk: pedidos.append(pk))
    pauta = Topic.objects.create(title="Crescimento linear")
    publicado = Article.objects.create(
        title="Crescimento linear",
        topic=pauta,
        fluxo=fluxos.B,
        status=Article.Status.PUBLISHED,
        remote_id="r1",
    )
    nivel, _msg = fluxos.gerar_de_novo(publicado)

    pauta.refresh_from_db()
    assert nivel == "success"
    pesquisa = pauta.busca_de_fontes["pesquisa"]
    assert pesquisa["versao_de"] == str(publicado.pk) and pesquisa["gerar_depois"]


@pytest.mark.django_db
def test_segundo_clique_nao_dispara_outra_pesquisa(ambiente, monkeypatch):  # noqa: F811
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.knowledge.tasks import pesquisar_pauta

    pedidos = []
    monkeypatch.setattr(pesquisar_pauta, "delay", lambda pk: pedidos.append(pk))
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Crescimento linear")
    publicado = Article.objects.create(
        title="Crescimento linear",
        topic=pauta,
        fluxo=fluxos.B,
        status=Article.Status.PUBLISHED,
        remote_id="r1",
    )
    assert fluxos.gerar_de_novo(publicado)[0] == "success"
    assert fluxos.gerar_de_novo(publicado)[0] == "info"

    pagina = client.get(
        reverse("content:revisar", args=[publicado.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "Etapa 1 de 2" in pagina
    operacao = client.get(
        reverse("operacao:trabalhos", urlconf="core.urls_tenants")
    ).content.decode()
    assert "Pesquisas de artigos em andamento" in operacao and "Crescimento linear" in operacao


def test_frase_central_e_a_que_mais_se_repete():
    import numpy as np

    from apps.content.services import _centralidade

    vetores = np.array([[1, 0], [0.9, 0.1], [0.95, 0.05], [0, 1]])
    nota = _centralidade(vetores)
    assert nota.argmin() == 3  # a frase que destoa das outras e a menos central


@pytest.mark.django_db
def test_gerar_de_novo_esperando_pdfs_mostra_o_motivo(ambiente, monkeypatch):  # noqa: F811
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.content.tasks import gerar_b_quando_pronta
    from apps.knowledge import pesquisa

    _, _, client = ambiente
    monkeypatch.setattr(pesquisa, "pedidos_em_aberto", lambda pauta: 2)
    monkeypatch.setattr(
        pesquisa, "pronta_para_gerar", lambda pauta: "esperando o PDF de 2 artigo(s)"
    )
    pauta = Topic.objects.create(title="Crescimento linear")
    publicado = Article.objects.create(
        title="Crescimento linear",
        topic=pauta,
        fluxo=fluxos.B,
        status=Article.Status.PUBLISHED,
        remote_id="r1",
    )
    pauta.busca_de_fontes = {"pesquisa": {"situacao": "pronta", "versao_de": str(publicado.pk)}}
    pauta.save()

    nivel, mensagem = fluxos.gerar_de_novo(publicado)
    assert nivel == "info" and "PDFs" in mensagem
    pagina = client.get(
        reverse("content:revisar", args=[publicado.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "esperando o PDF de 2" in pagina and "Conferir os PDFs pedidos" in pagina

    # Resolvido o ultimo pedido: a versao nova vai para a fila.
    disparos = []
    monkeypatch.setattr(pesquisa, "pedidos_em_aberto", lambda pauta: 0)
    monkeypatch.setattr(gerar_b_quando_pronta, "delay", lambda pk: disparos.append(pk))
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda f: f())
    pesquisa._depois_dos_pedidos([pauta])
    assert disparos == [str(pauta.pk)]

    # Rascunho gerado de novo ("substitui"): idem, e nao `disparar`, que veria
    # o rascunho e nada faria.
    pauta.busca_de_fontes = {"pesquisa": {"situacao": "pronta", "substitui": str(publicado.pk)}}
    pauta.save()
    pesquisa._depois_dos_pedidos([pauta])
    assert disparos == [str(pauta.pk)] * 2


@pytest.mark.django_db
def test_rascunho_do_b_pode_ser_gerado_de_novo_e_pesquisar_pela_pauta_e_barrado(
    ambiente,  # noqa: F811
    monkeypatch,
):
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.knowledge.tasks import pesquisar_pauta

    pedidos = []
    monkeypatch.setattr(pesquisar_pauta, "delay", lambda pk: pedidos.append(pk))
    _, _, client = ambiente
    pauta = Topic.objects.create(title="Crescimento linear")
    rascunho = Article.objects.create(
        title="Crescimento linear",
        topic=pauta,
        fluxo=fluxos.B,
        status=Article.Status.PENDING_REVIEW,
    )

    # Pela pauta, nao: a pesquisa nova sem texto novo deixaria as referencias trocadas.
    client.post(reverse("content:pesquisar_artigos", args=[pauta.pk], urlconf="core.urls_tenants"))
    assert pedidos == []

    assert fluxos.gerar_de_novo(rascunho)[0] == "success"
    pauta.refresh_from_db()
    assert pauta.busca_de_fontes["pesquisa"]["substitui"] == str(rascunho.pk)
    pagina = client.get(
        reverse("content:revisar", args=[rascunho.pk], urlconf="core.urls_tenants")
    ).content.decode()
    assert "substituir este rascunho" in pagina


@pytest.mark.django_db
def test_pdfs_resolvidos_sem_texto_tem_botao_e_varredura(ambiente, monkeypatch):  # noqa: F811
    from apps.content import fluxos
    from apps.content.models import Topic
    from apps.content.tasks import gerar_b_quando_pronta
    from apps.knowledge import pesquisa

    _, _, client = ambiente
    monkeypatch.setattr(pesquisa, "pedidos_em_aberto", lambda pauta: 0)
    monkeypatch.setattr(pesquisa, "pronta_para_gerar", lambda pauta: "")
    pauta = Topic.objects.create(title="Crescimento linear")
    publicado = Article.objects.create(
        title="Crescimento linear",
        topic=pauta,
        fluxo=fluxos.B,
        status=Article.Status.PUBLISHED,
        remote_id="r1",
    )
    pauta.busca_de_fontes = {
        "pesquisa": {"situacao": "pronta", "versao_de": str(publicado.pk), "gerar_depois": False}
    }
    pauta.save()

    url = reverse("content:revisar", args=[publicado.pk], urlconf="core.urls_tenants")
    assert "Comecar o texto agora" in client.get(url).content.decode()

    disparos = []
    monkeypatch.setattr(gerar_b_quando_pronta, "delay", lambda pk: disparos.append(pk))
    assert fluxos.geracoes_esperando() == 1 and disparos == [str(pauta.pk)]
