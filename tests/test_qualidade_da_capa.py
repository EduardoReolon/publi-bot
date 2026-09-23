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


def test_proibe_texto_legivel_em_qualquer_forma():
    """A bancada do worker foi categorica: nenhuma variante acertou letreiro.
    Nao e questao de amostrador nem de passo — o modelo aprendeu material e
    luz, nao aprendeu a escrever. Palavra na capa se compoe por cima depois."""
    sistema = SEMENTE["sistema"].lower()

    assert "no readable text" in sistema
    for proibido in ("signage", "book cover", "label", "interface"):
        assert proibido in sistema, proibido


def test_proibe_aparelho_com_tela_como_assunto():
    """Mesma bancada: um notebook em cena saiu visivelmente errado em TODAS as
    variantes. Coisas com partes contaveis e legiveis sao o ponto cego deste
    modelo."""
    sistema = SEMENTE["sistema"].lower()

    assert "no manufactured device with a screen" in sistema
    assert "laptop" in sistema


def test_orienta_para_o_que_o_modelo_sabe_fazer():
    """Proibir nao basta: sem um "prefira isto", o modelo de texto escolhe o
    assunto obvio, que num artigo cientifico costuma ser gente de jaleco ou um
    aparelho com tela."""
    sistema = SEMENTE["sistema"].lower()

    for bom in ("material", "texture", "light", "architecture", "nature"):
        assert bom in sistema, bom


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
# A grade nao mora aqui — ela vem do worker, em `/health/` -> `imagem.grade`.
# O exemplo do contrato traz uma amostra dela; o teste do tamanho padrao usa a
# grade REAL, buscada do exemplo de saude, para nao recriar a copia que o
# `tamanhos.py` existe para evitar.
def _grade_do_contrato() -> list[str]:
    import json
    from pathlib import Path

    caminho = Path(__file__).resolve().parent / "contrato_do_worker" / "saude-resposta.json"
    return json.loads(caminho.read_text(encoding="utf-8"))["imagem"]["grade"]


def test_o_tamanho_padrao_passa_nos_tres_limites_do_worker(settings):
    """Sao tres limites independentes, e cada um pede correcao diferente:
    multiplo de 8 (o latente e 8x menor), lado maximo (o que a placa comporta)
    e area maxima (o que o custo comporta). Um padrao que nao passe deixaria a
    instalacao nova com 422 na primeira capa."""
    from apps.inference.tamanhos import conferir

    # Os tetos do worker de hoje. A grade vai separada no teste seguinte.
    limites = {"lado_maximo": 1536, "area_maxima_mp": 1.2}

    assert conferir(settings.IMAGEM_TAMANHO, limites) == []


def test_a_grade_do_exemplo_e_amostra_e_nao_a_lista_inteira():
    """O `saude-resposta.json` traz tres formatos; o worker publica ~40.

    Este teste existe para que ninguem valide contra o exemplo achando que
    ele e a grade — e para que a ausencia do `1344x704` ali, que e o formato
    que este projeto pede, nao seja lida como "esta fora da grade".
    """
    grade = _grade_do_contrato()

    assert len(grade) < 10, "se o exemplo crescer, revise este teste e o comentario"


def test_a_conferencia_acusa_o_que_esta_fora_da_grade():
    """O caso que motivou tudo: `1024x576` passa nos tres limites — multiplo
    de 8, lado e area — e mesmo assim entrega imagem pior, porque a grade e de
    proporcoes e ele nao esta nela. Sem esta conferencia, nada acusa."""
    from apps.inference.tamanhos import conferir

    estado = {"lado_maximo": 1536, "area_maxima_mp": 1.2, "grade": ["1024x1024", "1344x704"]}

    assert conferir("1024x1024", estado) == []

    avisos = conferir("1024x576", estado)
    assert len(avisos) == 1
    assert "grade de treino" in avisos[0]
    # A sugestao vem por PROPORCAO: quem pede 16:9 quer um formato largo, e
    # devolver o quadrado primeiro seria trocar o problema.
    assert "1344x704" in avisos[0]


def test_o_padrao_e_o_formato_que_as_redes_pedem(settings):
    """1200x630 = 1.905:1 e o alvo do `og:image`. Publicar noutra proporcao
    entrega o enquadramento ao corte automatico da rede."""
    largura, altura = (int(p) for p in settings.IMAGEM_TAMANHO.split("x"))

    assert abs(largura / altura - 1200 / 630) < 0.02


def test_o_alvo_literal_das_redes_nao_serve(settings):
    """`1200x630` parece a escolha obvia e nao passa — 630 nao e multiplo de
    8. Nao e a grade que recusa, e o latente, e por isso a mensagem precisa
    ser outra."""
    from apps.inference.tamanhos import conferir

    avisos = conferir("1200x630", {"lado_maximo": 1536, "area_maxima_mp": 1.2})

    assert any("multiplo de 8" in aviso for aviso in avisos)


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
            "lado_maximo": 1536,
            "area_maxima_mp": 1.2,
            "grade": ["1024x1024", "1344x704", "1344x768", "1536x640"],
        },
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **k: httpx.Response(200, json=corpo, request=httpx.Request("GET", url)),
    )

    call_command("configurar_imagem", "--testar")

    saida = capsys.readouterr().out
    assert "nao esta na grade de treino publicada pelo worker" in saida
    assert "1344x704" in saida


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
            "lado_maximo": 1536,
            "area_maxima_mp": 1.2,
            "grade": ["1024x1024", "1344x704", "1344x768", "1536x640"],
        },
    }
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **k: httpx.Response(200, json=corpo, request=httpx.Request("GET", url)),
    )

    call_command("configurar_imagem", "--testar")

    assert "grade de treino" not in capsys.readouterr().out


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

    assert "grade de treino" not in capsys.readouterr().out


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


@pytest.mark.django_db
def test_o_nada_a_fazer_diz_o_motivo(tenant_com_acervo, capsys):
    """Um tenant saudavel e um `--atualizar` que nao alcancou nada terminam
    iguais. Sem o motivo na saida, so a segunda situacao pede investigacao e
    nada na tela distingue as duas."""
    from django.core.management import call_command

    call_command("semear_prompts")
    capsys.readouterr()

    call_command("semear_prompts", "--atualizar")

    assert "nenhum diverge da semente" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# O prompt precisa ser LEGIVEL por quem revisa
# ---------------------------------------------------------------------------
# Quando as tres opcoes saem ruins, a pergunta e "a culpa e do gerador ou do
# prompt?". Sem o texto inteiro na tela nao da para responder — e tema
# abstrato costuma virar prompt vago, que e um defeito do lado de ca.
@pytest.mark.django_db
def test_o_prompt_sai_no_lote_e_nao_repetido_em_cada_opcao(tenant_com_acervo):
    """O worker devolve `revised_prompt` igual ao que recebeu, entao as tres
    opcoes de um lote tem o mesmo texto. Repeti-lo tres vezes seria repetir a
    mesma coisa tres vezes."""
    from apps.content.models import Article, ArticleImage
    from apps.content.views import _lotes_de_capa

    artigo = Article.objects.create(title="RFM", body_markdown="Texto.")
    for posicao in (1, 2, 3):
        ArticleImage.objects.create(
            article=artigo, batch=1, order=posicao, prompt="a ceramic cup on oak, 50mm"
        )

    (lote,) = _lotes_de_capa(artigo)

    assert lote["prompt"] == "a ceramic cup on oak, 50mm"
    assert lote["divergentes"] is False


@pytest.mark.django_db
def test_quando_cada_opcao_tem_prompt_proprio_o_lote_nao_elege_um(tenant_com_acervo):
    """O caso do provedor pago: o dall-e-3 reescreve o prompt POR IMAGEM, e o
    que ele desenhou passa a ser diferente do que se pediu. Mostrar o da
    primeira como se fosse o do lote seria mentir sobre as outras duas."""
    from apps.content.models import Article, ArticleImage
    from apps.content.views import _lotes_de_capa

    artigo = Article.objects.create(title="RFM", body_markdown="Texto.")
    for posicao, texto in enumerate(("cup on oak", "cup on marble", "cup on steel"), start=1):
        ArticleImage.objects.create(article=artigo, batch=1, order=posicao, prompt=texto)

    (lote,) = _lotes_de_capa(artigo)

    assert lote["prompt"] == ""
    assert lote["divergentes"] is True


@pytest.mark.django_db
def test_lotes_diferentes_mostram_prompts_diferentes(tenant_com_acervo):
    """Pedir mais exemplos escreve uma descricao NOVA. Comparar os dois textos
    e o que explica por que o segundo lote ficou melhor ou pior."""
    from apps.content.models import Article, ArticleImage
    from apps.content.views import _lotes_de_capa

    artigo = Article.objects.create(title="RFM", body_markdown="Texto.")
    ArticleImage.objects.create(article=artigo, batch=1, order=1, prompt="primeira descricao")
    ArticleImage.objects.create(article=artigo, batch=2, order=1, prompt="segunda descricao")

    primeiro, segundo = _lotes_de_capa(artigo)

    assert primeiro["prompt"] == "primeira descricao"
    assert segundo["prompt"] == "segunda descricao"


@pytest.mark.django_db
def test_o_prompt_entra_no_payload_do_trabalho(tenant_com_acervo, imagem_falsa, monkeypatch):
    """Quem olha a fila esta investigando por que a capa saiu ruim, e ali o
    artigo pode nem ter sido aberto. O payload e onde a resposta cabe."""
    from apps.content.flows import passo_gerar_capas
    from apps.content.models import Article
    from apps.ops.models import GenerationJob

    artigo = Article.objects.create(title="RFM", body_markdown="Texto.")
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.ARTICLE_COVER,
        target_object_id=str(artigo.pk),
        total_steps=1,
    )

    payload = passo_gerar_capas(job)

    assert payload["capas"] == 3
    assert payload["prompt"], "sem o prompt, o payload diz quantas e nao diz o quê"


@pytest.mark.django_db
def test_o_payload_sem_capa_nao_inventa_prompt(tenant_com_acervo, monkeypatch):
    """Sem conexao de imagem nao houve descricao nenhuma. Um campo vazio ali
    seria lido como "o prompt era vazio", que e outra coisa."""
    from apps.content.flows import passo_gerar_capas
    from apps.content.models import Article
    from apps.ops.models import GenerationJob

    artigo = Article.objects.create(title="RFM", body_markdown="Texto.")
    job = GenerationJob.objects.create(
        kind=GenerationJob.Kind.ARTICLE_COVER,
        target_object_id=str(artigo.pk),
        total_steps=1,
    )

    payload = passo_gerar_capas(job)

    assert payload["capas"] == 0
    assert "prompt" not in payload


def test_o_prompt_tem_saida_para_tema_abstrato():
    """O caso concreto que motivou isto: um artigo sobre RFM — recencia,
    frequencia, valor — nao tem objeto nenhum. Sem uma regra para isso, o
    modelo tenta desenhar a IDEIA, e desenhar ideia e exatamente o que produz
    a capa vaga com cara de IA.

    A saida nao e proibir: e mandar procurar um objeto concreto no MUNDO de
    que o artigo trata, e nao no conceito.
    """
    sistema = SEMENTE["sistema"]

    assert "ABSTRACT" in sistema
    assert "do not try to draw the idea" in sistema
    # Um exemplo concreto junto: a regra sozinha e abstrata, e pedir a um
    # modelo que evite abstracao com uma frase abstrata costuma nao pegar.
    assert "cardboard box on a doorstep" in sistema
