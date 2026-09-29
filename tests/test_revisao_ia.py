"""A volta da outra IA: pedido com codigos, previa e aplicacao."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.radar.models import ConfiguracaoDoRadar, GrupoDeDemanda
from tests.test_interface import ambiente  # noqa: F401


def _grupo(rotulo, nota=50, **extra):
    return GrupoDeDemanda.objects.create(rotulo=rotulo, nota=nota, **extra)


def _resposta(bom, ruim):
    from apps.radar.revisao_ia import codigo

    return f"""Aqui esta a analise.

**SEMENTES:**
- organizar a operacao da empresa
- *descobrir o lucro de verdade*
- organizar a operacao da empresa

## DORES
1. nao sei quanto lucro de verdade
2. equipe refaz o mesmo trabalho

BONS:
- `{codigo(bom)}`: dono de empresa com dor real
RUINS:
- {codigo(ruim)} - publico estudante
- t-zzzzzz: codigo inventado
COMENTARIOS:
Confirme exame.com como concorrente de conteudo.
FIM
Espero ter ajudado!"""


@pytest.mark.django_db
def test_le_a_resposta_mesmo_enfeitada(ambiente):  # noqa: F811
    from apps.radar.revisao_ia import ler

    bom, ruim = _grupo("lucro real da empresa"), _grupo("padronizacao tcc")
    leitura = ler(_resposta(bom, ruim))

    assert leitura.sementes == ["organizar a operacao da empresa", "descobrir o lucro de verdade"]
    assert leitura.dores[0] == "nao sei quanto lucro de verdade"
    assert leitura.bons[0][1] == "dono de empresa com dor real"
    assert [m for _, m in leitura.ruins] == ["publico estudante", "codigo inventado"]
    assert "exame.com" in leitura.comentarios


@pytest.mark.django_db
def test_previa_mostra_o_que_muda_sem_aplicar(ambiente):  # noqa: F811
    from apps.radar.revisao_ia import ler, previa

    config = ConfiguracaoDoRadar.carregar()
    config.sementes = "tecnologia e automacao\norganizar a operacao da empresa"
    config.save()
    bom, ruim = _grupo("lucro real da empresa"), _grupo("padronizacao tcc")

    resultado = previa(ler(_resposta(bom, ruim)))

    assert resultado["sementes"]["entram"] == ["descobrir o lucro de verdade"]
    assert resultado["sementes"]["saem"] == ["tecnologia e automacao"]
    assert (resultado["bons"], resultado["ruins"]) == (1, 1)
    assert resultado["nao_achados"] == ["t-zzzzzz"]
    assert ConfiguracaoDoRadar.carregar().lista_de_sementes[0] == "tecnologia e automacao"


@pytest.mark.django_db
def test_aplicar_troca_as_listas_e_etiqueta_sem_descartar(ambiente):  # noqa: F811
    _, _, client = ambiente
    bom, ruim = _grupo("lucro real da empresa"), _grupo("padronizacao tcc")

    resposta = client.post(
        reverse("radar:aplicar_resposta_ia", urlconf="core.urls_tenants"),
        {"resposta": _resposta(bom, ruim)},
    )

    assert resposta.status_code == 302
    config = ConfiguracaoDoRadar.carregar()
    assert config.lista_de_sementes == [
        "organizar a operacao da empresa",
        "descobrir o lucro de verdade",
    ]
    assert config.lista_de_dores == [
        "nao sei quanto lucro de verdade",
        "equipe refaz o mesmo trabalho",
    ]
    ruim.refresh_from_db()
    assert (ruim.avaliacao_ia, ruim.motivo_ia) == ("ruim", "publico estudante")
    assert ruim.situacao == GrupoDeDemanda.Situacao.NOVO


@pytest.mark.django_db
def test_pedido_leva_so_temas_sem_avaliacao_com_codigo(ambiente):  # noqa: F811
    from apps.radar.resumo import texto_para_ia
    from apps.radar.revisao_ia import codigo

    novo = _grupo("tema novo")
    avaliado = _grupo("tema avaliado", avaliacao_ia="boa", motivo_ia="ok")

    texto = texto_para_ia()

    assert f"{codigo(novo)} | tema novo" in texto
    assert "tema avaliado" not in texto
    assert "RUINS:" in texto and "1 ja avaliados" in texto
    assert codigo(avaliado) not in texto


@pytest.mark.django_db
def test_filtro_e_descarte_dos_ruins(ambiente):  # noqa: F811
    _, _, client = ambiente
    _grupo("tema bom", avaliacao_ia="boa")
    ruim = _grupo("tema ruim", avaliacao_ia="ruim", motivo_ia="estudante")
    url = reverse("radar:radar", urlconf="core.urls_tenants")

    pagina = client.get(url + "?ia=ruim").content.decode()
    assert "tema ruim" in pagina and "tema bom" not in pagina
    assert "Descartar os 1 marcados como ruins" in pagina

    client.post(reverse("radar:descartar_ruins", urlconf="core.urls_tenants"))
    ruim.refresh_from_db()
    assert ruim.situacao == GrupoDeDemanda.Situacao.DESCARTADO
    assert GrupoDeDemanda.objects.get(rotulo="tema bom").situacao == GrupoDeDemanda.Situacao.NOVO


@pytest.mark.django_db
def test_colar_mostra_a_previa(ambiente):  # noqa: F811
    _, _, client = ambiente
    bom, ruim = _grupo("lucro real da empresa"), _grupo("padronizacao tcc")

    resposta = client.post(
        reverse("radar:revisar_resposta_ia", urlconf="core.urls_tenants"),
        {"resposta": _resposta(bom, ruim)},
    )

    html = resposta.content.decode()
    assert resposta.status_code == 200
    assert "Nada foi aplicado ainda" in html and "padronizacao tcc" in html
    assert "t-zzzzzz" in html
