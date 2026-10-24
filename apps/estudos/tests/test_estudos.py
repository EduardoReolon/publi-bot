"""Estudos de mercado: area por raio, rodadas com a outra IA, estimativa pelo
estado x populacao, valor real por cidade, palavra e funil."""

from __future__ import annotations

import pytest
from django.db import connection
from django.urls import reverse

from apps.estudos import coleta, funil, municipios, pedido
from apps.estudos.models import Estudo, Rodada
from apps.radar import provedores
from apps.radar.models import LocalDisponivel

U = "core.urls_tenants"
CASCAVEL, TOLEDO = 4104808, 4127700
ESTADO, LOCAL_CASCAVEL = 21000, 1001


def test_raio_em_volta_de_cascavel_e_leitura_do_sidra():
    perto = {m.codigo: d for m, d in municipios.no_raio(CASCAVEL, 50)}
    assert perto[CASCAVEL] == 0 and 35 < perto[TOLEDO] < 45
    assert all(d <= 50 for d in perto.values())
    assert municipios.procurar("cascavel")[0].nome == "Cascavel"
    dados = [
        {"D1C": "Município (Código)", "V": "Valor"},
        {"D1C": str(CASCAVEL), "V": "348051"},
        {"D1C": str(TOLEDO), "V": "..."},
    ]
    assert municipios._ler_sidra(dados) == {CASCAVEL: 348051}


def test_tendencia_sazonalidade_e_pico():
    meses = [[2024, m, 100] for m in range(1, 13)] + [[2025, m, 120] for m in range(1, 13)]
    meses[11][2] = meses[23][2] = 300  # dezembro forte
    assert funil.tendencia(meses) == pytest.approx(15.7, abs=0.1)
    assert funil.pico(meses) == "dez"
    assert funil.sazonalidade(meses)[11] > 2


@pytest.fixture
def estudo_pronto(ambiente, monkeypatch):
    """Estudo em Cascavel (raio de 50 km), com o tenant marcado e superusuario."""
    tenant, usuario, client = ambiente
    usuario.is_superuser = True
    usuario.save()
    tenant.estudos = True
    tenant.save()
    connection.tenant.estudos = True
    LocalDisponivel.objects.create(
        codigo=ESTADO, nome="Parana", tipo="State", pais="BR", busca="parana"
    )
    LocalDisponivel.objects.create(
        codigo=LOCAL_CASCAVEL,
        nome="Cascavel, Parana",
        tipo="City",
        pais="BR",
        busca="cascavel, parana",
    )
    # O IBGE: todo municipio do PR com 10 mil habitantes, Cascavel com 350 mil.
    do_pr = [c for c, m in municipios.todos().items() if m.uf == 41]
    sidra = [{"D1C": "Município (Código)", "V": "Valor"}] + [
        {"D1C": str(c), "V": "350000" if c == CASCAVEL else "10000"} for c in do_pr
    ]

    class Resposta:
        def raise_for_status(self):
            pass

        def json(self):
            return sidra

    monkeypatch.setattr(municipios.httpx, "get", lambda url, **k: Resposta())
    pedidos = []

    def post(caminho, corpo, contas):
        pedidos.append((caminho, corpo[0]))
        local = corpo[0]["location_code"]
        if "keywords_for_site" in caminho:
            itens = [{"keyword": "simulador de consorcio", "search_volume": 800, "cpc": 1.0,
                      "competition_index": 20, "monthly_searches": []}]  # fmt: skip
        elif "keywords_for_keywords" in caminho:
            itens = [{"keyword": "consorcio de imoveis", "search_volume": 5000, "cpc": 2.0,
                      "competition_index": 40, "monthly_searches": []}]  # fmt: skip
        else:
            volume = 10000 if local == ESTADO else 30
            itens = [
                {"keyword": p, "search_volume": None if p == "rara" and local != ESTADO else volume,
                 "cpc": 1.5, "competition_index": 70,
                 "monthly_searches": [{"year": 2025, "month": m, "search_volume": volume}
                                      for m in range(1, 13)]}
                for p in corpo[0]["keywords"]
            ]  # fmt: skip
        return {"cost": 0.075, "tasks": [{"result": itens}]}

    monkeypatch.setattr(provedores, "_post_dataforseo", post)
    monkeypatch.setattr(
        "apps.estudos.tasks.buscar_rodada.delay",
        lambda rodada_id, trabalho=None: __import__(
            "apps.estudos.tasks"
        ).estudos.tasks.buscar_rodada(rodada_id, trabalho),
    )
    return client, pedidos


@pytest.mark.django_db
def test_rodadas_com_a_outra_ia_estimativa_e_valor_real(estudo_pronto):
    client, pedidos = estudo_pronto
    inicio = reverse("estudos:inicio", urlconf=U)
    assert "Estudos" in client.get(reverse("accounts:painel", urlconf=U)).content.decode()
    client.post(
        inicio,
        {
            "nome": "Empresa X",
            "descricao": "Vende consorcio.",
            "cidade": "Cascavel (PR)",
            "raio_km": "50",
        },
    )
    estudo = Estudo.objects.get()
    assert estudo.cidade == CASCAVEL
    tela = reverse("estudos:estudo", args=[estudo.pk], urlconf=U)
    pagina = client.get(tela).content.decode()
    assert "Toledo" in pagina and "Copiar o pedido" in pagina
    texto = pedido.montar(estudo)
    assert "Vende consorcio." in texto and "Toledo" in texto and "esta e a primeira" in texto

    resposta = """**ANALISE:** comecar amplo.
MODO: estimativa
PALAVRAS:
- consorcio
- Rara
SEMENTES:
- consorcio imovel
SITE: [https://www.exemplo.com.br/consorcio](https://www.exemplo.com.br/consorcio)
CAMPANHA:
- consorcio
FIM"""
    client.post(tela, {"acao": "colar", "resposta": resposta, "pedido": texto})
    rodada = Rodada.objects.get()
    assert rodada.palavras == ["consorcio", "rara"] and rodada.sementes == ["consorcio imovel"]
    assert rodada.situacao == Rodada.Situacao.RASCUNHO
    assert rodada.site == "exemplo.com.br"  # so o dominio, sem o markdown
    conferir = client.get(tela).content.decode()
    assert "Quero estimativa" in conferir and "consorcio\nrara</textarea>" in conferir
    assert "join:" not in conferir
    # Colar de novo (ex.: depois de mudar o raio) troca o rascunho, nao empilha.
    client.post(tela, {"acao": "colar", "resposta": resposta, "pedido": texto})
    assert list(Rodada.objects.values_list("numero", flat=True)) == [1]

    client.post(
        tela,
        {"acao": "buscar", "velocidade": "na_hora", "rodada": rodada.pk, "modo": "estimativa",
         "palavras": "consorcio\nrara",
         "sementes": "consorcio imovel",
         "site": "[https://www.exemplo.com.br](https://www.exemplo.com.br)"},
    )  # fmt: skip
    rodada.refresh_from_db()
    assert rodada.situacao == Rodada.Situacao.PRONTA, rodada.erro
    # dois pedidos de ideias (sementes e site) e um de volume, todos no estado
    assert [c.rsplit("/", 2)[-2] for c, _ in pedidos] == [
        "keywords_for_keywords", "keywords_for_site", "search_volume",
    ]  # fmt: skip
    assert pedidos[1][1]["target"] == "exemplo.com.br"
    item = rodada.resultado["consorcio"]
    area = coleta.cidades_da_area(estudo)
    fatia = (350000 + 10000 * (len(area) - 1)) / (
        350000 + 10000 * (sum(1 for m in municipios.todos().values() if m.uf == 41) - 1)
    )
    assert item["volume_area"] == pytest.approx(10000 * fatia, abs=1)
    assert item["cidades"][str(CASCAVEL)] > item["cidades"][str(TOLEDO)]
    assert rodada.resultado["consorcio de imoveis"]["origem"] == "ideia"
    assert rodada.resultado["simulador de consorcio"]["origem"] == "ideia"
    assert float(rodada.custo_usd) == pytest.approx(0.225)

    # A rodada seguinte leva o que esta achou; o valor real por cidade.
    texto = pedido.montar(Estudo.objects.get())
    assert "consorcio |" in texto and "CAMPANHA ATUAL" in texto
    client.post(
        tela,
        {"acao": "colar", "pedido": texto,
         "resposta": "ANALISE: confirmar\nMODO: real\nPALAVRAS:\n- consorcio\n- rara\n"
                     "CIDADES:\n- Cascavel\n- Toledo\nFIM"},
    )  # fmt: skip
    segunda = Rodada.objects.get(numero=2)
    assert segunda.modo == "real" and segunda.cidades == [CASCAVEL, TOLEDO]
    client.post(
        tela,
        {"acao": "buscar", "velocidade": "na_hora", "rodada": segunda.pk, "modo": "real",
         "palavras": "consorcio\nrara",
         "cidades": [str(CASCAVEL), str(TOLEDO)]},
    )  # fmt: skip
    segunda.refresh_from_db()
    assert segunda.situacao == Rodada.Situacao.PRONTA, segunda.erro
    assert segunda.resultado["consorcio"]["reais"] == {str(CASCAVEL): 30}  # Toledo sem local
    assert segunda.resultado["rara"]["reais"] == {str(CASCAVEL): None}  # abaixo do minimo
    assert any("Toledo" in a for a in segunda.avisos)

    pagina = client.get(tela).content.decode()
    assert "A campanha, pelo funil" in pagina and "Historico das rodadas" in pagina
    detalhe = client.get(
        reverse("estudos:palavra", args=[estudo.pk], urlconf=U), {"p": "consorcio"}
    )
    corpo = detalhe.content.decode()
    assert detalhe.status_code == 200 and "Sazonalidade" in corpo and "Cascavel (PR)" in corpo

    client.post(tela, {"acao": "campanha", "campanha": ["rara"], "negativas": "gratis"})
    estudo.refresh_from_db()
    assert estudo.campanha == ["rara"] and estudo.negativas == ["gratis"]


@pytest.mark.django_db
def test_estudos_so_no_tenant_marcado_e_para_superusuario(ambiente):
    _, usuario, client = ambiente
    inicio = reverse("estudos:inicio", urlconf=U)
    assert client.get(inicio).status_code == 404
    assert "estudos/" not in client.get(reverse("accounts:painel", urlconf=U)).content.decode()
    usuario.is_superuser = True
    usuario.save()
    assert client.get(inicio).status_code == 404  # o tenant nao esta marcado


RESPOSTA_DO_CLIENTE = """TEMA: planejamento para comprar imovel sem juros
PUBLICO: familias da regiao que querem casa propria
OFERTA: carta de credito para comprar o imovel pagando parcelas, sem juros
DORES:
- juros do financiamento muito altos
- nao consigo dar entrada
FRENTES:
- veiculos
PILARES:
- Casa propria: entrada; parcelas; prazos
SEMENTES:
- *consorcio de imoveis
- como comprar casa sem juros
HUMOR: 2
FORMALIDADE: 3
RESPEITO: 1
ENTUSIASMO: 3
PESSOA: voce
REGRA DE OURO: claro e sem promessa
SOMOS:
- somos diretos | nao somos vendedores
TERMOS:
- garantido | provavel | ninguem garante contemplacao
CONVITE: converse com um consultor
EXEMPLOS:
Um paragrafo.

Outro paragrafo.
FIM"""


@pytest.mark.django_db
def test_estudo_vira_tenant_com_os_primeiros_passos_feitos(
    estudo_pronto, tenant_factory, monkeypatch, django_capture_on_commit_callbacks
):
    from django_tenants.utils import schema_context

    from apps.accounts.models import Tenant
    from apps.editorial.models import EditorialProfile, PerfilDoNegocio
    from apps.estudos import passagem, tasks
    from apps.radar.models import ConfiguracaoDoRadar, ContasExternas

    client, _ = estudo_pronto
    estudo = Estudo.objects.create(
        nome="Empresa X", descricao="Vende consorcio.", cidade=CASCAVEL, raio_km=30,
        valor_do_cliente=1500,
    )  # fmt: skip
    Rodada.objects.create(
        estudo=estudo, numero=1, situacao=Rodada.Situacao.PRONTA,
        resultado={"consorcio": {"volume_area": 900, "reais": {}, "meses_area": []}},
    )  # fmt: skip
    texto = passagem.pedido(estudo)
    assert "- consorcio: 900" in texto and "Cascavel (PR) e regiao" in texto
    ContasExternas.objects.update_or_create(pk=1, defaults={"dataforseo_login": "login-do-dono"})

    despachados = []
    monkeypatch.setattr(
        tasks.criar_tenant_do_estudo, "delay", lambda *a, **k: despachados.append((a, k))
    )
    tela = reverse("estudos:estudo", args=[estudo.pk], urlconf=U)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            tela,
            {"acao": "virar_cliente", "resposta": RESPOSTA_DO_CLIENTE, "organization": "Empresa X",
             "subdomain": "empresa-x", "copiar_contas": "on"},
        )  # fmt: skip
    estudo.refresh_from_db()
    assert estudo.tenant_criado == "empresa_x"
    assert Tenant.objects.get(schema_name="empresa_x").status == Tenant.Status.PROVISIONING
    (_tenant_id, dados), opcoes = despachados[0]
    assert opcoes["copiar_contas"] is True
    assert dados["negocio"]["tema"].startswith("planejamento")
    assert dados["sementes"][:2] == ["consorcio de imoveis", "como comprar casa sem juros"]
    assert dados["regiao"] == {"codigo": LOCAL_CASCAVEL, "nome": "Cascavel, Paraná"}
    assert dados["guia"]["tom_humor"] == 2
    assert "abrir" in client.get(tela).content.decode()

    # O que a tarefa faz dentro do schema novo (aqui, um schema de teste).
    with schema_context("public"):
        novo = tenant_factory("passagem")
    with schema_context(novo.schema_name):
        passagem.semear(dados, copiar_contas=True)
        perfil = PerfilDoNegocio.carregar()
        assert perfil.oferta.startswith("carta de credito") and "veiculos" in perfil.frentes
        assert perfil.onde_atende == "Cascavel (PR) e regiao"
        assert perfil.valor_da_conversao == 1500
        config = ConfiguracaoDoRadar.carregar()
        assert "consorcio de imoveis" in config.lista_de_sementes
        assert "nao consigo dar entrada" in config.lista_de_dores
        assert config.regioes == [dados["regiao"]]
        guia = EditorialProfile.carregar()
        assert guia.tom_humor == 2 and guia.regra_de_ouro == "claro e sem promessa"
        assert ContasExternas.carregar().dataforseo_login == "login-do-dono"


@pytest.mark.django_db
def test_rodada_na_fila_padrao_ideias_e_depois_volume(estudo_pronto, monkeypatch):
    """A fila padrao (a barata, que vem marcada): posta as ideias, espera, colhe;
    posta o volume (sem repetir as ideias, que ja vieram com volume), espera,
    colhe e monta como o modo ao vivo. O colhedor do Radar nao mexe nelas."""
    from apps.radar import coleta as coleta_do_radar
    from apps.radar import fila
    from apps.radar.models import TarefaNaFila

    client, _ = estudo_pronto
    postadas, prontas = [], {"sim": False}

    def postar(tipo, lote, *, contas, finalidade, rodada=None):
        criadas = []
        for corpo, contexto in lote:
            postadas.append((tipo, corpo))
            criadas.append(
                TarefaNaFila.objects.create(
                    task_id=f"t{len(postadas)}", tipo=tipo, finalidade=finalidade,
                    contexto={**contexto, "_corpo": corpo},
                )
            )  # fmt: skip
        return criadas

    def colher(tarefa, contas):
        if not prontas["sim"]:
            return None
        corpo = tarefa.contexto["_corpo"]
        if tarefa.tipo == TarefaNaFila.Tipo.IDEIAS:
            itens = [{"keyword": "consorcio de imoveis", "search_volume": 5000, "cpc": 2.0,
                      "competition_index": 40, "monthly_searches": []}]  # fmt: skip
        else:
            itens = [{"keyword": p, "search_volume": 10000, "cpc": 1.5, "competition_index": 70,
                      "monthly_searches": []} for p in corpo["keywords"]]  # fmt: skip
        return {"result": itens}

    monkeypatch.setattr(fila, "postar", postar)
    monkeypatch.setattr(fila, "colher", colher)
    estudo = Estudo.objects.create(nome="X", cidade=CASCAVEL, raio_km=30)
    rodada = Rodada.objects.create(
        estudo=estudo, numero=1, palavras=["consorcio"], sementes=["consorcio imovel"],
        situacao=Rodada.Situacao.NA_FILA,
    )  # fmt: skip
    assert rodada.fila  # a barata vem marcada
    assert float(coleta.custo_estimado(rodada)) == pytest.approx(0.12)

    assert coleta.avancar_na_fila(rodada) == "esperando"  # ideias postadas
    assert [t for t, _ in postadas] == ["ideias"]
    assert coleta.avancar_na_fila(rodada) == "esperando"  # ainda na fila
    coleta_do_radar.colher_fila()  # o Radar nao colhe as tarefas do estudo
    assert TarefaNaFila.objects.filter(situacao="aguardando").count() == 1
    prontas["sim"] = True
    assert coleta.avancar_na_fila(rodada) == "esperando"  # ideias colhidas, volume postado
    assert postadas[1][0] == "volume" and postadas[1][1]["keywords"] == ["consorcio"]
    assert coleta.avancar_na_fila(rodada) == "pronta"
    rodada.refresh_from_db()
    assert rodada.situacao == Rodada.Situacao.PRONTA and rodada.etapa == {}
    assert rodada.resultado["consorcio"]["volume_area"] > 0
    assert rodada.resultado["consorcio de imoveis"]["origem"] == "ideia"

    tela = reverse("estudos:estudo", args=[estudo.pk], urlconf=U)
    Rodada.objects.create(estudo=estudo, numero=2, palavras=["x"])
    assert 'value="fila" checked' in client.get(tela).content.decode()
