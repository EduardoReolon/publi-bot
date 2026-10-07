"""Caixa de ideias: da ideia solta a pauta investigada, e a trava que impede o
DISCURSO ("o que se diz") de virar evidencia."""

from __future__ import annotations

import json
import types

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.content.models import Topic
from apps.ideias import tasks
from apps.ideias.models import Ideia
from apps.knowledge.models import CandidatoDeFonte, Document
from apps.radar.provedores import ItemDeBusca, ResultadoDeBusca

U = "core.urls_tenants"

LEITURA = {
    "titulo": "Falta mao de obra para usar IA?",
    "afirmacao": "O que trava a IA agora e a falta de mao de obra para usa-la.",
    "tese": "Falta profissional da area que saiba conferir a saida, nao operador de IA.",
    "frentes": [
        {
            "nome": "O que o jornal diz",
            "papel": "discurso",
            "descricao": "Falta mao de obra para IA.",
            "buscas": ["falta mao de obra ia"],
            "links": ["https://g1.exemplo.com/ia-mao-de-obra", "https://inventado.com/x"],
        },
        {
            "nome": "Especialista confere",
            "papel": "a_favor",
            "buscas": ["especialista conferir saida ia"],
            "estudos": True,
        },
        {
            "nome": "Produtividade",
            "papel": "alternativa",
            "descricao": "A produtividade subiu e a demanda por especialistas cresceu.",
            "buscas": ["produtividade ia demanda especialistas"],
            "estudos": False,
            "links": ["https://estudo.org/produtividade"],
        },
    ],
    "onde": "os_dois",
}

HTML_DA_MATERIA = (
    b"<html><body><article><h1>Falta gente para IA</h1><p>"
    + b"Segundo a pesquisa, 60% das empresas relatam falta de profissionais. " * 20
    + b'</p><a href="https://www.ibge.gov.br/estatisticas/ia.html">pesquisa do IBGE</a>'
    b'<a href="https://outro.com/noticia">outra noticia</a>'
    b'<a href="https://instituto.org/relatorio-ia.pdf">relatorio completo</a>'
    b"</article></body></html>"
)


@pytest.fixture
def modelo(monkeypatch):
    monkeypatch.setattr(
        "apps.content.inference.executar_prompt",
        lambda **kw: types.SimpleNamespace(texto=json.dumps(LEITURA)),
    )


@pytest.fixture
def buscador(monkeypatch):
    feitas = []

    def buscar(consulta, finalidade=None):
        feitas.append(consulta)
        n = len(feitas)
        return ResultadoDeBusca(
            provedor="teste",
            resultados=[ItemDeBusca(url=f"https://jornal{n}.com/m{n}", titulo=f"Materia {n}")],
        )

    monkeypatch.setattr("apps.radar.provedores.buscar", buscar)
    monkeypatch.setattr(
        "apps.knowledge.academicos.buscar_para_pauta", lambda pauta, limite, consulta="": []
    )
    return feitas


@pytest.mark.django_db
def test_ideia_vira_pauta_com_debate_e_busca_os_dois_lados(ambiente, modelo, buscador, monkeypatch):
    _, _, client = ambiente
    disparadas = []
    monkeypatch.setattr(tasks.processar_ideia, "delay", disparadas.append)
    resposta = client.post(
        reverse("ideias:inicio", urlconf=U),
        {
            "texto": "Vi no jornal que falta mao de obra para IA. Eu acho que...",
            "links": "https://g1.exemplo.com/ia-mao-de-obra\nhttps://estudo.org/produtividade",
        },
    )
    assert resposta.status_code == 302
    ideia = Ideia.objects.get()
    assert ideia.links == [
        "https://g1.exemplo.com/ia-mao-de-obra",
        "https://estudo.org/produtividade",
    ]

    tasks.processar_ideia(str(ideia.pk))
    ideia.refresh_from_db()
    assert ideia.situacao == Ideia.Situacao.CURADORIA and ideia.onde == "os_dois"
    pauta = ideia.pauta
    assert pauta.origin == Topic.Origin.IDEIA and pauta.status == Topic.Status.SUGGESTED
    assert pauta.debate["tese"].startswith("Falta profissional")

    # Sem frente "contra" na leitura, o PubliBot acrescenta uma.
    frentes = {f["nome"]: f for f in ideia.leitura["frentes"]}
    assert frentes["Contra a tese"]["papel"] == "contra"
    assert frentes["O que o jornal diz"]["links"] == ["https://g1.exemplo.com/ia-mao-de-obra"]
    assert [f["nome"] for f in pauta.debate["frentes"]][:3] == [
        "O que o jornal diz",
        "Especialista confere",
        "Produtividade",
    ]

    candidatos = CandidatoDeFonte.objects.filter(pauta=pauta)
    discurso = candidatos.filter(papel=CandidatoDeFonte.Papel.DISCURSO)
    # O link do jornal (atribuido ao discurso) e a busca do discurso; o link do
    # estudo foi para a frente alternativa, como EVIDENCIA.
    assert {c.url for c in discurso} >= {"https://g1.exemplo.com/ia-mao-de-obra"}
    assert discurso.count() == 2 and not candidatos.filter(url__contains="inventado")
    estudo = candidatos.get(url="https://estudo.org/produtividade")
    assert estudo.papel == "" and estudo.metricas == {
        "frente": "Produtividade",
        "lado": "alternativa",
    }
    assert all(c.metricas.get("frente") for c in candidatos)
    assert ideia.buscas["frentes"]["Contra a tese"] == 1

    pagina = client.get(reverse("ideias:inicio", urlconf=U)).content.decode()
    assert "Produtividade" in pagina and "contra a sua ideia" in pagina
    assert "Fazer a curadoria" in pagina


@pytest.mark.django_db
def test_discurso_nunca_vira_documento_e_segue_as_citacoes(ambiente, monkeypatch):
    from apps.content.debate import bloco
    from apps.knowledge.fontes_web import aprovar
    from apps.knowledge.models import DocumentCategory
    from apps.knowledge.provisorias import acolher

    _, _, client = ambiente
    pauta = Topic.objects.create(
        title="Falta mao de obra para IA?",
        debate={"afirmacao": "Falta mao de obra.", "tese": "Falta especialista.", "linhas": []},
    )
    candidato = CandidatoDeFonte.objects.create(
        url="https://jornal.com/ia",
        titulo="Falta gente para IA",
        dominio="jornal.com",
        pauta=pauta,
        papel=CandidatoDeFonte.Papel.DISCURSO,
    )
    assert acolher(candidato) is False  # nem provisorio
    monkeypatch.setattr(
        "apps.knowledge.web.baixar",
        lambda url: (HTML_DA_MATERIA, url, "text/html"),
    )
    monkeypatch.setattr("apps.knowledge.web.conferir_destino", lambda url: None)

    # Mesmo pelo caminho de evidencia (aprovar com categoria), o discurso nao vira documento.
    categoria = DocumentCategory.objects.first() or DocumentCategory.objects.create(name="Imprensa")
    aprovado = aprovar(candidato, categoria=categoria)
    assert aprovado.situacao == CandidatoDeFonte.Situacao.APROVADO
    assert aprovado.documento is None and not Document.objects.filter(source_url=candidato.url)
    assert "60% das empresas" in aprovado.texto_extraido

    # A fonte primaria citada vira sugestao de EVIDENCIA; a outra noticia, nao.
    citadas = CandidatoDeFonte.objects.filter(pauta=pauta, papel=CandidatoDeFonte.Papel.EVIDENCIA)
    assert {c.url for c in citadas} == {
        "https://www.ibge.gov.br/estatisticas/ia.html",
        "https://instituto.org/relatorio-ia.pdf",
    }

    texto = bloco(pauta)
    assert "NAO sao evidencia" in texto and "60% das empresas" in texto
    assert "[D" not in texto  # nada que pareca marcador de citacao
    assert bloco(Topic.objects.create(title="Pauta comum")) == ""

    # Na curadoria: o discurso tem os proprios botoes.
    outro = CandidatoDeFonte.objects.create(
        url="https://blog.com/x", titulo="Post", pauta=pauta, papel="discurso"
    )
    pagina = client.get(
        reverse("knowledge:fontes_sugeridas", urlconf=U), {"pauta": pauta.pk}
    ).content.decode()
    assert "Aprovar como discurso" in pagina and "E evidencia, na verdade" in pagina
    client.post(
        reverse("knowledge:decidir_candidato", args=[outro.pk], urlconf=U),
        {"decisao": "evidencia"},
    )
    outro.refresh_from_db()
    assert outro.papel == CandidatoDeFonte.Papel.EVIDENCIA and outro.situacao == "pendente"


@pytest.mark.django_db
def test_geracao_recebe_o_debate(ambiente):
    from apps.content.flows import _com_debate
    from apps.content.models import Article

    pauta = Topic.objects.create(
        title="T",
        debate={
            "afirmacao": "Dizem X.",
            "tese": "Acho Y.",
            "frentes": [{"nome": "Ou Z", "papel": "alternativa", "descricao": "Ou Z."}],
        },
    )
    artigo = Article.objects.create(title="T", topic=pauta)
    texto = _com_debate("Tese das fontes.", artigo)
    assert texto.startswith("Tese das fontes.") and "Dizem X." in texto
    assert "Frente 'Ou Z' — outra explicacao: Ou Z." in texto
    assert "conclusao sai das FONTES" in texto


@pytest.mark.django_db
def test_audio_e_levar_as_redes(ambiente, modelo, buscador, monkeypatch, settings, tmp_path):
    # Pelo registro do Django: a caixa de ideias nao importa o modulo de redes.
    from django.apps import apps as registro
    from django.utils.module_loading import import_string

    proprio = import_string("apps.social.proprio")
    Destino = registro.get_model("social", "Destino")

    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    monkeypatch.setattr(tasks.processar_ideia, "delay", lambda pk: None)
    client.post(
        reverse("ideias:inicio", urlconf=U),
        {"audio": SimpleUploadedFile("ideia.ogg", b"OggS...", content_type="audio/ogg")},
    )
    ideia = Ideia.objects.get()
    monkeypatch.setattr(
        "apps.knowledge.extraction.transcrever_audio",
        lambda *a, **k: ([(0.0, "Dizem que falta mao de obra para IA.")], 3.0),
    )
    tasks.processar_ideia(str(ideia.pk))
    ideia.refresh_from_db()
    assert "falta mao de obra" in ideia.transcricao and ideia.pauta is not None

    CandidatoDeFonte.objects.filter(pauta=ideia.pauta, papel="discurso").update(
        situacao="aprovado", texto_extraido="A materia diz que falta mao de obra."
    )
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    levados = []
    monkeypatch.setattr(proprio, "levar", levados.append)
    client.post(
        reverse("ideias:acao", args=[ideia.pk], urlconf=U),
        {"acao": "redes", "destinos": [insta.pk]},
    )
    (entrada,) = levados
    assert entrada.tipo == "comentario"
    assert all(r["papel"] == "discurso" for r in entrada.referencias)
    material = proprio.como_artigo(entrada).texto
    assert "DISCURSO, NAO E EVIDENCIA" in material


@pytest.mark.django_db
def test_modo_investigativo_em_pauta_que_ja_existe(ambiente, modelo, buscador, monkeypatch):
    from apps.content.models import Article

    _, _, client = ambiente
    monkeypatch.setattr(tasks.processar_ideia, "delay", lambda pk: None)
    pauta = Topic.objects.create(title="IA e mercado de trabalho", briefing="Para gestores.")
    artigo = Article.objects.create(
        title="IA e mercado de trabalho", topic=pauta, consensus=Article.Consensus.CONFLICT
    )

    tela = client.get(reverse("content:pauta", args=[pauta.pk], urlconf=U)).content.decode()
    assert "Ligar o modo investigativo" in tela

    resposta = client.post(
        reverse("ideias:investigar_pauta", args=[pauta.pk], urlconf=U),
        {"texto": "Dizem que falta mao de obra. https://g1.exemplo.com/ia", "voltar": "/x/"},
    )
    assert resposta.status_code == 302 and resposta["Location"] == "/x/"
    ideia = Ideia.objects.get()
    assert ideia.pauta == pauta and ideia.links == ["https://g1.exemplo.com/ia"]
    assert "Pauta: IA e mercado de trabalho" in ideia.texto and "Para gestores." in ideia.texto

    tasks.processar_ideia(str(ideia.pk))
    pauta.refresh_from_db()
    assert pauta.title == "IA e mercado de trabalho"  # o titulo da pauta nao muda
    assert pauta.debate["frentes"] and Topic.objects.count() == 1

    revisao = client.get(reverse("content:revisar", args=[artigo.pk], urlconf=U)).content.decode()
    assert "as fontes divergem" in revisao and "Produtividade" in revisao

    # Na pauta: a arvore (Sobre a pauta no topo), as frentes com os cards de
    # curadoria e o link para a curadoria filtrada pela frente.
    tela = client.get(reverse("content:pauta", args=[pauta.pk], urlconf=U)).content.decode()
    assert tela.index('id="sobre"') < tela.index('id="investigar"')
    assert "alimenta o A e o B" in tela and 'id="frente-1"' in tela
    assert 'name="ancora" value="#frente-' in tela
    assert "frente=Produtividade" in tela

    curadoria = client.get(
        reverse("knowledge:fontes_sugeridas", urlconf=U) + f"?pauta={pauta.pk}&frente=Produtividade"
    ).content.decode()
    assert "Frente: Produtividade" in curadoria and "Todas as frentes desta pauta" in curadoria
    da_frente = CandidatoDeFonte.objects.filter(pauta=pauta, metricas__frente="Produtividade")
    assert da_frente and all(c.url in curadoria for c in da_frente)
    assert "g1.exemplo.com" not in curadoria


@pytest.mark.django_db
def test_ideia_preparada_pela_outra_ia(
    ambiente, buscador, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.ideias.investigacao import pedido_para_outra_ia

    _, _, client = ambiente
    disparadas = []
    monkeypatch.setattr(
        tasks.processar_ideia, "delay", lambda pk: pytest.fail("nao chama o modelo")
    )
    monkeypatch.setattr(tasks.buscar_ideia, "delay", disparadas.append)
    client.post(
        reverse("ideias:inicio", urlconf=U),
        {"texto": "Dizem que falta mao de obra para IA.", "modo": "outra_ia"},
    )
    ideia = Ideia.objects.get()
    assert ideia.situacao == Ideia.Situacao.OUTRA_IA and ideia.pauta is None

    pedido = pedido_para_outra_ia(ideia)
    assert "PESQUISAR NA WEB" in pedido and "Dizem que falta mao de obra" in pedido
    tela = client.get(reverse("ideias:inicio", urlconf=U)).content.decode()
    assert "Copiar o pedido" in tela and "Cole aqui a resposta" in tela

    # Resposta quebrada: avisa e nao muda nada.
    client.post(
        reverse("ideias:acao", args=[ideia.pk], urlconf=U), {"acao": "resposta", "resposta": "oi"}
    )
    ideia.refresh_from_db()
    assert ideia.situacao == Ideia.Situacao.OUTRA_IA and not disparadas

    resposta = "Aqui esta:\n```json\n" + json.dumps(LEITURA) + "\n```\nBoa sorte!"
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("ideias:acao", args=[ideia.pk], urlconf=U),
            {"acao": "resposta", "resposta": resposta},
        )
    ideia.refresh_from_db()
    assert ideia.leitura["pela_outra_ia"] and disparadas == [str(ideia.pk)]
    # Os links que a outra IA achou entram (para a curadoria), mesmo sem estar na ideia.
    frentes = {f["nome"]: f for f in ideia.leitura["frentes"]}
    assert "https://estudo.org/produtividade" in frentes["Produtividade"]["links"]

    tasks.buscar_ideia(str(ideia.pk))
    ideia.refresh_from_db()
    assert ideia.situacao == Ideia.Situacao.CURADORIA and ideia.pauta.debate["frentes"]
    achado = CandidatoDeFonte.objects.get(url="https://estudo.org/produtividade")
    assert "outra IA" in achado.trecho


@pytest.mark.django_db
def test_dossie_para_o_veredito_leva_o_conteudo_e_o_meio(ambiente, modelo, buscador, monkeypatch):
    from apps.ideias import veredito

    _, _, client = ambiente
    monkeypatch.setattr(tasks.processar_ideia, "delay", lambda pk: None)
    client.post(
        reverse("ideias:inicio", urlconf=U),
        {"texto": "Falta mao de obra para IA?", "links": "https://g1.exemplo.com/ia-mao-de-obra"},
    )
    ideia = Ideia.objects.get()
    tasks.processar_ideia(str(ideia.pk))
    ideia.refresh_from_db()
    pauta = ideia.pauta

    # Um video aprovado como discurso (com texto) e uma pagina recusada.
    candidatos = CandidatoDeFonte.objects.filter(pauta=pauta)
    video = candidatos.filter(papel=CandidatoDeFonte.Papel.DISCURSO).first()
    video.tipo, video.canal_nome = CandidatoDeFonte.Tipo.VIDEO, "Jornal da TV"
    video.situacao = CandidatoDeFonte.Situacao.APROVADO
    video.texto_extraido = "Falta quem saiba usar a IA, diz o apresentador. " * 400
    video.save()
    recusada = candidatos.exclude(pk=video.pk).first()
    recusada.situacao = CandidatoDeFonte.Situacao.RECUSADO
    recusada.save()

    texto = veredito.dossie(pauta)
    assert "VEREDITO" in texto and "POR MEIO E PUBLICO" in texto
    assert "A SUSPEITA DO AUTOR: Falta profissional" in texto
    assert "video (fala: TV, YouTube, podcast) — canal Jornal da TV" in texto
    assert "papel: o que se diz · aprovada pelo autor" in texto
    assert "ainda nao curada" in texto and "so o resumo da busca" in texto
    assert recusada.url not in texto
    # O texto longo e cortado, nao despejado inteiro.
    assert texto.count("diz o apresentador") < 400 and "[...]" in texto

    # Na pauta: carregado so ao abrir, e baixar o texto que falta.
    tela = client.get(reverse("content:pauta", args=[pauta.pk], urlconf=U)).content.decode()
    url = reverse("ideias:dossie", args=[pauta.pk], urlconf=U)
    assert f'data-carregar="{url}"' in tela and "Copiar o dossie" in tela
    assert client.get(url).content.decode() == texto

    monkeypatch.setattr("apps.knowledge.web.texto_da_pagina", lambda url: f"Texto inteiro de {url}")
    assert veredito.capturar_textos(pauta) >= 1
    assert "Texto inteiro de https://" in veredito.dossie(pauta)
