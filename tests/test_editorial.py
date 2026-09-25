"""Guia editorial: entra nos prompts de redacao e confere o texto antes de aprovar.

Tres promessas guardadas aqui:

* o guia chega ao modelo sem que nenhum prompt do banco precise mudar;
* cada bloco do guia chega so a quem precisa dele (estrutura ao planejamento,
  convite ao fecho) — mandar tudo a todos faria convite no meio do texto;
* termo proibido nao passa pela aprovacao sem o revisor dizer que viu.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.content.models import Article, Topic
from apps.editorial.models import EditorialProfile
from apps.editorial.services import aplicar_modo, conferir_texto, texto_do_guia
from apps.ops.models import GenerationJob
from apps.ops.orchestrator import criar_job
from tests.test_flows import ROTEIRO_DO_ARTIGO, ModeloFalso, _rodar_ate_o_fim
from tests.test_interface import ambiente, artigo_para_revisar, autora  # noqa: F401


@pytest.fixture(autouse=True)
def _embedding_falso_em_todo_o_arquivo(embedding_falso):
    """O que se olha aqui e o guia, e nao a busca."""


def _perfil(**campos):
    perfil = EditorialProfile(**campos)
    perfil.termos = campos.get("termos", [{"termo": "trata", "troca": "auxilia no manejo"}])
    return perfil


# ---------------------------------------------------------------------------
# Conferencia do texto
# ---------------------------------------------------------------------------
def test_termo_proibido_casa_palavra_inteira_sem_caixa_nem_acento():
    perfil = _perfil(termos=[{"termo": "cura", "troca": "auxilia"}])

    achados = conferir_texto("A CURA existe? Não há cúra. A curadoria é outra coisa.", perfil)

    assert achados.bloqueia
    assert achados.proibidos[0].vezes == 2
    assert achados.proibidos[0].sugestao == "auxilia"


def test_palavra_que_so_contem_o_termo_nao_e_o_termo():
    perfil = _perfil(termos=[{"termo": "trata"}])

    assert not conferir_texto("O tratamento e longo.", perfil).bloqueia


def test_marcas_de_maquina_avisam_sem_bloquear():
    achados = conferir_texto(
        "Vale ressaltar que, no cenário atual, isso proporciona ganhos.", _perfil(termos=[])
    )

    assert not achados.bloqueia
    expressoes = {a.expressao for a in achados.marcas}
    assert {"vale ressaltar", "no cenario atual", "proporcionar"} - expressoes == {"proporcionar"}
    # "proporciona" nao e "proporcionar": palavra inteira vale para marca tambem.


def test_travessao_demais_vira_aviso():
    texto = "um — dois — tres — quatro — cinco"

    assert conferir_texto(texto, _perfil(termos=[])).travessoes == 4
    assert conferir_texto("um — dois", _perfil(termos=[])).travessoes == 0


# ---------------------------------------------------------------------------
# O guia no prompt
# ---------------------------------------------------------------------------
def test_estrutura_so_vai_ao_planejamento_e_convite_so_ao_fecho():
    perfil = _perfil(convite="Convide para agendar uma avaliacao.")

    planejamento = texto_do_guia(perfil, chave="article_outline", tipo_de_conteudo="servico")
    secao = texto_do_guia(perfil, chave="section_draft", tipo_de_conteudo="servico")
    fecho = texto_do_guia(perfil, chave="article_framing", tipo_de_conteudo="servico")

    assert "Como e uma sessao ou atendimento" in planejamento
    assert "Como e uma sessao" not in secao
    assert "agendar uma avaliacao" in fecho
    assert "agendar uma avaliacao" not in planejamento
    assert '"trata" (use "auxilia no manejo")' in secao


def test_prompt_que_so_le_fontes_nao_recebe_guia():
    assert texto_do_guia(_perfil(), chave="consensus_filter") == ""


def test_aplicar_modo_troca_voz_e_termos():
    perfil = _perfil(termos=[])

    aplicar_modo(perfil, "saude")

    assert perfil.mode == "saude"
    assert any(t["termo"] == "cura" for t in perfil.termos)
    with pytest.raises(ValueError):
        aplicar_modo(perfil, "inexistente")


@pytest.mark.django_db
def test_o_guia_chega_ao_modelo_sem_mudar_o_prompt_do_banco(
    tenant_com_acervo, conexao, monkeypatch
):
    perfil = EditorialProfile.carregar()
    perfil.termos = [{"termo": "milagroso", "troca": ""}]
    perfil.save()

    modelo = ModeloFalso(ROTEIRO_DO_ARTIGO)
    monkeypatch.setattr("apps.content.inference.get_provider", lambda *a, **k: modelo)
    topic = Topic.objects.create(title="Efeito no metabolismo", content_type="comparativo")
    job = criar_job(kind=GenerationJob.Kind.PILLAR_ARTICLE, target_object_id=str(topic.pk))
    _rodar_ate_o_fim(str(job.pk))

    tese, plano, secao = modelo.chamadas[0], modelo.chamadas[1], modelo.chamadas[2]
    assert "GUIA EDITORIAL" not in tese["system"]
    assert '"milagroso"' in plano["system"]
    assert "quando escolher cada opcao" in plano["system"]
    assert "quando escolher cada opcao" not in secao["system"]
    assert Article.objects.get(topic=topic).content_type == "comparativo"


# ---------------------------------------------------------------------------
# Aprovacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_termo_proibido_bloqueia_ate_o_revisor_confirmar(ambiente, artigo_para_revisar):  # noqa: F811
    from apps.content.services import RevisaoInsuficiente, aprovar_e_agendar

    _, usuario, _ = ambiente
    perfil = EditorialProfile.carregar()
    perfil.termos = [{"termo": "trata", "troca": "auxilia no manejo"}]
    perfil.save()
    artigo_para_revisar.body_markdown = "A quiropraxia trata hernia de disco."
    artigo_para_revisar.save()

    with pytest.raises(RevisaoInsuficiente, match="trata"):
        aprovar_e_agendar(artigo_para_revisar, revisor=usuario, quando=None)

    aprovar_e_agendar(artigo_para_revisar, revisor=usuario, quando=None, termos_confirmados=True)
    artigo_para_revisar.refresh_from_db()
    assert artigo_para_revisar.status == Article.Status.APPROVED_SCHEDULED


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_salvar_o_guia_pela_tela(ambiente):  # noqa: F811
    _, _, client = ambiente
    url = reverse("editorial:guia", urlconf="core.urls_tenants")

    assert client.get(url).status_code == 200

    resposta = client.post(
        url,
        {
            "tom_humor": 1,
            "tom_formalidade": 4,
            "tom_respeito": 1,
            "tom_entusiasmo": 2,
            "pessoa": "voce",
            "regra_de_ouro": "Validar antes de educar.",
            "tipo_padrao": "servico",
            "somos_texto": "proximos | intimos",
            "termos_texto": "trata | auxilia no manejo | promessa\ncura",
            "estrutura_servico": "O que e\nPara quem e",
            "convite": "",
            "exemplos": "",
        },
    )

    assert resposta.status_code == 302
    perfil = EditorialProfile.carregar()
    assert perfil.somos == [{"somos": "proximos", "nao_somos": "intimos"}]
    assert perfil.termos[0] == {
        "termo": "trata",
        "troca": "auxilia no manejo",
        "motivo": "promessa",
    }
    assert perfil.termos[1]["termo"] == "cura"
    assert perfil.estrutura_de("servico") == ["O que e", "Para quem e"]
    # Tipo sem texto na tela volta ao padrao, e nao some.
    assert perfil.estrutura_de("guia")


@pytest.mark.django_db
def test_aplicar_modo_pela_tela(ambiente):  # noqa: F811
    _, _, client = ambiente

    client.post(reverse("editorial:aplicar_modo", urlconf="core.urls_tenants"), {"modo": "produto"})

    assert EditorialProfile.carregar().mode == "produto"


@pytest.mark.django_db
def test_revisao_mostra_os_termos_achados(ambiente, artigo_para_revisar):  # noqa: F811
    _, _, client = ambiente
    perfil = EditorialProfile.carregar()
    perfil.termos = [{"termo": "trata", "troca": "auxilia no manejo"}]
    perfil.save()
    artigo_para_revisar.body_markdown = "Isto trata a dor."
    artigo_para_revisar.save()

    pagina = client.get(
        reverse("content:revisar", args=[artigo_para_revisar.pk], urlconf="core.urls_tenants")
    ).content.decode()

    assert "Termos proibidos pelo guia editorial" in pagina
    assert "confirmar_termos" in pagina
