"""Cliente de embeddings.

A interface expoe apenas `embed_query()` e `embed_passage()`. **Nao existe um
`embed()` cru, de proposito.**

O modelo em uso, `intfloat/multilingual-e5-large`, exige que o texto seja
prefixado com `query: ` ou `passage: ` conforme o papel. Esquecer o prefixo NAO
levanta erro: apenas derruba a revocacao, em silencio, de forma que so
apareceria como "o RAG nao acha nada bom" semanas depois. Tornar impossivel
chamar sem prefixo e mais barato que confiar em disciplina.

O modelo tambem trunca em 512 tokens sem avisar, entao `contar_tokens()` usa o
tokenizador real e nao uma estimativa por caracteres.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np
from django.conf import settings


class EmbeddingClient(ABC):
    """Contrato de embeddings.

    Query e passagem sao metodos separados porque, neste modelo, sao operacoes
    genuinamente diferentes — nao e uma formalidade.
    """

    model_name: str
    dimensions: int

    @abstractmethod
    def embed_query(self, texto: str) -> list[float]:
        """Vetoriza uma CONSULTA (o que o usuario procura)."""

    @abstractmethod
    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        """Vetoriza PASSAGENS (o que esta no corpus)."""

    def embed_queries(self, textos: list[str]) -> list[list[float]]:
        """Varias CONSULTAS de uma vez. Por padrao, uma a uma; os clientes reais
        mandam em lote (centenas de titulos numa chamada so, e nao centenas)."""
        return [self.embed_query(t) for t in textos]

    @abstractmethod
    def contar_tokens(self, texto: str) -> int:
        """Conta tokens com o tokenizador real do modelo."""


def _normalizar(vetor) -> list[float]:
    """Normaliza em L2.

    Verificado: este modelo devolve vetores com norma em torno de 29, nao 1.
    Normalizar na gravacao mantem a distancia de cosseno consistente e permite
    comparar valores entre execucoes.
    """
    arr = np.asarray(vetor, dtype=np.float32)
    norma = np.linalg.norm(arr)
    if norma == 0:
        return arr.tolist()
    return (arr / norma).tolist()


class FastEmbedClient(EmbeddingClient):
    """Implementacao com fastembed (ONNX em CPU, sem torch).

    Roda na nuvem e nao na GPU: indexar um documento pode esperar a GPU acordar,
    mas CONSULTAR nao pode. Se o embedding vivesse so na maquina local, buscar
    no indice com ela desligada seria impossivel (ADR-0005).
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.dimensions = settings.EMBEDDING_DIM
        self._modelo = None
        self._tokenizer = None
        self._trava = threading.Lock()

    def _carregar(self):
        # Carga preguicosa e com trava: o modelo ocupa cerca de 2 GB e leva
        # alguns segundos para subir. Carregar no import faria todo comando de
        # gerenciamento pagar esse custo.
        if self._modelo is None:
            with self._trava:
                if self._modelo is None:
                    from fastembed import TextEmbedding

                    try:
                        self._modelo = TextEmbedding(
                            self.model_name,
                            cache_dir=settings.EMBEDDING_CACHE_DIR,
                            local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
                        )
                    except Exception as erro:
                        _traduzir_falha_de_carregamento(erro)
                        raise
        return self._modelo

    def embed_query(self, texto: str) -> list[float]:
        # O `query_embed` do fastembed, para este modelo, e o mesmo `embed` sem
        # prefixo nenhum — e o limiar `RAG_MAX_COSINE_DISTANCE` foi medido assim.
        return self.vetorizar_preparados([texto])[0]

    def embed_queries(self, textos: list[str]) -> list[list[float]]:
        return self.vetorizar_preparados(list(textos)) if textos else []

    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        if not textos:
            return []
        # `passage: ` e obrigatorio para este modelo; o metodo `embed` do
        # fastembed nao o adiciona sozinho.
        return self.vetorizar_preparados([f"passage: {t}" for t in textos])

    def vetorizar_preparados(self, entradas: list[str]) -> list[list[float]]:
        """Vetoriza textos que ja vem como o modelo deve recebe-los (com ou sem
        prefixo). E o que o servico de vetores roda para os outros processos."""
        modelo = self._carregar()
        return [_normalizar(v) for v in modelo.embed(entradas)]

    def contar_tokens(self, texto: str) -> int:
        if self._tokenizer is None:
            from tokenizers import Tokenizer

            caminho = self._caminho_do_tokenizer()
            self._tokenizer = Tokenizer.from_file(str(caminho))
        # Conta sobre o texto ja prefixado: e isso que o modelo recebe, e o
        # prefixo tambem ocupa tokens do orcamento de 512.
        return len(self._tokenizer.encode(f"passage: {texto}").ids)

    def _caminho_do_tokenizer(self):
        """Onde esta o `tokenizer.json`, baixando o modelo se preciso.

        Procura no disco ANTES de chamar `_carregar()`, e essa ordem importa.
        Contar tokens precisa de um arquivo de 17 MB; `_carregar()` abre uma
        sessao ONNX de 2 GB. Como a curadoria conta os tokens de cada bloco, a
        ordem invertida fazia a tela pagar o modelo inteiro para medir texto — e
        transformava qualquer defeito de carregamento do ONNX em erro 500 numa
        pagina que nao precisava do modelo para nada.
        """
        from pathlib import Path

        base = Path(settings.EMBEDDING_CACHE_DIR)
        candidatos = sorted(base.glob("**/tokenizer.json"))
        if not candidatos:
            # Nao esta em disco: ai sim vale carregar, porque e o carregamento
            # que dispara o download.
            self._carregar()
            candidatos = sorted(base.glob("**/tokenizer.json"))

        if not candidatos:
            raise FileNotFoundError(
                f"tokenizer.json nao encontrado em {base}. O modelo foi baixado?"
            )
        return candidatos[0]


def _traduzir_falha_de_carregamento(erro: Exception) -> None:
    """Levanta um erro com instrucao quando a causa e o cache em links.

    A mensagem original fala de "external data path" e de dois diretorios de
    hash, e nao menciona cache em lugar nenhum:

        FAIL : External data path validation failed for initializer:
        embeddings.word_embeddings.weight. Error: External data path escapes
        model directory. ... allowed directory: ".model_cache/blobs/29"

    O que aconteceu: os dois arquivos do modelo (`model.onnx` e os 2 GB de
    `model.onnx_data`) foram guardados no formato do HuggingFace, cada um numa
    pasta `blobs/<hash>` diferente, com links simbolicos apontando para la — e o
    onnxruntime recusa uma segunda parte que esteja fora da pasta da primeira.

    `HF_HUB_DISABLE_SYMLINKS` (ligado em `core/settings/base.py`) impede que
    isso volte a acontecer, mas nao conserta um cache ja escrito assim: os
    arquivos ja estao no disco e nada sera baixado de novo. Por isso a saida e
    apagar a pasta.
    """
    texto = str(erro)
    if "External data path" not in texto and "escapes model directory" not in texto:
        return

    raise RuntimeError(
        f"O cache do modelo de embedding esta no formato de links do "
        f"HuggingFace, que o onnxruntime recusa desde a versao 1.22.\n\n"
        f"Apague o cache e deixe baixar de novo (~2 GB):\n"
        f"    rm -rf {settings.EMBEDDING_CACHE_DIR}\n\n"
        f"O download seguinte ja vem no formato certo: "
        f"`HF_HUB_DISABLE_SYMLINKS` esta ligado no settings.\n\n"
        f"Mensagem original: {texto}"
    ) from erro


class ServicoDeVetoresClient(FastEmbedClient):
    """Pede os vetores ao servico de vetores da maquina (`manage.py
    servir_vetores`), em vez de abrir o modelo no proprio processo.

    O modelo ocupa cerca de 2 GB *por processo*. Cada worker do Gunicorn e cada
    processo do Celery que vetorizasse abriria a sua copia: numa VM pequena,
    tres ou quatro copias esgotam a memoria e a maquina inteira trava. Com o
    servico, ha uma copia so, e os vetores saem identicos aos do
    `FastEmbedClient` — o servico roda o mesmo codigo.

    Servico fora do ar (subindo, reiniciando) e `VetorizacaoAdiada`: a
    indexacao volta para a fila; a busca falha com a mensagem.
    """

    LOTE = 32

    def embed_query(self, texto: str) -> list[float]:
        return self._pedir([texto], prazo=60)[0]

    def embed_queries(self, textos: list[str]) -> list[list[float]]:
        vetores = []
        for inicio in range(0, len(textos), self.LOTE):
            vetores += self._pedir(list(textos[inicio : inicio + self.LOTE]), prazo=120)
        return vetores

    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        vetores = []
        for inicio in range(0, len(textos), self.LOTE):
            lote = textos[inicio : inicio + self.LOTE]
            vetores += self._pedir([f"passage: {t}" for t in lote], prazo=600)
        return vetores

    def _pedir(self, entradas: list[str], *, prazo: float) -> list[list[float]]:
        import httpx

        try:
            resposta = httpx.post(
                f"{settings.EMBEDDING_SERVICO_URL.rstrip('/')}/v1/embeddings",
                json={"model": self.model_name, "input": entradas},
                timeout=httpx.Timeout(prazo, connect=5.0),
            )
        except httpx.TransportError as exc:
            raise VetorizacaoAdiada(
                f"o servico de vetores nao respondeu ({type(exc).__name__}); "
                "confira `systemctl status vetores-publibot`."
            ) from exc
        if resposta.status_code == 503:
            from apps.inference.providers.openai_compatible import _retry_after

            raise VetorizacaoAdiada(
                "o servico de vetores ainda esta carregando o modelo.",
                retry_after=_retry_after(resposta),
            )
        resposta.raise_for_status()
        return _vetores_da_resposta(resposta.json(), len(entradas), origem="o servico de vetores")

    def _carregar(self):
        # So o tokenizador (17 MB) e lido aqui, para contar tokens. O modelo
        # inteiro nunca: e exatamente o que este cliente existe para evitar.
        raise RuntimeError(
            "tokenizer.json nao esta no cache do modelo. Ele vem com o download "
            "que o servico de vetores faz ao subir (`manage.py servir_vetores`)."
        )


class FakeEmbeddingClient(EmbeddingClient):
    """Cliente deterministico para testes.

    Nao e um atalho: e o que permite testar a logica de recuperacao, os
    limiares e o fluxo de curadoria sem carregar 2 GB de modelo a cada execucao
    da suite. Os vetores sao derivados do hash do texto, entao o mesmo texto
    sempre produz o mesmo vetor e textos diferentes produzem vetores
    diferentes — que e tudo o que esses testes precisam.
    """

    def __init__(self, model_name: str = "fake-determinista", dimensions: int | None = None):
        self.model_name = model_name
        self.dimensions = dimensions or settings.EMBEDDING_DIM

    def _vetor(self, texto: str) -> list[float]:
        import hashlib

        semente = int.from_bytes(hashlib.sha256(texto.encode()).digest()[:8], "big")
        rng = np.random.default_rng(semente)
        return _normalizar(rng.standard_normal(self.dimensions))

    def embed_query(self, texto: str) -> list[float]:
        return self._vetor(texto)

    def embed_passage(self, textos: list[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    def contar_tokens(self, texto: str) -> int:
        # Aproximacao suficiente para teste; o cliente real usa o tokenizador.
        return max(1, len(texto) // 4)


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """Cliente configurado, reaproveitado entre chamadas.

    O cache existe porque cada instanciacao do modelo real custa segundos e
    cerca de 2 GB de memoria.
    """
    from django.utils.module_loading import import_string

    classe = import_string(settings.EMBEDDING_CLIENT)
    return classe()


class VetorizacaoAdiada(RuntimeError):
    """O worker nao pode vetorizar agora (ocupado, desligado, baixando o
    modelo). Nao e erro: o trabalho volta para a fila."""

    def __init__(self, mensagem: str, *, retry_after: int | None = None):
        super().__init__(mensagem)
        self.retry_after = retry_after


def conexao_de_vetorizacao():
    """A conexao do worker que vetoriza, se alguma foi marcada para isso."""
    from apps.inference.models import InferenceConnection

    for conexao in InferenceConnection.objects.filter(is_active=True).order_by("created_at"):
        if conexao.atende(InferenceConnection.Workload.EMBEDDING) and not conexao.circuito_aberto:
            return conexao
    return None


def _no_worker(conexao, textos: list[str], *, dono: str) -> list[list[float]]:
    """`POST /v1/embeddings` no dialeto da OpenAI (docs/WORKER_VETORIZACAO.md).

    Os textos vao ja com o prefixo `passage: `: o worker nao acrescenta nada, e
    assim o vetor sai igual ao do servidor.
    """
    import httpx

    from apps.inference.leases import SemCapacidade, reserva
    from apps.inference.providers.openai_compatible import _retry_after
    from apps.inference.security import decifrar_chave

    segredo = decifrar_chave(conexao) or ""
    try:
        with reserva(conexao, owner_key=dono):
            resposta = httpx.post(
                f"{conexao.base_url.rstrip('/')}/v1/embeddings",
                json={
                    "model": settings.EMBEDDING_MODEL,
                    "input": [f"passage: {t}" for t in textos],
                },
                headers={"Authorization": f"Bearer {segredo}"},
                # Conectar e rapido ou nao vai: maquina desligada atras de VPN
                # pode engolir o pacote e prender a tarefa ate o fim do prazo.
                timeout=httpx.Timeout(600.0, connect=10.0),
            )
    except SemCapacidade as exc:
        raise VetorizacaoAdiada(f"a maquina da placa esta ocupada: {exc}") from exc
    except httpx.TransportError as exc:
        raise VetorizacaoAdiada(f"worker inalcancavel: {type(exc).__name__}") from exc

    if resposta.status_code == 503:
        raise VetorizacaoAdiada(
            "o worker pediu para esperar (placa ocupada ou modelo carregando).",
            retry_after=_retry_after(resposta),
        )
    if resposta.status_code == 404:
        raise VetorizacaoAdiada(
            "o worker nao tem a rota /v1/embeddings (docs/WORKER_VETORIZACAO.md)."
        )
    resposta.raise_for_status()
    return _vetores_da_resposta(resposta.json(), len(textos), origem="o worker")


def _vetores_da_resposta(dados: dict, quantos: int, *, origem: str) -> list[list[float]]:
    """Le a resposta de `/v1/embeddings` (worker da placa ou servico local)."""
    if dados.get("model") and dados["model"] != settings.EMBEDDING_MODEL:
        # Vetor de outro modelo no mesmo indice estraga a busca sem aviso.
        raise RuntimeError(
            f"{origem} vetorizou com {dados['model']}, e o indice usa {settings.EMBEDDING_MODEL}."
        )
    vetores = [item["embedding"] for item in sorted(dados["data"], key=lambda i: i["index"])]
    if len(vetores) != quantos or any(len(v) != settings.EMBEDDING_DIM for v in vetores):
        raise RuntimeError(f"{origem} devolveu vetores em numero ou tamanho errado.")
    return [_normalizar(v) for v in vetores]


def vetorizar_passagens(
    textos: list[str], *, permitir_local: bool = True, dono: str = "vetorizacao"
) -> list[list[float]]:
    """Vetoriza passagens no worker da placa, se houver um marcado para isso;
    senao (ou se ele nao puder agora e `permitir_local`), no servidor.

    Sem worker cadastrado, vetoriza no servidor (como sempre foi). Com worker que
    nao pode agora e sem `permitir_local`, levanta `VetorizacaoAdiada`: quem
    chamou tenta de novo depois, ou oferece "vetorizar agora no servidor".
    """
    if not textos:
        return []
    conexao = conexao_de_vetorizacao()
    if conexao is not None:
        try:
            return _no_worker(conexao, textos, dono=dono)
        except VetorizacaoAdiada:
            if not permitir_local:
                raise
    return get_embedding_client().embed_passage(textos)
