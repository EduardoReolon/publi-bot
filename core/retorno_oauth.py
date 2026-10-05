"""Um endereco de retorno so, no dominio raiz, para todas as conexoes OAuth.

LinkedIn, Meta e Google so aceitam devolver o usuario para enderecos
cadastrados EXATAMENTE no app. Com um cliente por subdominio, cada cliente
novo exigiria cadastrar mais um endereco no app de cada rede. Em vez disso,
todo pedido de conexao usa `https://<dominio raiz>/redes/retorno/`, e o
`state` assinado diz para qual cliente (schema) e para qual pagina dele o
retorno segue. O token so e trocado la, no cliente, com este mesmo endereco.
"""

from __future__ import annotations

from django.conf import settings
from django.core import signing

SAL = "oauth-retorno"
VALIDADE = 900  # segundos entre sair para a rede e voltar
CAMINHO = "/redes/retorno/"


def endereco_de_retorno() -> str:
    """O endereco para cadastrar em todos os apps (e para mandar na conexao)."""
    return f"{settings.ESQUEMA_PUBLICO}://{settings.ROOT_DOMAIN}{CAMINHO}"


def assinar(schema: str, volta: str, **extra) -> str:
    """O `state`: o cliente, a pagina dele que recebe o codigo e o que mais
    o modulo precisar de volta."""
    return signing.dumps({"schema": schema, "volta": volta, **extra}, salt=SAL)


def ler(state: str) -> dict:
    """Levanta signing.BadSignature se vencido ou forjado."""
    return signing.loads(state or "", salt=SAL, max_age=VALIDADE)
