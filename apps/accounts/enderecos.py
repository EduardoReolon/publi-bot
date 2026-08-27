"""Montagem de URLs absolutas entre o dominio raiz e os subdominios.

Estas funcoes existem para que a porta seja tratada num lugar so. A porta e a
parte que erra com facilidade e falha de forma confusa: um link para
`http://acme.publibot.localhost/` sem porta simplesmente nao abre no servidor
de desenvolvimento, e um link para a porta errada abre outra coisa.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest


def _sufixo_de_porta(request: HttpRequest) -> str:
    """A porta que o CLIENTE usou, extraida do cabecalho `Host`.

    Nao `request.get_port()`, que le `SERVER_PORT`: atras do nginx isso e a
    porta interna do gunicorn, nao a que o navegador acessou. Sem
    `USE_X_FORWARDED_PORT` — que este projeto nao define, porque o nginx
    repassa o `Host` original — um link montado com `get_port()` apontaria para
    `https://dominio:8000/`, uma porta que nao esta publicada.
    """
    _, _, porta = request.get_host().partition(":")
    return f":{porta}" if porta and porta not in {"80", "443"} else ""


def url_do_dominio_raiz(request: HttpRequest, caminho: str = "/") -> str:
    esquema = "https" if request.is_secure() else "http"
    return f"{esquema}://{settings.ROOT_DOMAIN}{_sufixo_de_porta(request)}{caminho}"


def url_do_tenant(request: HttpRequest, tenant, caminho: str = "/") -> str | None:
    """Endereco do painel de um tenant, ou `None` se ele nao tem dominio.

    O dominio vem do banco, nunca montado a partir do slug. Sao coisas
    diferentes de proposito: o slug usa hifen, o schema usa underscore, e um
    tenant pode ganhar um dominio proprio que nao deriva de nenhum dos dois.
    """
    dominio = next((d for d in tenant.domains.all() if d.is_primary), None)
    if dominio is None:
        return None
    esquema = "https" if request.is_secure() else "http"
    return f"{esquema}://{dominio.domain}{_sufixo_de_porta(request)}{caminho}"
