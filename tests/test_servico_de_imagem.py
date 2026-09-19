"""O servico de imagem do `worker-gpu`, pelo HTTP.

O modelo de difusao nao entra aqui: `_gerar_imagens` e substituido, e o que
sobra e exatamente o que se quer testar — quem pode chamar, o que acontece
quando a placa ja esta ocupada, e se a resposta tem o formato que o
`OpenAICompatibleImageClient` sabe ler.

Esse ultimo ponto e o mais importante. Os dois lados combinam por um contrato
que nenhum dos dois declara: o cliente monta `POST {base_url}/v1/images/
generations` e le `data[].b64_json`. Divergir em qualquer detalhe — a rota, o
nome do campo, o base64 — produz uma falha em producao numa parte do sistema
que so roda na maquina com placa.

`importorskip` no topo: o FastAPI esta em `requirements-dev.txt`, entao no CI e
no ambiente de quem desenvolve ele existe. Numa instalacao so de producao,
nao — e ai estes testes nao teriam o que exercitar mesmo.
"""

from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi so entra por requirements-dev.txt")

from fastapi.testclient import TestClient

SEGREDO = "segredo-de-teste"


@pytest.fixture
def servico(monkeypatch):
    """O modulo carregado com um ambiente conhecido, e sem modelo nenhum.

    Recarregado a cada teste porque ele le o ambiente em constantes de modulo,
    na importacao — que e o que faz o systemd valer alguma coisa, e o que
    obriga a recarga aqui.
    """
    monkeypatch.setenv("WORKER_SHARED_SECRET", SEGREDO)
    monkeypatch.setenv("IMAGEM_DEVICE", "cpu")
    # Sem temporizador de descarga: um `threading.Timer` vivo depois do teste
    # dispararia no meio de outro.
    monkeypatch.setenv("IMAGEM_OCIOSO_SEGUNDOS", "0")
    monkeypatch.setenv("IMAGEM_MAXIMO", "4")

    raiz = str(Path(__file__).resolve().parent.parent / "worker-gpu")
    if raiz not in sys.path:
        sys.path.insert(0, raiz)

    modulo = importlib.import_module("imagem_api")
    modulo = importlib.reload(modulo)

    # A parte pesada. O que passa por ela e o que os testes conferem.
    monkeypatch.setattr(
        modulo,
        "_gerar_imagens",
        lambda dispositivo, pedido, quantas, largura, altura: [
            f"png-{i}-{largura}x{altura}".encode() for i in range(quantas)
        ],
    )
    return modulo


@pytest.fixture
def cliente(servico):
    return TestClient(servico.app)


def _cabecalhos(segredo: str = SEGREDO) -> dict[str, str]:
    return {"Authorization": f"Bearer {segredo}"}


# ---------------------------------------------------------------------------
# Credencial
# ---------------------------------------------------------------------------
def test_sem_credencial_recusa(cliente):
    """Um endpoint que roda modelo na sua placa, aberto, e placa de graca para
    quem achar a porta."""
    resposta = cliente.post("/v1/images/generations", json={"prompt": "um gato"})

    assert resposta.status_code == 401


def test_credencial_errada_recusa(cliente):
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "um gato"}, headers=_cabecalhos("outro")
    )

    assert resposta.status_code == 401


def test_aceita_o_bearer_que_o_publibot_envia(cliente):
    """`Authorization: Bearer` e o que o `OpenAICompatibleImageClient` manda,
    porque e o que o dialeto da OpenAI define. Se o servico so entendesse o
    cabecalho proprio do Docling, toda geracao voltaria 401."""
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "um gato"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 200


def test_aceita_tambem_o_cabecalho_do_docling(cliente):
    """Para um `curl` de diagnostico ser igual nos dois servicos."""
    resposta = cliente.post(
        "/v1/images/generations",
        json={"prompt": "um gato"},
        headers={"X-Worker-Secret": SEGREDO},
    )

    assert resposta.status_code == 200


def test_sem_segredo_configurado_o_servico_diz_isso(servico, monkeypatch):
    """500 e nao 401: o problema e deste lado, e responder "credencial
    invalida" mandaria procurar no lugar errado."""
    monkeypatch.setattr(servico, "SEGREDO", "")

    resposta = TestClient(servico.app).post(
        "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 500
    assert "WORKER_SHARED_SECRET" in resposta.json()["detail"]


# ---------------------------------------------------------------------------
# O contrato com o cliente do PubliBot
# ---------------------------------------------------------------------------
def test_a_resposta_tem_o_formato_que_o_cliente_le(cliente):
    resposta = cliente.post(
        "/v1/images/generations",
        json={"prompt": "um gato", "n": 3, "size": "1024x1024"},
        headers=_cabecalhos(),
    )

    corpo = resposta.json()
    assert len(corpo["data"]) == 3
    assert base64.b64decode(corpo["data"][0]["b64_json"]) == b"png-0-1024x1024"
    assert corpo["data"][0]["revised_prompt"] == "um gato"


def test_o_cliente_de_verdade_consegue_ler_a_resposta(servico, monkeypatch):
    """O teste que fecha o circuito: a resposta deste servico passa pelo
    `OpenAICompatibleImageClient` de verdade, o mesmo que roda em producao.

    Sem ele, os dois lados poderiam divergir na rota ou no nome de um campo e
    os testes de cada um continuariam verdes.
    """
    import httpx

    from apps.inference.providers.openai_compatible import OpenAICompatibleImageClient

    # `TestClient` e um `httpx.Client` sincrono que fala com o app em
    # processo; o `ASGITransport` do httpx so serve ao cliente assincrono, e o
    # do PubliBot e sincrono. Os kwargs do chamador (`timeout`, `verify`) sao
    # descartados porque nao ha socket nenhum aqui.
    def cliente_sobre_o_app(*args, **kwargs):
        return TestClient(servico.app, base_url="http://worker")

    monkeypatch.setattr(httpx, "Client", cliente_sobre_o_app)

    cliente_real = OpenAICompatibleImageClient(base_url="http://worker", api_key=SEGREDO)

    geradas = cliente_real.generate(model="sdxl", prompt="um gato", quantidade=3)

    assert len(geradas) == 3
    assert geradas[0].conteudo == b"png-0-1024x1024"
    assert geradas[0].prompt_revisado == "um gato"


def test_v1_models_responde_porque_o_health_do_cliente_bate_la(cliente):
    resposta = cliente.get("/v1/models")

    assert resposta.status_code == 200
    assert resposta.json()["data"][0]["id"]


# ---------------------------------------------------------------------------
# Dividir a placa
# ---------------------------------------------------------------------------
def test_recusa_a_segunda_geracao_em_vez_de_enfileirar(servico):
    """503, e nao uma fila aqui dentro: o PubliBot ja tem fila e sabe tentar de
    novo. Uma segunda fila seria invisivel para ele — e duas geracoes ao mesmo
    tempo estouram a VRAM, que e o que este semaforo evita."""
    cliente = TestClient(servico.app)
    servico._uma_por_vez.acquire()

    try:
        resposta = cliente.post(
            "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
        )
    finally:
        servico._uma_por_vez.release()

    assert resposta.status_code == 503
    assert resposta.headers["Retry-After"] == "60"


def test_a_vaga_e_devolvida_mesmo_quando_a_geracao_falha(servico, monkeypatch):
    """Sem isto, um erro deixaria o servico recusando tudo com 503 ate o
    proximo restart — e o sintoma seria "o gerador de imagem parou"."""
    cliente = TestClient(servico.app)

    def explodir(*a, **k):
        raise RuntimeError("o modelo nao carregou")

    monkeypatch.setattr(servico, "_gerar_imagens", explodir)

    primeira = cliente.post("/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos())

    assert primeira.status_code == 500
    assert servico._uma_por_vez._value == 1


def test_sem_vram_recusa_em_vez_de_gastar_horas_de_cpu(servico, monkeypatch, caplog):
    """A licao mais cara desta feature, medida em uso: 8h23min de CPU, 11 GB
    de RAM e 2 GB de swap para gerar UMA leva de capas.

    Numa placa de 8 GB com um modelo de texto grande carregado, a VRAM esta
    sempre cheia — o "fallback para CPU" deixa de ser excecao e vira o caminho
    normal. Recusar e melhor: o PubliBot entende 503 como "tente depois", o
    trabalho volta para a fila, e a capa sai quando a placa vagar.
    """
    import logging

    tentativas = []

    def sem_vram(dispositivo, pedido, quantas, largura, altura):
        tentativas.append(dispositivo)
        if dispositivo == "cuda":
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return [b"png-da-cpu"]

    monkeypatch.setattr(servico, "_gerar_imagens", sem_vram)
    monkeypatch.setattr(servico, "_resolver_dispositivo", lambda: "cuda")
    monkeypatch.setattr(servico, "PERMITIR_CPU", False)

    with caplog.at_level(logging.WARNING, logger="imagem-api"):
        resposta = TestClient(servico.app).post(
            "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
        )

    assert resposta.status_code == 503
    # Tentou na placa e parou ali. Nao foi para a CPU.
    assert tentativas == ["cuda"]
    # E a mensagem diz o que fazer, nao so o que houve.
    assert "IMAGEM_PERMITIR_CPU" in resposta.json()["error"]["message"]


def test_com_permissao_explicita_a_cpu_e_usada(servico, monkeypatch, caplog):
    """Quem tem tempo de sobra pode ligar. O que nao pode e isso acontecer
    sozinho — e, quando acontece, precisa deixar rastro."""
    import logging

    tentativas = []

    def sem_vram(dispositivo, pedido, quantas, largura, altura):
        tentativas.append(dispositivo)
        if dispositivo == "cuda":
            raise RuntimeError("CUDA out of memory")
        return [b"png-da-cpu"]

    monkeypatch.setattr(servico, "_gerar_imagens", sem_vram)
    monkeypatch.setattr(servico, "_resolver_dispositivo", lambda: "cuda")
    monkeypatch.setattr(servico, "PERMITIR_CPU", True)

    with caplog.at_level(logging.WARNING, logger="imagem-api"):
        resposta = TestClient(servico.app).post(
            "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
        )

    assert resposta.status_code == 200
    assert tentativas == ["cuda", "cpu"]
    assert any("IMAGEM_PERMITIR_CPU" in r.message for r in caplog.records)


def test_tempo_esgotado_vira_503_com_o_que_ajustar(servico, monkeypatch):
    """Nao e erro do pedido: e esta maquina nao dando conta deste tamanho."""

    def demorou_demais(dispositivo, pedido, quantas, largura, altura):
        raise servico.TempoEsgotado("a geracao passou de 600s em cpu no passo 3 de 25")

    monkeypatch.setattr(servico, "_gerar_imagens", demorou_demais)

    resposta = TestClient(servico.app).post(
        "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 503
    assert resposta.headers["Retry-After"] == "300"
    assert "passou de 600s" in resposta.json()["error"]["message"]


def test_o_vigia_interrompe_a_difusao_quando_estoura_o_orcamento(servico, monkeypatch):
    """O `callback_on_step_end` e o unico ponto em que da para desistir: o laco
    de difusao nao olha para sinal nem para timeout.

    Sem ele, um `systemctl restart` fica preso em `deactivating (stop-sigterm)`
    ate o `TimeoutStopSec` — foi o que aconteceu na geracao de 8 horas.
    """
    monkeypatch.setattr(servico, "TEMPO_MAXIMO", 0.0001)
    vigia = servico._vigia_do_relogio("cpu", 1024, 576)

    import time

    time.sleep(0.01)

    with pytest.raises(servico.TempoEsgotado) as erro:
        vigia(None, 3, None, {})

    assert "1024x576" in str(erro.value)
    assert "passo 3" in str(erro.value)


def test_sem_orcamento_o_vigia_nem_existe(servico, monkeypatch):
    """`IMAGEM_TEMPO_MAXIMO=0` desliga. O diffusers recebe `None` e nao chama
    nada — nao ha custo por passo."""
    monkeypatch.setattr(servico, "TEMPO_MAXIMO", 0)

    assert servico._vigia_do_relogio("cuda", 512, 512) is None


def test_o_vigia_deixa_passar_dentro_do_orcamento(servico, monkeypatch):
    monkeypatch.setattr(servico, "TEMPO_MAXIMO", 600)
    vigia = servico._vigia_do_relogio("cuda", 512, 512)

    # Devolve os argumentos intactos: o diffusers usa o retorno.
    assert vigia(None, 1, None, {"latents": "x"}) == {"latents": "x"}


def test_a_queda_para_cpu_aparece_no_health(servico, monkeypatch):
    """E onde o `configurar_imagem --testar` vai procurar depois."""
    monkeypatch.setattr(servico, "_resolver_dispositivo", lambda: "cpu")
    cliente = TestClient(servico.app)

    cliente.post("/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos())

    assert cliente.get("/health/").json()["ultimo_dispositivo"] == "cpu"


def test_um_erro_de_verdade_em_cpu_nao_vira_tentativa_de_cpu(servico, monkeypatch):
    """A queda para CPU so faz sentido vindo da placa. Repeti-la em CPU seria
    rodar duas vezes o mesmo erro e demorar o dobro para relata-lo."""
    tentativas = []

    def sempre_falha(dispositivo, *a, **k):
        tentativas.append(dispositivo)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(servico, "_gerar_imagens", sempre_falha)
    monkeypatch.setattr(servico, "_resolver_dispositivo", lambda: "cpu")

    resposta = TestClient(servico.app).post(
        "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 500
    assert tentativas == ["cpu"]


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def test_prompt_vazio_e_recusado(cliente):
    resposta = cliente.post("/v1/images/generations", json={"prompt": "  "}, headers=_cabecalhos())

    assert resposta.status_code == 422


def test_lado_que_nao_e_multiplo_de_oito_e_recusado(cliente):
    """Nao falharia: o modelo arredonda por dentro e devolve uma imagem de
    tamanho diferente do pedido, sem avisar."""
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "x", "size": "1000x1001"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 422
    assert "multiplo de 8" in resposta.json()["detail"]


def test_tamanho_acima_do_que_a_placa_comporta_e_recusado(cliente):
    """Recusar aqui da uma mensagem sobre tamanho; deixar passar da um erro de
    VRAM vindo de dentro do torch, que nao menciona tamanho nenhum."""
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "x", "size": "2048x2048"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 422
    assert "1024" in resposta.json()["detail"]


def test_size_sem_sentido_e_recusado(cliente):
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "x", "size": "grande"}, headers=_cabecalhos()
    )

    assert resposta.status_code == 422


def test_pedido_maior_que_o_teto_e_aparado(cliente):
    """Um cliente distraido pedindo 50 seguraria a placa por meia hora."""
    resposta = cliente.post(
        "/v1/images/generations", json={"prompt": "x", "n": 50}, headers=_cabecalhos()
    )

    assert len(resposta.json()["data"]) == 4


# ---------------------------------------------------------------------------
# A armadilha documentada no proprio arquivo
# ---------------------------------------------------------------------------
def test_o_pipeline_nao_vai_inteiro_para_a_placa():
    """`enable_model_cpu_offload()` e o que mantem os pesos na RAM e sobe para
    a VRAM so o submodulo em uso — e o que permite o Ollama continuar
    carregado ao lado, numa placa de 8 GB.

    Um `.to("cuda")` desfaz o arranjo em SILENCIO: continua funcionando,
    continua gerando imagem, e passa a ocupar o dobro. Nao ha como flagrar
    isso sem placa; o que da para impedir e a linha voltar ao arquivo.

    Pela arvore sintatica, e nao por busca de texto: o proprio arquivo explica
    a armadilha num comentario, e procurar a string encontraria o comentario.
    """
    import ast

    fonte = (Path(__file__).resolve().parent.parent / "worker-gpu" / "imagem_api.py").read_text()
    chamadas = [no for no in ast.walk(ast.parse(fonte)) if isinstance(no, ast.Call)]

    def chamou(metodo: str, argumento: str | None = None) -> bool:
        for no in chamadas:
            if not isinstance(no.func, ast.Attribute) or no.func.attr != metodo:
                continue
            if argumento is None:
                return True
            if any(isinstance(a, ast.Constant) and a.value == argumento for a in no.args):
                return True
        return False

    assert chamou("enable_model_cpu_offload")
    assert not chamou("to", "cuda")


# ---------------------------------------------------------------------------
# O servico continua respondendo enquanto gera
# ---------------------------------------------------------------------------
def test_health_responde_durante_uma_geracao(servico, monkeypatch):
    """Encontrado em uso: `/health/` dava "timed out" enquanto uma capa era
    gerada, e o diagnostico inteiro parava junto.

    A causa e uma so, e vale para qualquer servico FastAPI deste repositorio:
    um handler `async def` roda NO EVENT LOOP. Uma chamada bloqueante dentro
    dele — e rodar um modelo de difusao e a mais bloqueante possivel — congela
    o processo todo. Nenhuma outra requisicao chega a ser lida.

    Um handler `def` (sem async) roda numa thread do pool, e o loop continua
    livre. A diferenca sao seis letras e nada no comportamento aparente ate o
    dia em que a requisicao demora minutos.

    O `with` importa: so dentro dele o TestClient usa UM event loop para todas
    as requisicoes, que e como o uvicorn se comporta. Sem ele cada chamada
    ganha um loop proprio e o defeito desaparece do teste sem ter sumido do
    servico.
    """
    import threading
    import time

    comecou = threading.Event()

    def demorada(dispositivo, pedido, quantas, largura, altura):
        comecou.set()
        time.sleep(2.0)
        return [b"png-demorado"]

    monkeypatch.setattr(servico, "_gerar_imagens", demorada)

    with TestClient(servico.app) as cliente:
        geracao = threading.Thread(
            target=lambda: cliente.post(
                "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
            ),
            daemon=True,
        )
        geracao.start()
        assert comecou.wait(timeout=10), "a geracao nem comecou"

        inicio = time.perf_counter()
        resposta = cliente.get("/health/")
        decorrido = time.perf_counter() - inicio

        geracao.join(timeout=20)

    assert resposta.status_code == 200
    assert decorrido < 1.5, f"/health/ esperou a geracao terminar ({decorrido:.1f}s)"
    # E, ja que respondeu, respondeu a verdade.
    assert resposta.json()["busy"] is True


def test_a_segunda_geracao_recebe_503_em_vez_de_esperar(servico, monkeypatch):
    """O par do teste acima. Com o loop bloqueado, o semaforo era decorativo:
    a segunda requisicao nao era processada, entao nao havia o que recusar.
    Com o handler fora do loop, ela chega e leva o 503 que o PubliBot entende
    como "tente de novo"."""
    import threading
    import time

    comecou = threading.Event()

    def demorada(dispositivo, pedido, quantas, largura, altura):
        comecou.set()
        time.sleep(2.0)
        return [b"png"]

    monkeypatch.setattr(servico, "_gerar_imagens", demorada)

    with TestClient(servico.app) as cliente:
        primeira = threading.Thread(
            target=lambda: cliente.post(
                "/v1/images/generations", json={"prompt": "x"}, headers=_cabecalhos()
            ),
            daemon=True,
        )
        primeira.start()
        assert comecou.wait(timeout=10)

        segunda = cliente.post(
            "/v1/images/generations", json={"prompt": "y"}, headers=_cabecalhos()
        )

        primeira.join(timeout=20)

    assert segunda.status_code == 503
