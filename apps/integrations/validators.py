"""Validacao da URL de um site externo.

A URL vem de quem cadastra o site, e o servidor vai fazer requisicoes para ela.
Isso e, por definicao, uma superficie de SSRF: sem restricao, alguem poderia
cadastrar `http://169.254.169.254/` e usar o proprio sistema como intermediario
para alcancar o servico de metadados da nuvem, ou varrer a rede interna.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Nomes que nunca devem ser destino, mesmo que resolvam para algo publico.
NOMES_PROIBIDOS = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


def validar_url_de_site(valor: str) -> None:
    """Exige HTTPS e recusa destinos internos."""
    partes = urlparse(valor)

    if partes.scheme != "https":
        raise ValidationError(
            _(
                "O endereco precisa usar https. Sem TLS, a chave de API e todo o "
                "conteudo trafegam em texto claro."
            )
        )

    anfitriao = (partes.hostname or "").lower()
    if not anfitriao:
        raise ValidationError(_("Endereco sem host."))

    if anfitriao in NOMES_PROIBIDOS:
        raise ValidationError(_("Este endereco nao pode ser usado."))

    for endereco in _resolver(anfitriao):
        if not endereco.is_global or endereco.is_reserved:
            raise ValidationError(
                _(
                    "O endereco resolve para uma rede interna (%(ip)s). Sites "
                    "externos precisam estar na internet publica."
                )
                % {"ip": endereco}
            )


def _resolver(anfitriao: str) -> list[ipaddress._BaseAddress]:
    """Resolve o nome para IPs.

    Um nome pode apontar para um endereco interno — checar apenas a string
    deixaria passar `interno.exemplo.com` apontando para 10.0.0.5.
    """
    try:
        literal = ipaddress.ip_address(anfitriao)
    except ValueError:
        pass
    else:
        return [literal]

    try:
        informacoes = socket.getaddrinfo(anfitriao, None)
    except socket.gaierror:
        # Nome que nao resolve agora nao e motivo para recusar o cadastro: um
        # DNS temporariamente fora do ar bloquearia um site legitimo.
        return []

    encontrados = []
    for familia, _tipo, _proto, _nome, endereco_socket in informacoes:
        if familia in (socket.AF_INET, socket.AF_INET6):
            try:
                encontrados.append(ipaddress.ip_address(endereco_socket[0]))
            except ValueError:
                continue
    return encontrados
