"""O que decide se a capa sai boa, e que nao aparece em erro nenhum.

A geracao de imagem tem uma propriedade desagradavel: quase tudo que se faz
errado **funciona**. Um prompt em portugues gera imagem. Um tamanho fora da
grade de treino gera imagem. Um prompt pedindo pessoas gera imagem. Nada
disso levanta excecao, nada aparece no log, e o unico sintoma e a capa feia
que alguem olha na tela de revisao e nao sabe explicar.

Por isso estes testes guardam CONFIGURACAO e TEXTO, e nao comportamento: o
comportamento ja esta coberto em `test_capa_junto_do_artigo.py`. Aqui se
guarda o que faz a diferenca entre uma capa publicavel e uma com cara de IA.
"""

from __future__ import annotations

import logging

import pytest

from apps.content.capas import parece_portugues
from apps.content.prompts_iniciais import PROMPTS_INICIAIS

SEMENTE = PROMPTS_INICIAIS["image_prompt"]


# ---------------------------------------------------------------------------
# O idioma
# ---------------------------------------------------------------------------
def test_a_instrucao_esta_em_ingles():
    """Pedir em portugues "responda em ingles" e uma instrucao que o modelo
    cumpre quase sempre, e o "quase" custa uma capa."""
    sistema = SEMENTE["sistema"]

    assert "Answer in ENGLISH only" in sistema
    # Sinal grosseiro de que o corpo da instrucao tambem esta em ingles: se
    # alguem traduzir de volta, isto acusa.
    assert sistema.count(" the ") >= 5


def test_o_pedido_termina_lembrando_o_idioma():
    """O titulo e o resumo chegam em portugues. O ultimo token antes da
    resposta e onde o lembrete pesa mais."""
    assert SEMENTE["usuario"].rstrip().endswith("Write the English image prompt:")


@pytest.mark.parametrize(
    "texto",
    [
        "uma mesa de madeira com um copo de agua, luz da manha",
        "foto de um laboratorio, fundo desfocado, sem pessoas",
    ],
)
def test_reconhece_a_descricao_que_voltou_em_portugues(texto):
    assert parece_portugues(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "a blood pressure monitor on a worn wooden table, morning light, 50mm lens",
        "an empty laboratory bench, glass beakers, cold light, shallow depth of field",
        # Nomes proprios e termos tecnicos nao podem disparar o aviso: um
        # falso positivo por rodada ensina a ignorar o log.
        "a petri dish on a lab bench in Sao Paulo, natural light, macro photography",
    ],
)
def test_nao_acusa_ingles_de_ser_portugues(texto):
    assert not parece_portugues(texto)


@pytest.mark.django_db
def test_descricao_em_portugues_vira_aviso_e_nao_falha(monkeypatch, caplog, tenant_com_acervo):
    """Aviso, e nao recusa: o lote continua saindo e quem revisa continua
    escolhendo. O que muda e haver uma linha apontando a causa."""
    from types import SimpleNamespace

    from apps.content.capas import descrever_capa
    from apps.content.models import Article

    monkeypatch.setattr(
        "apps.content.inference.executar_prompt",
        lambda **k: SimpleNamespace(texto="uma mesa de madeira com luz da manha", prompt_run=None),
    )

    artigo = Article.objects.create(title="Efeito X", body_markdown="Texto.")

    with caplog.at_level(logging.WARNING, logger="publibot.content"):
        descricao, _ = descrever_capa(artigo)

    assert descricao  # nao perdeu a descricao
    assert "portugues" in caplog.text
    assert "SDXL" in caplog.text


# ---------------------------------------------------------------------------
# O que o prompt proibe
# ---------------------------------------------------------------------------
def test_proibe_pessoas_inteiras_e_nao_so_rostos():
    """Maos e rostos sao onde este modelo falha de forma visivel. A regra
    antiga dizia "sem rostos em primeiro plano", que ainda deixava passar
    gente no fundo — e uma mao com seis dedos no canto estraga a capa
    inteira."""
    sistema = SEMENTE["sistema"].lower()

    assert "no people" in sistema
    assert "no hands" in sistema or "hands" in sistema


def test_proibe_texto_na_cena():
    sistema = SEMENTE["sistema"].lower()

    assert "no text" in sistema
    assert "letters" in sistema or "signage" in sistema


def test_pede_um_assunto_so():
    """Cena cheia da ao modelo espaco para inventar, e o que ele inventa e o
    que parece errado."""
    assert "One clear subject" in SEMENTE["sistema"]


def test_a_temperatura_continua_alta():
    """Pedir mais exemplos tem de dar imagens diferentes; com temperatura
    baixa, o segundo lote seria variacao do primeiro."""
    assert SEMENTE["temperatura"] >= 0.7


# ---------------------------------------------------------------------------
# O tamanho
# ---------------------------------------------------------------------------
PROPORCOES_TREINADAS = {
    (1024, 1024),
    (1152, 896),
    (896, 1152),
    (1216, 832),
    (832, 1216),
    (1344, 768),
    (768, 1344),
    (1536, 640),
    (640, 1536),
}


def test_o_tamanho_padrao_e_uma_proporcao_treinada(settings):
    """O padrao foi `1024x576` por um tempo: 16:9, escolhido para "economizar
    placa". Esta fora da grade e abaixo do megapixel, e o SDXL responde a isso
    duplicando o assunto e torcendo a geometria — sem erro nenhum. A medicao
    ja tinha mostrado que nao havia economia: 4x mais pixels custaram 23% mais
    tempo."""
    largura, altura = (int(p) for p in settings.IMAGEM_TAMANHO.split("x"))

    assert (largura, altura) in PROPORCOES_TREINADAS


def test_o_tamanho_padrao_cabe_no_limite_do_worker(settings):
    """O worker recusa lado acima de `IMAGEM_LADO_MAXIMO`, que vem 1024. Um
    padrao maior deixaria a instalacao nova com 422 na primeira capa."""
    largura, altura = (int(p) for p in settings.IMAGEM_TAMANHO.split("x"))

    assert max(largura, altura) <= 1024


@pytest.mark.django_db
def test_o_testar_avisa_quando_o_tamanho_esta_fora_da_grade(monkeypatch, settings, capsys):
    import httpx
    from django.core.management import call_command

    settings.IMAGEM_NOME = "Geracao de imagem"
    settings.IMAGEM_BASE_URL = "http://worker:8090"
    settings.IMAGEM_MODELO = "stabilityai/stable-diffusion-xl-base-1.0"
    settings.IMAGEM_SEGREDO = "segredo"
    settings.IMAGEM_TAMANHO = "1024x576"

    corpo = {
        "status": "ok",
        "rotas": {"texto": True, "imagem": True, "conversao": True},
        "imagem": {
            "modelo": "stabilityai/stable-diffusion-xl-base-1.0",
            "dispositivo": "cuda",
            "baixado": True,
        },
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **k: httpx.Response(200, json=corpo, request=httpx.Request("GET", url)),
    )

    call_command("configurar_imagem", "--testar")

    saida = capsys.readouterr().out
    assert "nao e uma das proporcoes em que o SDXL foi treinado" in saida
    assert "1344x768" in saida


@pytest.mark.django_db
def test_o_testar_cala_quando_o_tamanho_esta_certo(monkeypatch, settings, capsys):
    import httpx
    from django.core.management import call_command

    settings.IMAGEM_NOME = "Geracao de imagem"
    settings.IMAGEM_BASE_URL = "http://worker:8090"
    settings.IMAGEM_MODELO = "stabilityai/stable-diffusion-xl-base-1.0"
    settings.IMAGEM_SEGREDO = "segredo"
    settings.IMAGEM_TAMANHO = "1024x1024"

    corpo = {
        "status": "ok",
        "rotas": {"texto": True, "imagem": True, "conversao": True},
        "imagem": {
            "modelo": "stabilityai/stable-diffusion-xl-base-1.0",
            "dispositivo": "cuda",
            "baixado": True,
        },
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **k: httpx.Response(200, json=corpo, request=httpx.Request("GET", url)),
    )

    call_command("configurar_imagem", "--testar")

    assert "proporcoes em que o SDXL" not in capsys.readouterr().out


@pytest.mark.django_db
def test_um_provedor_pago_nao_leva_o_aviso_do_sdxl(monkeypatch, settings, capsys):
    """O dall-e-3 aceita tres tamanhos, nenhum deles desta lista. Avisar ali
    seria ruido, e ruido ensina a ignorar o aviso que importa."""
    import httpx
    from django.core.management import call_command

    settings.IMAGEM_NOME = "Geracao de imagem"
    settings.IMAGEM_BASE_URL = "https://api.openai.com/v1"
    settings.IMAGEM_MODELO = "dall-e-3"
    settings.IMAGEM_SEGREDO = "segredo"
    settings.IMAGEM_TAMANHO = "1792x1024"

    corpo = {
        "status": "ok",
        "rotas": {"texto": True, "imagem": True, "conversao": True},
        "imagem": {"modelo": "dall-e-3", "dispositivo": "n/a", "baixado": True},
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **k: httpx.Response(200, json=corpo, request=httpx.Request("GET", url)),
    )

    call_command("configurar_imagem", "--testar")

    assert "proporcoes em que o SDXL" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A melhoria precisa CHEGAR a quem ja esta rodando
# ---------------------------------------------------------------------------
# `garantir_prompts_padrao` nunca toca no que existe — de proposito, porque
# quem ajustou um prompt pela tela nao pode perde-lo numa implantacao
# (ADR-0012). O efeito colateral e que reescrever uma semente no codigo nao
# alcanca NINGUEM: o conserto sai, a suite fica verde, e todo tenant antigo
# continua com o texto velho. Sem `--atualizar`, tudo o que esta acima seria
# trabalho para instalacao nova apenas.
@pytest.mark.django_db
def test_semear_sozinho_nao_mexe_no_que_existe(tenant_com_acervo):
    from apps.content.models import PromptVersion
    from apps.content.services import garantir_prompts_padrao

    garantir_prompts_padrao()
    versao = PromptVersion.objects.get(template__key="image_prompt", is_active=True)
    versao.system_prompt = "texto que alguem ajustou na tela"
    versao.save(update_fields=["system_prompt"])

    garantir_prompts_padrao()

    versao.refresh_from_db()
    assert versao.system_prompt == "texto que alguem ajustou na tela"


@pytest.mark.django_db
def test_atualizar_publica_versao_nova_e_guarda_a_anterior(tenant_com_acervo):
    from apps.content.models import PromptVersion
    from apps.content.services import atualizar_prompts_padrao, garantir_prompts_padrao

    garantir_prompts_padrao()
    antiga = PromptVersion.objects.get(template__key="image_prompt", is_active=True)
    antiga.system_prompt = "prompt velho, em portugues"
    antiga.save(update_fields=["system_prompt"])

    assert atualizar_prompts_padrao({"image_prompt"}) == ["image_prompt"]

    nova = PromptVersion.objects.get(template__key="image_prompt", is_active=True)
    assert nova.pk != antiga.pk
    assert nova.system_prompt == SEMENTE["sistema"]
    assert nova.version > antiga.version

    # A anterior continua la, desativada: um clique de volta, e nao um
    # `git revert` seguido de implantacao.
    antiga.refresh_from_db()
    assert antiga.is_active is False
    assert antiga.system_prompt == "prompt velho, em portugues"


@pytest.mark.django_db
def test_atualizar_duas_vezes_nao_cria_duas_versoes_iguais(tenant_com_acervo):
    from apps.content.models import PromptVersion
    from apps.content.services import atualizar_prompts_padrao, garantir_prompts_padrao

    garantir_prompts_padrao()
    quantas = PromptVersion.objects.filter(template__key="image_prompt").count()

    assert atualizar_prompts_padrao({"image_prompt"}) == []
    assert PromptVersion.objects.filter(template__key="image_prompt").count() == quantas


@pytest.mark.django_db
def test_atualizar_nao_derruba_a_variante_de_teste_ab(tenant_com_acervo):
    """Sobrescrever a variante B destruiria a comparacao em curso — que e
    exatamente o que o teste A/B existe para produzir."""
    from apps.content.models import PromptTemplate, PromptVersion
    from apps.content.services import atualizar_prompts_padrao, garantir_prompts_padrao

    garantir_prompts_padrao()
    template = PromptTemplate.objects.get(key="image_prompt")
    template.versions.filter(variant="A").update(system_prompt="velho")
    experimento = PromptVersion.objects.create(
        template=template,
        version=99,
        variant="B",
        system_prompt="a variante do experimento",
        user_prompt_template="{titulo}",
        is_active=True,
    )

    atualizar_prompts_padrao({"image_prompt"})

    experimento.refresh_from_db()
    assert experimento.is_active is True
    assert experimento.system_prompt == "a variante do experimento"


@pytest.mark.django_db
def test_o_comando_atualiza_so_as_chaves_pedidas(tenant_com_acervo, capsys):
    from django.core.management import call_command

    from apps.content.models import PromptVersion
    from apps.content.services import garantir_prompts_padrao

    garantir_prompts_padrao()
    PromptVersion.objects.filter(template__key="image_prompt").update(system_prompt="velho")
    PromptVersion.objects.filter(template__key="seo_metadata").update(system_prompt="velho tambem")

    call_command("semear_prompts", "--atualizar", "--so", "image_prompt")

    assert (
        PromptVersion.objects.get(template__key="image_prompt", is_active=True).system_prompt
        == SEMENTE["sistema"]
    )
    assert (
        PromptVersion.objects.get(template__key="seo_metadata", is_active=True).system_prompt
        == "velho tambem"
    )


@pytest.mark.django_db
def test_sem_atualizar_o_comando_continua_conservador(tenant_com_acervo):
    """A implantacao chama `semear_prompts --todos` sem `--atualizar`.
    Sobrescrever o ajuste de um cliente tem de ser decisao de alguem, nunca
    efeito colateral de um deploy."""
    from django.core.management import call_command

    from apps.content.models import PromptVersion
    from apps.content.services import garantir_prompts_padrao

    garantir_prompts_padrao()
    PromptVersion.objects.filter(template__key="image_prompt").update(system_prompt="ajustado")

    call_command("semear_prompts")

    assert (
        PromptVersion.objects.get(template__key="image_prompt", is_active=True).system_prompt
        == "ajustado"
    )
