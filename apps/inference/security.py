"""Cifragem de credenciais de terceiros.

Uma chave de API de cliente guardada em texto puro significa que um dump do
banco — ou um backup que vaze — entrega acesso de escrita ao ambiente de cada
cliente de uma vez so. O vazamento e permanente: invalidar exigiria editar a
configuracao de cada um, manualmente.

A chave de cifra vive FORA do banco, em `NODE_KEY_ENCRYPTION_KEY`. Isso e o que
faz a medida valer alguma coisa: guardada junto, seria apenas ofuscacao.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ImproperlyConfigured

from core import env


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    chave = env.get("NODE_KEY_ENCRYPTION_KEY")
    if not chave:
        raise ImproperlyConfigured(
            "NODE_KEY_ENCRYPTION_KEY nao definida. Gere uma com:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(chave.encode() if isinstance(chave, str) else chave)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "NODE_KEY_ENCRYPTION_KEY invalida: precisa ser uma chave Fernet "
            "(32 bytes em base64 url-safe)."
        ) from exc


def cifrar(valor: str) -> bytes:
    return _fernet().encrypt(valor.encode())


def decifrar(dados: bytes | memoryview | None) -> str | None:
    if not dados:
        return None
    if isinstance(dados, memoryview):
        dados = dados.tobytes()
    try:
        return _fernet().decrypt(dados).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Nao foi possivel decifrar a credencial. A NODE_KEY_ENCRYPTION_KEY "
            "mudou? Sem a chave original, os valores guardados sao irrecuperaveis."
        ) from exc


def guardar_chave(objeto, valor: str, *, campo: str = "api_key_ciphertext") -> None:
    """Cifra e guarda, registrando os ultimos 4 caracteres para identificacao.

    Os ultimos 4 permitem a uma pessoa confirmar *qual* chave esta cadastrada
    sem que o sistema precise exibi-la.

    **Nao grava no banco**: apenas preenche os campos do objeto. Quem chama
    decide quando salvar — no admin isso acontece no `save_model` seguinte.
    """
    setattr(objeto, campo, cifrar(valor))
    if hasattr(objeto, "api_key_last4"):
        objeto.api_key_last4 = valor[-4:]


def decifrar_chave(objeto, *, campo: str = "api_key_ciphertext") -> str | None:
    return decifrar(getattr(objeto, campo, None))
