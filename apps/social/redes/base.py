"""O que toda rede declara, e a interface comum de publicacao.

Cada rede mora num modulo proprio (`linkedin.py`, `instagram.py`, `gmn.py`)
com tres coisas: o FORMATO (limites e onde vai o link), o ESTILO (o que o
publico de la espera, que vai para o pedido ao modelo e para a tela) e o
PUBLICADOR (a API). Mudar como se escreve para o Instagram e mexer so no
modulo do Instagram — ou, sem deploy, no prompt `social_instagram` pela tela.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ErroDaRede(Exception):
    """A rede recusou ou falhou. A mensagem vai para a tela como esta."""


class NaoConectado(ErroDaRede):
    """O destino nao tem conta conectada (ou a API nao foi liberada): o post
    sai pelo 'Copiar para postar'."""


class SemSuporte(ErroDaRede):
    """A rede (ou o tipo de conta) nao oferece isto pela API."""


@dataclass(frozen=True)
class Formato:
    max_caracteres: int
    max_hashtags: int
    # Onde vai o link do artigo: "texto", "comentario" (primeiro comentario),
    # "botao" (botao do proprio post) ou "bio" (sem link clicavel no post).
    link: str
    # "capa" (a capa do artigo), "laminas" (carrossel montado aqui) ou "nenhuma".
    imagem: str
    min_laminas: int = 0
    max_laminas: int = 0
    # Quantos caracteres aparecem antes do "ver mais": o gancho tem de caber.
    dobra: int = 0


@dataclass(frozen=True)
class Rede:
    codigo: str
    nome: str
    formato: Formato
    # Para a tela e para o pedido ao modelo: como e o publico e o que funciona.
    estilo: str
    publico_padrao: str
    tom_padrao: str
    # (codigo, rotulo) dos tipos de conta: perfil pessoal, pagina da empresa...
    autores: tuple = ()
    # Itens de "o que o dono precisa fazer" para a API funcionar.
    preparo: tuple = ()
    publicador: type | None = None
    oauth: object | None = None
    extras: dict = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return f"social_{self.codigo}"


@dataclass
class ImagemPublica:
    """Uma imagem que a rede busca por URL (Instagram, Google) ou que o
    publicador envia em bytes (LinkedIn)."""

    url: str
    caminho: str  # no storage


@dataclass
class Publicado:
    id_remoto: str
    url: str = ""


@dataclass
class ComentarioLido:
    id_remoto: str
    texto: str
    autor: str = ""
    escrito_em: str = ""  # ISO
    # Resposta do proprio dono da conta: nao e comentario a tratar.
    do_dono: bool = False


class Publicador:
    """A API de uma rede para um destino. `http` e um httpx.Client (os testes
    passam um com MockTransport)."""

    def __init__(self, destino, *, http=None):
        import httpx

        self.destino = destino
        self.http = http or httpx.Client(timeout=60.0)

    def credenciais(self) -> dict:
        if not self.destino.conectado:
            raise NaoConectado(f"{self.destino.nome}: conta nao conectada.")
        return self.destino.ler_credenciais()

    def publicar(self, post, texto: str, imagens: list[ImagemPublica]) -> Publicado:
        raise NotImplementedError

    def comentar(self, post, texto: str) -> str:
        raise SemSuporte("esta rede nao recebe comentario pela API.")

    def ler_comentarios(self, post) -> list[ComentarioLido]:
        return []

    def responder(self, post, id_do_comentario: str, texto: str) -> str:
        raise SemSuporte("esta rede nao responde comentarios pela API.")

    def metricas(self, post) -> dict:
        return {}

    def seguidores(self) -> int | None:
        """Quantos seguem a conta (None: a rede nao informa para este tipo de conta)."""
        return None

    def referencias(self, hashtag: str, limite: int = 25) -> list[dict]:
        """Os posts de outras contas que mais engajam numa hashtag."""
        raise SemSuporte("esta rede nao mostra os posts de uma hashtag pela API.")

    def _conferir(self, resposta, contexto: str):
        """Levanta ErroDaRede com a mensagem da rede (sem o token)."""
        if resposta.status_code >= 400:
            try:
                corpo = resposta.json()
            except ValueError:
                corpo = resposta.text[:500]
            raise ErroDaRede(f"{contexto}: HTTP {resposta.status_code} — {corpo}")
        return resposta
