"""Cuidados de linguagem (estigma, temas sensiveis), regras dos conselhos de
saude e texto alternativo das imagens: em artigos e redes."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.editorial.linguagem import CONSELHOS_DE_SAUDE, cuidados, instrucoes_sensiveis
from apps.editorial.services import conferir_texto, texto_do_guia
from tests.test_interface import ambiente  # noqa: F401

U = "core.urls_tenants"


def test_linguagem_que_estigmatiza_vira_aviso_com_a_troca():
    achados = cuidados("O paciente diabético, portador de HIV, sofre de ansiedade.")
    por_termo = {a.expressao: a for a in achados}
    assert por_termo["portador de"].sugestao == "pessoa com"
    assert "diabetico" in por_termo and "sofre de" in por_termo
    # Palavra inteira: "diabetes" nao e "diabetico", e "deficiencia" nao e "deficiente".
    assert not cuidados("Pessoa com diabetes e pessoa com deficiencia.")


def test_suicidio_pede_o_cvv_e_nao_aceita_metodo():
    sem_cvv = cuidados("Falar sobre suicidio salva vidas.")
    assert any("CVV" in a.motivo for a in sem_cvv)
    com_cvv = cuidados("Falar sobre suicidio salva vidas. CVV, ligue 188.")
    assert not com_cvv
    metodo = cuidados("Ele tentou suicidio com veneno. Ligue 188.")
    assert any("metodo" in a.motivo for a in metodo)
    assert "CVV" in instrucoes_sensiveis("Prevencao do suicidio na adolescencia")
    assert instrucoes_sensiveis("Dor nas costas") == ""


def test_transtorno_alimentar_sem_peso_nem_caloria():
    assert any("calorias" in a.motivo for a in cuidados("Na anorexia, comer 500 calorias..."))
    assert not cuidados("A anorexia tem tratamento.")


@pytest.mark.django_db
def test_conferencia_do_artigo_avisa_sem_bloquear(ambiente):  # noqa: F811
    from apps.editorial.models import EditorialProfile

    perfil = EditorialProfile.carregar()
    conferencia = conferir_texto("Todo deficiente tem direito.", perfil)
    assert conferencia.cuidados and not conferencia.bloqueia and not conferencia.vazia

    guia = texto_do_guia(perfil, chave="section_draft")
    assert "pessoa antes da condicao" in guia and "CVV" in guia


@pytest.mark.django_db
def test_regras_dos_conselhos_no_guia_de_saude(ambiente):  # noqa: F811
    from apps.editorial.models import EditorialProfile
    from apps.editorial.services import aplicar_modo

    _, _, client = ambiente
    perfil = EditorialProfile.carregar()
    aplicar_modo(perfil, "saude")
    assert {
        "termo": "resultado garantido",
        "troca": "",
        "motivo": "garantia de resultado (CFM/CFO)",
    } in perfil.termos
    # Conta antiga, guia de saude sem os termos novos: o botao acrescenta so o que falta.
    perfil.termos = [t for t in perfil.termos if t["termo"] != "desconto"]
    perfil.save()
    pagina = client.get(reverse("editorial:guia", urlconf=U)).content.decode()
    assert "Adicionar ao meu guia" in pagina and '"desconto"' in pagina
    client.post(reverse("editorial:conselhos", urlconf=U))
    perfil.refresh_from_db()
    termos = [t["termo"] for t in perfil.termos]
    assert termos.count("desconto") == 1 and len(termos) == 4 + len(CONSELHOS_DE_SAUDE)
    assert conferir_texto("Promocao de setembro!", perfil).bloqueia


@pytest.mark.django_db
def test_post_das_redes_avisa_e_imagem_leva_texto_alternativo(ambiente, monkeypatch):  # noqa: F811
    import json

    import httpx

    from apps.social import fontes, redacao
    from apps.social.laminas import _alt_da_lamina
    from apps.social.models import Destino, Post
    from apps.social.redes.base import ImagemPublica
    from apps.social.redes.instagram import PublicadorInstagram
    from apps.social.tests.test_apis import Rede, _conectado

    insta = Destino.objects.create(rede="instagram", nome="Insta")
    artigo = fontes.ArtigoParaRedes(
        id="00000000-0000-0000-0000-0000000000a1",
        titulo="Prevencao do suicidio",
        url="https://site.exemplo.org/a",
        resumo="r",
        palavra_chave="",
        texto="Prevencao do suicidio. CVV 188.",
    )
    monkeypatch.setattr(fontes, "artigo", lambda _id: artigo)
    pedidos = []

    def executar(chave, variaveis, **_):
        pedidos.append(variaveis)
        return json.dumps(
            {"gancho": "Oi", "texto": "Todo doente mental precisa de ajuda.", "laminas": []}
        )

    monkeypatch.setattr(fontes, "executar", executar)
    post = Post.objects.create(
        destino=insta, artigo_id="00000000-0000-0000-0000-0000000000a1", motivo=Post.Motivo.NOVO
    )
    redacao.escrever(post)
    post.refresh_from_db()
    assert "CVV" in pedidos[0]["ajuste"] and "#SaudeMental" in pedidos[0]["ajuste"]
    assert any("doente mental" in a for a in post.avisos)

    laminas = [{"titulo": "Sinais", "texto": "isolamento"}]
    assert _alt_da_lamina(laminas, 1, "Link na bio") == "Sinais isolamento"
    assert _alt_da_lamina(laminas, 2, "Link na bio") == "Link na bio"

    destino = _conectado("instagram", "ig1")
    enviados = []

    def media(pedido):
        enviados.append(dict(pedido.url.params))
        return httpx.Response(200, json={"id": "c1"})

    rede = Rede(
        {
            "POST graph.facebook.com/v21.0/ig1/media_publish": httpx.Response(
                200, json={"id": "p1"}
            ),
            "POST graph.facebook.com/v21.0/ig1/media": media,
            "GET graph.facebook.com/v21.0/c1": httpx.Response(
                200, json={"status_code": "FINISHED"}
            ),
            "GET graph.facebook.com/v21.0/p1": httpx.Response(
                200, json={"permalink": "https://i/p"}
            ),
        }
    )
    publicador = PublicadorInstagram(destino, http=rede.cliente())
    publicador.esperar = lambda s: None
    publicador.publicar(
        Post(destino=destino),
        "texto",
        [ImagemPublica(url="https://x/1.png", caminho="c", alt="Sinais")],
    )
    assert enviados[0].get("alt_text") == "Sinais"
