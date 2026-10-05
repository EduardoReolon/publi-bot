"""Dados publicos: catalogo no public, escolha na pauta, fato no artigo.

* a semente cria as instituicoes uma vez (rodar de novo nao duplica);
* o link e reconhecido por dominio e por dominio/caminho;
* a varredura do acervo vira pedido de adaptador (lugar de dados sem adaptador);
* serie cadastrada a mao (so superusuario) entra aprovada, com valor;
* a pauta sugere por embedding, usa, e a geracao recebe [[DADO_N]];
* na montagem o marcador vira "(IBGE, 2019)" e o dado entra nas referencias;
* numero diferente do buscado bloqueia a aprovacao.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.content.models import Article, DadoDaPauta, Topic
from apps.dados import catalogo
from apps.dados.models import Instituicao, PedidoDeAdaptador, Serie, Valor
from tests.test_interface import ambiente  # noqa: F401

U = "core.urls_tenants"


def _serie(titulo="Prevalencia de obesidade em adultos", valor="25.9", **campos):
    ibge, _ = Instituicao.objects.get_or_create(
        sigla="IBGE", defaults={"nome": "IBGE", "dominios": ["ibge.gov.br"], "adaptador": "ibge"}
    )
    serie = Serie.objects.create(
        instituicao=ibge,
        codigo=titulo[:20],
        titulo=titulo,
        unidade="%",
        url="https://sidra.ibge.gov.br/tabela/1",
        origem=Serie.Origem.MANUAL,
        situacao=Serie.Situacao.APROVADA,
        **campos,
    )
    Valor.objects.create(serie=serie, periodo="2019", valor=Decimal(valor))
    return serie


class _MesmoVetor:
    def embed_query(self, texto):
        return [1.0] + [0.0] * 1023

    def embed_passage(self, textos):
        return [self.embed_query(t) for t in textos]


def test_numero_no_jeito_brasileiro():
    assert catalogo.formatar(Decimal("25.900000")) == "25,9"
    assert catalogo.formatar(Decimal("1234567")) == "1.234.567"
    assert catalogo.formas_do_numero(Decimal("1234.5")) == {"1.234,5", "1234,5", "1234.5"}


@pytest.mark.django_db
def test_semente_e_link_por_dominio_e_caminho(ambiente):  # noqa: F811
    call_command("semear_dados")
    call_command("semear_dados")
    assert Instituicao.objects.filter(sigla="IBGE").count() == 1
    assert {"saude", "ia", "obras"} <= {n for i in Instituicao.objects.all() for n in i.nichos}

    assert catalogo.instituicao_do_link("https://sidra.ibge.gov.br/tabela/4752").sigla == "IBGE"
    assert catalogo.instituicao_do_link("https://www.gov.br/saude/vigitel").sigla == "MS"
    assert catalogo.instituicao_do_link("https://www.gov.br/fazenda/x") is None


@pytest.mark.django_db
def test_acervo_vira_pedido_de_adaptador(ambiente):  # noqa: F811
    from apps.dados.acervo import classificar, gravar, texto_do_pedido

    call_command("semear_dados")
    acumulado = {}
    classificar(
        {
            "https://opendatasus.saude.gov.br/dataset/sim": {"d1", "d2"},
            "https://dados.prefeitura.sp.gov.br/x.csv": {"d3"},
            "https://sidra.ibge.gov.br/tabela/4752": {"d1"},  # IBGE: adaptador ainda nao le o link
            "https://blog.qualquer.com/post": {"d4"},
        },
        acumulado,
    )
    gravar(acumulado)

    pedidos = {p.dominio: p for p in PedidoDeAdaptador.objects.all()}
    assert set(pedidos) == {"datasus.saude.gov.br", "dados.prefeitura.sp.gov.br"}
    assert pedidos["datasus.saude.gov.br"].citacoes == 2
    assert "opendatasus" in texto_do_pedido(pedidos["datasus.saude.gov.br"])


@pytest.mark.django_db
def test_cadastro_a_mao_so_superusuario_e_tela(ambiente):  # noqa: F811
    _, usuario, client = ambiente
    call_command("semear_dados")
    ibge = Instituicao.objects.get(sigla="IBGE")
    dados = {"instituicao": ibge.pk, "titulo": "Obesidade", "valor": "25,9", "periodo": "2019"}

    assert client.post(reverse("dados:nova_serie", urlconf=U), dados).status_code == 403
    pagina = client.get(reverse("dados:catalogo", urlconf=U) + "?aba=instituicoes").content.decode()
    assert "pronto" in pagina and "Cadastrar um dado" not in pagina
    assert "Procurar series" not in pagina  # buscar na instituicao: so curador

    usuario.is_superuser = True
    usuario.save()
    client.post(reverse("dados:nova_serie", urlconf=U), dados)
    serie = Serie.objects.get(titulo="Obesidade")
    assert serie.situacao == Serie.Situacao.APROVADA
    assert serie.valores.get().valor == Decimal("25.9")
    pagina = client.get(reverse("dados:catalogo", urlconf=U)).content.decode()
    assert "Obesidade" in pagina and "Cadastrar um dado" in pagina


def test_locais_e_estado_do_site():
    from apps.dados.locais import normalizar, sigla

    assert normalizar("pr") == normalizar("Parana") == normalizar("Paraná") == "Paraná"
    assert normalizar("") == normalizar("brasil") == "Brasil"
    assert sigla("Sao Paulo") == "SP"


@pytest.mark.django_db
def test_da_pauta_ao_artigo(ambiente, monkeypatch):  # noqa: F811
    from apps.content.dados_da_pauta import (
        bloco_para_o_prompt,
        fatos_do_artigo,
        pendencias,
    )
    from apps.content.services import aplicar_rascunho
    from apps.knowledge import embeddings
    from apps.radar.models import ConfiguracaoDoRadar

    _, _, client = ambiente
    monkeypatch.setattr(embeddings, "get_embedding_client", lambda: _MesmoVetor())
    config = ConfiguracaoDoRadar.carregar()
    config.regioes = [
        {"codigo": 1, "nome": "Curitiba,Parana,Brazil"},
        {"codigo": 2, "nome": "Londrina,Parana,Brazil"},
    ]
    config.save()
    serie = _serie()
    Valor.objects.create(serie=serie, local="Paraná", periodo="2019", valor=Decimal("24.1"))
    pauta = Topic.objects.create(title="Obesidade em adultos", status=Topic.Status.APPROVED)
    url_da_pauta = reverse("content:pauta", args=[pauta.pk], urlconf=U)

    # Muito proximo: entra sozinho, em destaque, com o Parana e o Brasil.
    pagina = client.get(url_da_pauta).content.decode()
    assert "entrou sozinho" in pagina and "24,1" in pagina and "25,9" in pagina
    dado = DadoDaPauta.objects.get(topic=pauta, serie=serie)
    assert dado.automatico

    # Tirado nao volta sozinho; "Usar" traz de volta.
    acao = reverse("content:dados_da_pauta", args=[pauta.pk], urlconf=U)
    client.post(acao, {"acao": "tirar", "dado": dado.pk})
    assert "entrou sozinho" not in client.get(url_da_pauta).content.decode()
    client.post(acao, {"serie": serie.pk})
    dado.refresh_from_db()
    assert not dado.tirado and not dado.automatico

    artigo = Article.objects.create(title="Obesidade", topic=pauta)
    fatos = fatos_do_artigo(artigo)
    assert [(f["local"], f["valor"]) for f in fatos] == [("Paraná", "24,1"), ("Brasil", "25,9")]
    bloco = bloco_para_o_prompt(fatos)
    assert "OPCIONAIS" in bloco and "[[DADO_2]]" in bloco

    aplicar_rascunho(artigo, "## Quanto\n\nNo Paraná, 24,1% dos adultos [[DADO_1]].")
    artigo.refresh_from_db()
    assert "(IBGE, 2019)" in artigo.body_markdown and "[[DADO_" not in artigo.body_markdown
    assert "sidra.ibge.gov.br/tabela/1" in artigo.body_html
    assert [f["citado"] for f in artigo.dados_usados] == [True, False]
    assert pendencias(artigo) == []

    artigo.body_markdown = artigo.body_markdown.replace("24,1", "24")
    assert "24,1" in pendencias(artigo)[0]


@pytest.mark.django_db
def test_cada_trecho_recebe_poucos_dados(ambiente, monkeypatch):  # noqa: F811
    from apps.content.dados_da_pauta import MAXIMO_POR_TRECHO, fatos_para_o_trecho
    from apps.knowledge import embeddings

    monkeypatch.setattr(embeddings, "get_embedding_client", lambda: _MesmoVetor())
    fatos = [{"n": i, "serie_id": str(_serie(f"Serie {i}").pk)} for i in range(1, 10)]
    assert len(fatos_para_o_trecho(fatos, "qualquer secao")) == MAXIMO_POR_TRECHO


def test_arquivo_grande_e_apagado(monkeypatch):
    import contextlib
    import os

    import httpx

    from apps.dados.adaptadores import arquivo_temporario

    class Resposta:
        def raise_for_status(self):
            pass

        def iter_bytes(self, _tamanho):
            yield b"x" * 10

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: contextlib.nullcontext(Resposta()))
    with arquivo_temporario("https://exemplo.gov.br/base.csv") as caminho:
        assert os.path.getsize(caminho) == 10
    assert not os.path.exists(caminho)


@pytest.mark.django_db
def test_periodo_novo_vira_sugestao_e_a_versao_exige_o_numero_novo(ambiente):  # noqa: F811
    from apps.content.dados_da_pauta import pendencias
    from apps.radar.atualizacoes import pelos_dados
    from apps.radar.models import SugestaoDeAtualizacao

    _, _, client = ambiente
    serie = _serie()
    artigo = Article.objects.create(
        title="Obesidade",
        status=Article.Status.PUBLISHED,
        remote_id="r1",
        published_url="https://site.exemplo.org/obesidade/",
        body_markdown="Em 2019, 25,9% dos adultos (IBGE, 2019).",
        dados_usados=[
            {**catalogo.fato(serie, "Brasil"), "n": 1, "citado": True, "automatico": False}
        ],
    )
    assert pelos_dados() == 0  # nada mais novo ainda

    Valor.objects.create(serie=serie, local="Brasil", periodo="2023", valor=Decimal("27.8"))
    assert pelos_dados() == 1
    sugestao = SugestaoDeAtualizacao.objects.get(tipo="dado")
    assert sugestao.evidencia["dados"][0]["para"] == "2023: 27,8"
    assert pelos_dados() == 0  # a mesma nao duplica

    client.post(
        reverse("radar:decidir_atualizacao", args=[sugestao.pk], urlconf=U),
        {"decisao": "versao"},
    )
    nova = Article.objects.get(previous_version=artigo)
    assert nova.dados_usados[0]["valor"] == "27,8" and nova.dados_usados[0]["citado"]
    assert "27,8" in pendencias(nova)[0]  # a revisao pede o numero novo no texto
    assert "27,8" in nova.update_notes


@pytest.mark.django_db
def test_link_de_instituicao_confiavel_entra_sem_curadoria():
    from apps.knowledge.fontes_web import _de_instituicao_confiavel

    ibge = Instituicao.objects.create(
        sigla="IBGE-T", nome="IBGE", dominios=["ibge-teste.gov.br"], confiavel=False
    )
    assert not _de_instituicao_confiavel("https://www.ibge-teste.gov.br/estatisticas/x")
    ibge.confiavel = True
    ibge.save()
    assert _de_instituicao_confiavel("https://www.ibge-teste.gov.br/estatisticas/x")
    assert not _de_instituicao_confiavel("https://outro.org/x")


@pytest.mark.django_db
def test_cada_site_escolhe_os_nichos_de_dados(ambiente, monkeypatch):  # noqa: F811
    from apps.content.dados_da_pauta import nichos_do_cliente
    from apps.editorial.models import PerfilDoNegocio
    from apps.knowledge import embeddings

    monkeypatch.setattr(embeddings, "get_embedding_client", lambda: _MesmoVetor())
    saude = _serie("Obesidade em adultos")
    saude.nichos = ["saude"]
    saude.save()
    obra = _serie("Custo do metro quadrado da construcao")
    obra.nichos = ["obras"]
    obra.save()

    assert {s.pk for s in catalogo.sugerir("qualquer coisa", limite=10)} == {saude.pk, obra.pk}
    PerfilDoNegocio.objects.update_or_create(pk=1, defaults={"nichos_de_dados": ["saude"]})
    assert nichos_do_cliente() == ["saude"]
    assert [s.pk for s in catalogo.sugerir("x", limite=10, nichos=nichos_do_cliente())] == [
        saude.pk
    ]


@pytest.mark.django_db
def test_sugeridas_paginadas_e_aprovar_todas(ambiente):  # noqa: F811
    from apps.dados.views import POR_PAGINA

    _, usuario, client = ambiente
    call_command("semear_dados")
    bcb = Instituicao.objects.get(sigla="BCB")
    for n in range(POR_PAGINA + 5):
        Serie.objects.create(
            instituicao=bcb,
            codigo=str(n),
            titulo=f"Serie {n:03d}",
            descricao="<P>O conceito &amp; a base</P>",
            origem=Serie.Origem.CATALOGO,
        )
    url = reverse("dados:catalogo", urlconf=U) + "?aba=series&situacao=sugerida"

    # Quem nao e superusuario ve o aviso de quem aprova, sem botoes.
    pagina = client.get(url).content.decode()
    assert "superusuario" in pagina and ">Aprovar<" not in pagina

    usuario.is_superuser = True
    usuario.save()
    pagina = client.get(url).content.decode()
    assert pagina.count('value="aprovada"') >= POR_PAGINA  # um Aprovar por linha
    assert "&lt;P&gt;" not in pagina and "<P>" not in pagina
    assert "situacao=sugerida&amp;pagina=2" in pagina  # a pagina mantem o filtro
    assert "Serie 054" in client.get(url + "&pagina=2").content.decode()

    client.post(
        reverse("dados:situacao_em_lote", urlconf=U),
        {"situacao": "aprovada", "todas_sugeridas": "1", "q": "Serie 00"},
    )
    assert Serie.objects.filter(situacao=Serie.Situacao.APROVADA).count() == 10
    client.post(
        reverse("dados:situacao_em_lote", urlconf=U),
        {"situacao": "aprovada", "todas_sugeridas": "1"},
    )
    assert not Serie.objects.filter(situacao=Serie.Situacao.SUGERIDA).exists()


def test_descricao_sem_html():
    from apps.dados.catalogo import _sem_html

    assert _sem_html("<P>O conceito &amp; a\n base</P>") == "O conceito & a base"
