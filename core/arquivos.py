"""Entrega de arquivo guardado em `MEDIA_ROOT`.

Vive aqui, e nao dentro de um app, porque dois apps entregam arquivo: a capa
publica de um artigo (`apps.content`) e o PDF original de um documento
(`apps.knowledge`). Duplicar dez linhas seria barato; duplicar a decisao sobre
X-Accel nao — ela e a diferenca entre responder em milissegundos e prender um
processo do Gunicorn por download.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse


def entregar_arquivo(arquivo, *, tipo: str, nome_para_baixar: str = "") -> HttpResponse:
    """Delega ao Nginx quando ha um na frente; streama quando nao ha.

    Streamar pelo worker prende um processo do Gunicorn durante todo o
    download. Com o `X-Accel-Redirect` o Python responde na hora e quem envia
    os bytes e o servidor de arquivos, que existe para isso.

    `nome_para_baixar` transforma a resposta em download com nome proprio, em
    vez de o navegador derivar um nome da URL — que aqui e um UUID.

    Ele nao devolve o nome que a pessoa enviou: o `Document` guarda so o
    caminho no disco, e o Django ja acrescentou ali um sufixo contra colisao
    (`matriz_rfm_B6VZych.pdf`). O que se ganha e o arquivo chegar como
    `matriz_rfm_B6VZych.pdf` e nao como `a1b2c3d4-...`, sem extensao, que o
    sistema operacional nao saberia abrir.
    """
    if nome_para_baixar:
        disposicao = f'attachment; filename="{_sanear(nome_para_baixar)}"'
    else:
        disposicao = ""

    if settings.USAR_X_ACCEL:
        resposta = HttpResponse(content_type=tipo)
        resposta["X-Accel-Redirect"] = (
            f"{settings.PREFIXO_X_ACCEL}{caminho_sob_media_root(arquivo)}"
        )
        if disposicao:
            resposta["Content-Disposition"] = disposicao
        return resposta

    resposta = FileResponse(arquivo.open("rb"), content_type=tipo)
    if disposicao:
        resposta["Content-Disposition"] = disposicao
    return resposta


def caminho_sob_media_root(arquivo) -> str:
    """O caminho do arquivo a partir de `MEDIA_ROOT`, que e o que o Nginx serve.

    NAO e `arquivo.name`: o storage por tenant grava em
    `MEDIA_ROOT/<schema>/documents/...`, e o `name` e relativo a pasta do
    tenant (`documents/...`). Mandar o `name` ao Nginx pede um arquivo que nao
    existe, e o 404 nao diz por que.
    """
    return Path(arquivo.path).resolve().relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()


def _sanear(nome: str) -> str:
    """Tira do nome o que quebraria o cabecalho HTTP.

    Aspas e quebra de linha num `Content-Disposition` nao sao detalhe estetico:
    e assim que se injeta cabecalho numa resposta. O nome vem do arquivo que
    alguem enviou, entao e entrada externa.
    """
    limpo = nome.replace('"', "").replace("\\", "")
    limpo = "".join(c for c in limpo if c.isprintable() and c not in "\r\n")
    return limpo.strip() or "arquivo"
