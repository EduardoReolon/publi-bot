"""O cache do modelo de embedding, e as duas formas em que ele quebrou.

Encontrado numa instalacao nova, na tela de curadoria de um documento:

    onnxruntime.capi.onnxruntime_pybind11_state.Fail: [ONNXRuntimeError] :
    FAIL : External data path validation failed for initializer:
    embeddings.word_embeddings.weight. Error: External data path escapes model
    directory. ... allowed directory: ".model_cache/blobs/29"

O modelo vem em duas partes: `model.onnx`, com o grafo, e `model.onnx_data`,
com 2 GB de pesos. O cache do HuggingFace guarda cada arquivo em
`blobs/<dois primeiros digitos do hash>/` e deixa no diretorio do modelo apenas
um link simbolico — entao as duas partes caem em pastas diferentes. Desde a
versao 1.22 o onnxruntime resolve o link da primeira, adota a pasta resultante
como a unica permitida, e recusa a segunda por estar fora dela.

Nada aqui carrega o modelo de verdade: esses testes rodam em toda maquina, com
ou sem os 2 GB em disco. Os marcados `integration` em `test_embeddings_reais.py`
e a verificacao manual do layout e que exercitam o carregamento.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.test import override_settings

from apps.knowledge.embeddings import FastEmbedClient, _traduzir_falha_de_carregamento

MENSAGEM_REAL = (
    "[ONNXRuntimeError] : 1 : FAIL : External data path validation failed for "
    "initializer: embeddings.word_embeddings.weight. Error: External data path "
    'escapes model directory. External data path: "model.onnx_data" resolved '
    'path: "/casa/projeto/.model_cache/blobs/9e/9eac14dff" allowed directory: '
    '"/casa/projeto/.model_cache/blobs/29"'
)


# ---------------------------------------------------------------------------
# A prevencao
# ---------------------------------------------------------------------------
def test_os_links_do_huggingface_ficam_desligados():
    """Sem isto, o cache volta a ser escrito em `blobs/` com links simbolicos.

    Tem de estar no settings e nao no ponto de uso: o `huggingface_hub` le esta
    variavel UMA VEZ, no import, e guarda numa constante de modulo. Definida
    depois de ele ter sido importado, nao tem efeito nenhum — verificado.
    """
    import core.settings.base  # noqa: F401 - o efeito acontece no import

    assert os.environ.get("HF_HUB_DISABLE_SYMLINKS") == "1"


# ---------------------------------------------------------------------------
# A traducao do erro
# ---------------------------------------------------------------------------
def test_o_erro_do_onnx_vira_instrucao():
    """A mensagem original fala de "external data path" e de dois diretorios de
    hash. Nao menciona cache, nem versao, nem o que fazer."""
    with override_settings(EMBEDDING_CACHE_DIR="/casa/projeto/.model_cache"):
        with pytest.raises(RuntimeError) as capturado:
            _traduzir_falha_de_carregamento(RuntimeError(MENSAGEM_REAL))

    texto = str(capturado.value)
    assert "rm -rf /casa/projeto/.model_cache" in texto
    # A mensagem original continua junto: quem for procurar na internet precisa
    # do texto exato do onnxruntime.
    assert "External data path validation failed" in texto


def test_outros_erros_passam_intactos():
    """Traduzir tudo esconderia a causa real de qualquer outra falha de
    carregamento — e "apague o cache" viraria o conselho para tudo."""
    assert _traduzir_falha_de_carregamento(RuntimeError("disco cheio")) is None


def test_um_cache_em_links_produz_de_fato_esse_erro(tmp_path):
    """A reproducao do layout, sem os 2 GB.

    Este teste nao carrega modelo nenhum: ele monta o formato do HuggingFace
    com arquivos minusculos e confirma que as duas partes caem em pastas
    diferentes — que e a condicao exata que o onnxruntime recusa. Serve para
    que a descricao acima continue verdadeira mesmo que ninguem se lembre dela.
    """
    snapshot = tmp_path / "models--intfloat--multilingual-e5-large" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    for pasta, nome, arquivo in (
        ("29", "29aaa", "model.onnx"),
        ("9e", "9eaaa", "model.onnx_data"),
    ):
        blob = tmp_path / "blobs" / pasta
        blob.mkdir(parents=True, exist_ok=True)
        (blob / nome).write_bytes(b"conteudo")
        (snapshot / arquivo).symlink_to(blob / nome)

    grafo = (snapshot / "model.onnx").resolve().parent
    pesos = (snapshot / "model.onnx_data").resolve().parent

    assert grafo != pesos, (
        "As duas partes deveriam cair em pastas diferentes; se este assert "
        "falhar, o formato do cache do HuggingFace mudou e a explicacao deste "
        "arquivo ficou desatualizada."
    )


# ---------------------------------------------------------------------------
# Contar token nao carrega 2 GB
# ---------------------------------------------------------------------------
def test_contar_tokens_nao_abre_a_sessao_onnx(tmp_path, monkeypatch):
    """A curadoria conta os tokens de CADA bloco.

    Antes, `_caminho_do_tokenizer` chamava `_carregar()` para so entao procurar
    o arquivo no disco — entao medir texto custava uma sessao ONNX de 2 GB, e
    qualquer defeito no carregamento do modelo virava erro 500 numa pagina que
    nao precisa do modelo para nada. Foi assim que o defeito acima apareceu.
    """
    tokenizer = tmp_path / "modelo" / "tokenizer.json"
    tokenizer.parent.mkdir(parents=True)
    tokenizer.write_text(_TOKENIZER_MINIMO, encoding="utf-8")

    cliente = FastEmbedClient()

    def nao_deveria_carregar():
        raise AssertionError("contar_tokens nao pode abrir a sessao ONNX")

    monkeypatch.setattr(cliente, "_carregar", nao_deveria_carregar)

    with override_settings(EMBEDDING_CACHE_DIR=str(tmp_path)):
        assert cliente.contar_tokens("qualquer coisa") > 0


def test_sem_tokenizer_no_disco_o_modelo_e_baixado(tmp_path, monkeypatch):
    """A ordem inverteu, mas o download nao pode ter sumido: numa maquina nova o
    arquivo nao esta la, e e o carregamento que dispara a busca."""
    cliente = FastEmbedClient()
    tentou = []

    def carregar_falso():
        tentou.append(True)
        alvo = Path(tmp_path) / "modelo" / "tokenizer.json"
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(_TOKENIZER_MINIMO, encoding="utf-8")

    monkeypatch.setattr(cliente, "_carregar", carregar_falso)

    with override_settings(EMBEDDING_CACHE_DIR=str(tmp_path)):
        assert cliente.contar_tokens("qualquer coisa") > 0

    assert tentou == [True]


# Tokenizer de nivel de palavra, o menor que a biblioteca `tokenizers` aceita
# carregar de arquivo. Nao representa o modelo real e nao precisa: o que se
# verifica aqui e de onde o arquivo veio, nao quantos tokens ele conta.
_TOKENIZER_MINIMO = """
{
  "version": "1.0",
  "truncation": null,
  "padding": null,
  "added_tokens": [],
  "normalizer": null,
  "pre_tokenizer": {"type": "Whitespace"},
  "post_processor": null,
  "decoder": null,
  "model": {
    "type": "WordLevel",
    "vocab": {"passage:": 0, "qualquer": 1, "coisa": 2, "[UNK]": 3},
    "unk_token": "[UNK]"
  }
}
"""
