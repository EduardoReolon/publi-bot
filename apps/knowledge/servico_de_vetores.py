"""Servico de vetores: um processo so com o modelo de embedding aberto.

Os demais processos (Gunicorn, Celery) pedem os vetores aqui por HTTP
(`ServicoDeVetoresClient`), no mesmo dialeto do worker da placa
(`POST /v1/embeddings`, docs/WORKER_VETORIZACAO.md): `input` vem pronto, com o
prefixo que o modelo deve ver, e nada e acrescentado.

Escuta so em 127.0.0.1 e sem senha: nao sai da maquina. O modelo atende um
pedido por vez (trava); enquanto carrega, responde 503 com Retry-After, e quem
pediu trata como `VetorizacaoAdiada`.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from django.conf import settings

from apps.knowledge.embeddings import FastEmbedClient

log = logging.getLogger(__name__)

MAXIMO_DE_ENTRADAS = 256
MAXIMO_DE_BYTES = 8 * 1024 * 1024


class Servico:
    def __init__(self, cliente=None):
        self.cliente = cliente or FastEmbedClient()
        self.pronto = threading.Event()
        self.erro = ""
        self.trava = threading.Lock()

    def carregar(self) -> None:
        try:
            self.cliente._carregar()
        except Exception as exc:
            self.erro = str(exc)
            log.exception("servico de vetores: o modelo nao carregou")
            return
        self.pronto.set()
        log.info("servico de vetores: modelo %s pronto", self.cliente.model_name)

    def vetorizar(self, entradas: list[str]) -> list[list[float]]:
        with self.trava:
            return self.cliente.vetorizar_preparados(entradas)


def manipulador(servico: Servico):
    class Manipulador(BaseHTTPRequestHandler):
        def log_message(self, formato, *args):  # o acesso vai para o log, sem ruido
            log.debug(formato, *args)

        def _responder(self, codigo: int, corpo: dict, cabecalhos: dict | None = None):
            dados = json.dumps(corpo).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados)))
            for nome, valor in (cabecalhos or {}).items():
                self.send_header(nome, valor)
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self):
            if urlsplit(self.path).path != "/saude":
                return self._responder(404, {"erro": "rota desconhecida"})
            self._responder(
                200,
                {
                    "modelo": servico.cliente.model_name,
                    "pronto": servico.pronto.is_set(),
                    "erro": servico.erro,
                },
            )

        def do_POST(self):
            if urlsplit(self.path).path != "/v1/embeddings":
                return self._responder(404, {"erro": "rota desconhecida"})
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho <= 0 or tamanho > MAXIMO_DE_BYTES:
                return self._responder(413, {"erro": "corpo vazio ou grande demais"})
            try:
                pedido = json.loads(self.rfile.read(tamanho))
            except ValueError:
                return self._responder(400, {"erro": "JSON invalido"})
            entradas = pedido.get("input")
            if isinstance(entradas, str):
                entradas = [entradas]
            if (
                not isinstance(entradas, list)
                or not entradas
                or len(entradas) > MAXIMO_DE_ENTRADAS
                or not all(isinstance(e, str) for e in entradas)
            ):
                return self._responder(
                    400, {"erro": f"input: lista de 1 a {MAXIMO_DE_ENTRADAS} textos"}
                )
            modelo = servico.cliente.model_name
            if pedido.get("model") and pedido["model"] != modelo:
                return self._responder(400, {"erro": f"este servico vetoriza com {modelo}"})
            if not servico.pronto.is_set():
                return self._responder(
                    503, {"erro": servico.erro or "carregando o modelo"}, {"Retry-After": "30"}
                )
            vetores = servico.vetorizar(entradas)
            self._responder(
                200,
                {
                    "object": "list",
                    "model": modelo,
                    "data": [
                        {"object": "embedding", "index": i, "embedding": v}
                        for i, v in enumerate(vetores)
                    ],
                },
            )

    return Manipulador


def endereco() -> tuple[str, int]:
    partes = urlsplit(settings.EMBEDDING_SERVICO_URL)
    return partes.hostname or "127.0.0.1", partes.port or 8601


def servir() -> None:
    servico = Servico()
    # Carrega em paralelo: /saude e o 503 respondem desde o primeiro segundo.
    threading.Thread(target=servico.carregar, daemon=True).start()
    servidor = ThreadingHTTPServer(endereco(), manipulador(servico))
    servidor.daemon_threads = True
    log.info("servico de vetores ouvindo em %s:%s", *endereco())
    servidor.serve_forever()
