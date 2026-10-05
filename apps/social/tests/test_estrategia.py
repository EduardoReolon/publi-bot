"""A estrategia: temas entre artigos, medicao por taxa (com alcance minimo),
teste pago A/B, fases por seguidores, recomendacao de impulso e a pagina."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.social import estrategia, experimentos, fontes, parametros, temas
from apps.social.models import (
    Abordagem,
    ConfiguracaoSocial,
    Destino,
    Post,
    ReferenciaDoNicho,
    Tema,
)

U = "core.urls_tenants"

A1, A2, A3 = (f"00000000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3))

PALAVRAS = ["cansaco", "sono", "anemia", "tireoide", "exame", "ferro", "obra", "cimento"]


def _vetor(texto: str) -> list[float]:
    t = texto.lower()
    return [float(p in t) + 0.01 for p in PALAVRAS]


def _artigo(pk: str, titulo: str, frases: list[str]) -> fontes.ArtigoParaRedes:
    return fontes.ArtigoParaRedes(
        id=pk,
        titulo=titulo,
        url=f"https://site/{pk}/",
        resumo="",
        palavra_chave="",
        frases=frases,
        texto=" ".join(frases),
        remote_id=pk,
    )


@pytest.fixture
def acervo(monkeypatch):
    artigos = {
        A1: _artigo(
            A1,
            "Anemia",
            [
                "O cansaco que nao passa pode vir da anemia por falta de ferro no sangue.",
                "Um exame simples de sangue mostra o nivel de ferro guardado no corpo.",
                "A obra da casa nova exige cimento de boa qualidade e paciencia do dono.",
            ],
        ),
        A2: _artigo(
            A2,
            "Sono",
            [
                "Dormir mal deixa um cansaco que nao passa mesmo com cafe pela manha.",
                "O sono curto por semanas aumenta o cansaco e piora a atencao no trabalho.",
            ],
        ),
        A3: _artigo(
            A3,
            "Tireoide",
            [
                "A tireoide lenta causa cansaco que nao passa e ganho de peso sem motivo.",
                "O exame de sangue da tireoide e simples e esclarece a maior parte dos casos.",
            ],
        ),
    }
    conversoes = {A1: 0, A2: 0, A3: 3}
    monkeypatch.setattr(fontes, "artigos_no_ar", lambda desde=None: list(artigos))
    monkeypatch.setattr(fontes, "artigo", lambda pk: artigos.get(str(pk)))
    monkeypatch.setattr(
        fontes, "sinais", lambda a: {"quase_la": False, "conversoes": conversoes[a.id]}
    )
    monkeypatch.setattr(
        fontes, "vetores", lambda textos, consulta=False: [_vetor(t) for t in textos]
    )
    monkeypatch.setattr(
        fontes, "negocio", lambda: {"dores": ["cansaco o dia todo"], "regioes": ["Curitiba"]}
    )
    monkeypatch.setattr(
        fontes,
        "demanda",
        lambda limite=300: [
            {"rotulo": "exame de sangue ferro", "volume": 5400, "vetor": _vetor("exame ferro")}
        ],
    )
    return artigos


@pytest.mark.django_db
def test_temas_atravessam_artigos_e_tem_nota_explicada(ambiente, acervo):
    assert temas.recalcular() >= 1
    melhor = Tema.objects.filter(ativo=True).order_by("-nota").first()
    assert len(melhor.artigos) >= 2  # nunca um tema de um artigo so
    assert "cansaco" in melhor.titulo.lower() or "exame" in melhor.titulo.lower()
    explicacao = " ".join(melhor.sinais["explicacao"])
    assert "artigos" in explicacao and "dor" in explicacao and "buscado" in explicacao
    assert set(melhor.sinais["parcelas"]) >= {"dores", "amplitude"}
    # A frase da obra, sozinha num artigo, nao vira tema.
    assert not Tema.objects.filter(titulo__icontains="cimento").exists()
    # Recalcular troca os temas sem acumular.
    temas.recalcular()
    assert Tema.objects.filter(ativo=True).count() == Tema.objects.count()


def test_taxa_respeita_o_alcance_minimo():
    post = Post(cliques=2, metricas={"alcance": 80, "salvos": 3, "compartilhamentos": 1})
    taxa = ConfiguracaoSocial.Metrica.TAXA
    assert experimentos.valor(post, taxa, alcance_minimo=100) is None  # inconclusivo
    assert experimentos.valor(post, taxa, alcance_minimo=50) == pytest.approx(6 / 80)
    # Rede sem alcance (Google, perfil pessoal): o clique.
    assert experimentos.valor(Post(cliques=4, metricas={}), taxa) == 4.0


@pytest.mark.django_db
def test_teste_pago_julga_o_par_e_fica_fora_da_mediana_organica(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    antes = timezone.now() - timedelta(days=5)
    a = Post.objects.create(
        destino=destino,
        motivo="teste",
        situacao="publicado",
        publicado_em=antes,
        impulsionado=True,
        impulso_em=antes,
        custo_impulso=Decimal("10"),
        metricas={"alcance": 1000, "salvos": 30},
    )
    b = Post.objects.create(
        destino=destino,
        motivo="teste",
        situacao="publicado",
        publicado_em=antes,
        impulsionado=True,
        impulso_em=antes,
        custo_impulso=Decimal("10"),
        metricas={"alcance": 1000, "salvos": 10},
        variante_de=a,
    )
    experimentos.avaliar(destino)
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.sucesso, b.sucesso) == (True, False)
    mesmo_mes = antes.date().month == timezone.localdate().month
    assert estrategia.gasto_do_mes() == (Decimal("20") if mesmo_mes else Decimal("0"))


@pytest.mark.django_db
def test_fase_por_seguidores_ajustavel_e_fixavel(ambiente):
    destino = Destino.objects.create(rede="instagram", nome="Insta", seguidores=250)
    assert estrategia.fase(destino) == "comeco"
    config = ConfiguracaoSocial.carregar()
    parametros.gravar(config, {"seguidores_tracao": "200", "alcance_minimo": "100"})
    assert config.parametros == {"seguidores_tracao": 200}  # igual ao padrao nao grava
    assert estrategia.fase(destino) == "tracao"
    destino.fase_manual = "escala"
    assert estrategia.fase(destino) == "escala"
    assert parametros.gravar(config, {"valor_teste": "-3"}) == [
        "Valor por versao num teste pago (R$)"
    ]


@pytest.mark.django_db
def test_impulso_no_comeco_e_teste_ab_e_depois_amplificar_o_melhor(ambiente, monkeypatch):
    monkeypatch.setattr(fontes, "negocio", lambda: {"regioes": ["Curitiba"]})
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    Tema.objects.create(titulo="Cansaco que nao passa", nota=0.9, artigos=[A1, A2])
    [rec] = estrategia.impulsos(destino)
    assert rec.tipo == "gerar_par" and rec.tema.titulo.startswith("Cansaco")

    um = Abordagem.objects.create(nome="A", instrucao="a")
    dois = Abordagem.objects.create(nome="B", instrucao="b")
    a = Post.objects.create(destino=destino, motivo="teste", situacao="publicado", abordagem=um)
    Post.objects.create(
        destino=destino, motivo="teste", situacao="aprovado", abordagem=dois, variante_de=a
    )
    [rec] = estrategia.impulsos(destino)
    assert rec.tipo == "teste" and len(rec.posts) == 2 and rec.valor == 10
    assert "Curitiba" in rec.publico

    # Tracao: so o post recente acima do normal da conta ganha recomendacao.
    destino.seguidores = 500
    destino.save()
    velho = timezone.now() - timedelta(days=20)
    for salvos in (1, 2, 3, 4):
        Post.objects.create(
            destino=destino,
            motivo="novo",
            situacao="publicado",
            publicado_em=velho,
            metricas={"alcance": 500, "salvos": salvos},
        )
    destaque = Post.objects.create(
        destino=destino,
        motivo="novo",
        situacao="publicado",
        artigo_titulo="Destaque",
        publicado_em=timezone.now() - timedelta(hours=60),
        metricas={"alcance": 500, "salvos": 40},
    )
    recs = estrategia.impulsos(destino)
    assert [r.tipo for r in recs] == ["amplificar"] and recs[0].posts == [destaque]
    assert recs[0].valor == 20 and recs[0].dias == 3


@pytest.mark.django_db
def test_seguidores_e_referencias_do_nicho_pela_api(ambiente):
    from apps.social import medicao
    from apps.social.redes.instagram import PublicadorInstagram

    def responder(pedido):
        caminho = pedido.url.path
        params = parse_qs(urlsplit(str(pedido.url)).query)
        if caminho.endswith("/ig_hashtag_search"):
            assert params["q"] == ["saude"]
            return httpx.Response(200, json={"data": [{"id": "H1"}]})
        if caminho.endswith("/H1/top_media"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "R1",
                            "caption": "5 sinais de cansaco",
                            "like_count": 900,
                            "comments_count": 40,
                            "media_type": "CAROUSEL_ALBUM",
                            "permalink": "https://ig/p/R1",
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"followers_count": 137})

    destino = Destino.objects.create(
        rede="instagram", nome="Insta", conta_id="17841", hashtags_de_referencia="#saude"
    )
    destino.gravar_credenciais({"access_token": "tok"})
    destino.expira_em = timezone.now() + timedelta(days=30)
    destino.save()
    http = httpx.Client(transport=httpx.MockTransport(responder))
    medicao.acompanhar_conta(destino, http=http)

    destino.refresh_from_db()
    assert destino.seguidores == 137 and destino.seguidores_historico[-1]["n"] == 137
    ref = ReferenciaDoNicho.objects.get()
    assert (ref.formato, ref.curtidas, ref.hashtag) == ("CAROUSEL_ALBUM", 900, "saude")
    assert estrategia.formatos_do_nicho(destino)[0]["formato"] == "CAROUSEL_ALBUM"
    # Uma vez por semana: a segunda chamada no mesmo dia nao busca de novo.
    medicao.acompanhar_conta(destino, http=http)
    assert ReferenciaDoNicho.objects.count() == 1
    assert isinstance(PublicadorInstagram(destino, http=http).seguidores(), int)


@pytest.mark.django_db
def test_pagina_de_estrategia_e_acoes(ambiente, acervo, monkeypatch):
    from apps.social import tasks

    _, _, client = ambiente
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    pagina = reverse("social:estrategia", urlconf=U)
    acao = reverse("social:acao_na_estrategia", urlconf=U)

    html = client.get(pagina).content.decode()
    assert "Fase: Comeco" in html and "Parametros da estrategia" in html

    client.post(acao, {"acao": "recalcular_temas", "destino": destino.pk})
    tema = Tema.objects.filter(ativo=True).order_by("-nota").first()
    assert tema is not None
    html = client.get(pagina + f"?destino={destino.pk}").content.decode()
    assert "Gerar par para teste A/B" in html

    client.post(acao, {"acao": "gerar_do_tema", "destino": destino.pk, "tema": tema.pk, "par": "1"})
    a, b = Post.objects.order_by("criado_em")
    assert b.variante_de == a and a.tema == tema and a.motivo == Post.Motivo.TESTE

    client.post(acao, {"acao": "seguidores", "destino": destino.pk, "seguidores": "1200"})
    destino.refresh_from_db()
    assert estrategia.fase(destino) == "crescimento"

    client.post(acao, {"acao": "impulso", "post": a.pk, "valor": "12,50"})
    a.refresh_from_db()
    assert a.impulsionado and a.custo_impulso == Decimal("12.50")

    client.post(acao, {"acao": "parametros", "orcamento_mensal": "150"})
    assert parametros.valor("orcamento_mensal") == 150
    assert "R$ 12,50 de R$ 150,00" in client.get(pagina).content.decode()


@pytest.mark.django_db
def test_instagram_posta_o_tema_com_material_de_varios_artigos(
    ambiente, acervo, monkeypatch, django_capture_on_commit_callbacks, settings, tmp_path
):
    import json

    from apps.social import escolha, redacao
    from apps.social.abordagens import garantir_abordagens

    settings.MEDIA_ROOT = tmp_path
    pedidos = []

    def executar(chave, variaveis, json_schema=None):
        pedidos.append(variaveis)
        return json.dumps(
            {
                "gancho": "Cansaco que nao passa?",
                "texto": "Pode ser ferro, sono ou tireoide.",
                "laminas": [
                    {"titulo": t, "texto": ""} for t in ("Cansado?", "Ferro", "Sono", "Tireoide")
                ],
            }
        )

    monkeypatch.setattr(fontes, "executar", executar)
    monkeypatch.setattr(fontes, "termos_a_evitar", lambda texto: [])
    monkeypatch.setattr(fontes, "endereco_publico", lambda caminho: caminho)
    garantir_abordagens()
    temas.recalcular()
    config = ConfiguracaoSocial.carregar()
    config.ligado = True
    config.save()
    destino = Destino.objects.create(rede="instagram", nome="Insta")

    from apps.social import tasks

    escritos = []
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: escritos.append(pk))
    with django_capture_on_commit_callbacks(execute=True):
        assert escolha.rodada() == 1
    post = Post.objects.get()
    assert post.tema is not None and post.motivo == Post.Motivo.TEMA
    assert post.por_que.startswith("Tema (nota")

    redacao.escrever(post)
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.RASCUNHO
    assert "Tema que aparece em" in pedidos[0]["resumo"]
    assert len(post.material["secoes"]) >= 2  # titulos dos artigos do tema
    assert destino.posts.count() == 1


@pytest.mark.django_db
def test_primeira_visita_cria_as_contas_e_mostra_os_passos(ambiente, monkeypatch):
    from apps.social import tasks

    _, _, client = ambiente
    pedidos = []
    monkeypatch.setattr(tasks.recalcular_temas_do_cliente, "delay", lambda s: pedidos.append(s))
    from django.core.cache import cache

    cache.clear()
    html = client.get(reverse("social:estrategia", urlconf=U), follow=True).content.decode()
    assert {d.rede for d in Destino.objects.all()} == {"instagram", "linkedin", "gmn"}
    assert ConfiguracaoSocial.carregar().ligado
    assert "Primeiros passos desta conta" in html and "Aprovar o primeiro post" in html
    assert len(pedidos) == 1  # temas calculados em segundo plano, uma vez
    client.get(reverse("social:estrategia", urlconf=U))
    assert len(pedidos) == 1 and Destino.objects.count() == 3  # nada se repete


@pytest.mark.django_db
def test_series_iniciais_dos_nichos_uma_vez_so(ambiente, monkeypatch):
    from apps.dados import catalogo
    from apps.dados.adaptadores import SerieEncontrada
    from apps.dados.models import Instituicao

    termos = []

    class Falso:
        pronto = True

        def procurar(self, termo, limite=20):
            termos.append(termo)
            return [SerieEncontrada(codigo=f"{termo}/1", titulo=termo)]

    monkeypatch.setattr(catalogo, "adaptador_de", lambda i: Falso())
    Instituicao.objects.get_or_create(sigla="BCB", defaults={"nome": "BC", "adaptador": "bcb"})
    Instituicao.objects.exclude(sigla="BCB").update(adaptador="")
    assert catalogo.explorar_nichos_iniciais() == 5
    assert "IPCA" in termos
    assert catalogo.explorar_nichos_iniciais() == 0  # ja tem series do catalogo
