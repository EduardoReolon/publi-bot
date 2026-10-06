"""Post que comenta uma noticia ou estudo (o PubliBot le os links) e os
recados de posicionamento em teste (em parte dos posts, medidos contra os
sem recado)."""

from __future__ import annotations

import json
import random
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.social import escolha, experimentos, fontes, proprio, redacao, tasks
from apps.social.models import ConfiguracaoSocial, Destino, Entrada, Post, Recado

U = "core.urls_tenants"

PAGINA = {
    "url": "https://jornal.exemplo.com/ia-estudo",
    "titulo": "Estudo mostra que IA chantageia em teste",
    "site": "Jornal Exemplo",
    "data": "2026-05-01",
    "texto": "Em um cenario artificial montado pelos pesquisadores, o modelo ameacou em "
    "84% das rodadas. Os autores dizem que o teste foi desenhado para provocar o pior caso.",
}


@pytest.mark.django_db
def test_comentario_le_os_links_e_o_post_cita_a_fonte(ambiente, monkeypatch):
    lidos = []
    monkeypatch.setattr(tasks.ler_links, "delay", lambda *a: lidos.append(a))
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    insta = Destino.objects.create(rede="instagram", nome="Insta")

    with pytest.raises(proprio.EntradaInvalida, match="https"):
        proprio.criar(
            tipo="comentario", texto="", arquivos=[], links=["jornal.com"], destinos=[insta.pk]
        )
    entrada = proprio.criar(
        tipo="comentario",
        texto="A manchete exagerou: era um teste montado.",
        arquivos=[],
        links=[PAGINA["url"], "https://fechado.exemplo.com/x", PAGINA["url"]],
        destinos=[insta.pk],
    )
    assert [r["url"] for r in entrada.referencias] == [
        PAGINA["url"],
        "https://fechado.exemplo.com/x",
    ]
    assert not entrada.posts.exists()  # espera a leitura

    def ler(url):
        if "fechado" in url:
            raise fontes.LinkIlegivel("HTTP 403")
        return PAGINA

    monkeypatch.setattr(fontes, "ler_link", ler)
    proprio.ler_referencias(entrada)
    entrada.refresh_from_db()
    assert entrada.referencias[0]["site"] == "Jornal Exemplo"
    assert entrada.referencias[1]["erro"] == "HTTP 403"
    post = entrada.posts.get()

    pedidos = []

    def executar(chave, variaveis, **_):
        pedidos.append(variaveis)
        return json.dumps(
            {
                "gancho": "84% das vezes, num teste montado",
                "texto": "Segundo o Jornal Exemplo, 84% das rodadas. Link na bio.",
                "laminas": [{"titulo": "O teste", "texto": "foi montado"}] * 4,
            }
        )

    monkeypatch.setattr(fontes, "executar", executar)
    redacao.escrever(post)
    post.refresh_from_db()
    ajuste = pedidos[0]["ajuste"]
    assert "COMENTARIO" in ajuste and "de onde vem cada informacao" in ajuste
    assert not any("numero" in a for a in post.avisos)  # o 84% esta na referencia
    assert any("link nao lido" in a and "403" in a for a in post.avisos)
    assert "Jornal Exemplo" in proprio.como_artigo(entrada).texto


@pytest.mark.django_db
def test_tela_tem_o_campo_de_links(ambiente, monkeypatch):
    _, _, client = ambiente
    monkeypatch.setattr(tasks.ler_links, "delay", lambda *a: None)
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    pagina = client.get(reverse("social:novo_post", urlconf=U)).content.decode()
    assert 'name="links"' in pagina and "Noticia ou estudo comentado" in pagina
    resposta = client.post(
        reverse("social:novo_post", urlconf=U),
        {"tipo": "comentario", "links": PAGINA["url"], "destinos": [insta.pk], "artigo": ""},
    )
    assert resposta.status_code == 302
    assert Entrada.objects.get().referencias == [{"url": PAGINA["url"]}]


# -- Recados ------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_recado_entra_so_em_parte_dos_posts_e_vai_no_pedido(ambiente, monkeypatch):
    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk: None)
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    Recado.objects.create(nome="Checado", instrucao="aqui tudo tem fonte", por_cento=30)
    Recado.objects.create(nome="So LinkedIn", instrucao="x", por_cento=100, redes=["linkedin"])

    rng = random.Random(7)  # noqa: S311
    sorteados = [experimentos.sortear_recado(insta, rng=rng) for _ in range(1000)]
    com = [r for r in sorteados if r is not None]
    assert {r.nome for r in com} == {"Checado"}  # o do LinkedIn nao vale aqui
    assert 250 < len(com) < 350

    monkeypatch.setattr(
        experimentos, "sortear_recado", lambda d: Recado.objects.get(nome="Checado")
    )
    artigo = fontes.ArtigoParaRedes(
        id=None, titulo="Cansaco", url="https://site.exemplo.org/a", resumo="r", palavra_chave=""
    )
    par = escolha.sugerir(insta, artigo, Post.Motivo.TESTE, "teste", versoes=2)
    assert all(p.extras["recado"]["nome"] == "Checado" for p in par)  # o par igual

    pedidos = []

    def executar(chave, variaveis, **_):
        pedidos.append(variaveis)
        return json.dumps({"gancho": "Oi", "texto": "Texto.", "laminas": []})

    monkeypatch.setattr(fontes, "executar", executar)
    monkeypatch.setattr(fontes, "artigo", lambda _id: artigo)
    redacao.escrever(par[0])
    par[0].refresh_from_db()
    assert "RECADO DE POSICIONAMENTO" in pedidos[0]["ajuste"]
    assert "aqui tudo tem fonte" in pedidos[0]["ajuste"]
    assert par[0].extras["recado"]["nome"] == "Checado"  # continua marcado depois de escrito


@pytest.mark.django_db
def test_quadro_compara_com_e_sem_recado(ambiente):
    _, _, client = ambiente
    config = ConfiguracaoSocial.carregar()
    config.metrica = ConfiguracaoSocial.Metrica.TAXA
    config.save()
    insta = Destino.objects.create(rede="instagram", nome="Insta")
    recado = Recado.objects.create(
        nome="Checado", instrucao="fonte", criado_em=timezone.now() - timedelta(days=60)
    )
    for i in range(12):
        com = i % 2 == 0
        Post.objects.create(
            destino=insta,
            motivo=Post.Motivo.NOVO,
            situacao=Post.Situacao.PUBLICADO,
            publicado_em=timezone.now() - timedelta(days=i + 1),
            metricas={"alcance": 1000, "salvos": 60 if com else 30},
            extras={"recado": {"id": str(recado.pk), "nome": recado.nome}} if com else {},
        )
    (linha,) = experimentos.quadro_de_recados(insta)
    assert linha["com"] == 6 and linha["sem"] == 6
    assert linha["diferenca"] == 100 and linha["conclusao"] == "rende mais com o recado"

    pagina = client.get(reverse("social:inicio", urlconf=U), {"aba": "funciona"}).content.decode()
    assert "Recados de posicionamento" in pagina and "rende mais com o recado" in pagina
    configurar = client.get(reverse("social:inicio", urlconf=U), {"aba": "configurar"})
    assert "Novo recado" in configurar.content.decode()
    resposta = client.post(
        reverse("social:novo_recado", urlconf=U),
        {"nome": "Sem sensacionalismo", "instrucao": "sem exagero", "por_cento": 25, "ativo": "on"},
    )
    assert (
        resposta.status_code == 302 and Recado.objects.filter(nome="Sem sensacionalismo").exists()
    )
