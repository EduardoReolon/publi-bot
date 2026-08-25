"""Leitura tipada de variaveis de ambiente.

Regra do projeto: NENHUM valor de configuracao e hardcoded em settings. Tudo
que muda entre maquinas (dev, producao, CI) vem daqui, e tudo que existe esta
documentado em `.env.example`.

`require()` levanta na hora do import se a variavel faltar. Isso e deliberado:
e melhor o processo nao subir do que subir com um segredo vazio e falhar seis
horas depois, dentro de uma task, com uma mensagem que nao aponta para a causa.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


def load_env_file(base_dir: Path) -> None:
    """Carrega o `.env` da raiz do projeto, se existir.

    Variaveis ja presentes no ambiente real vencem as do arquivo — e o que
    permite o systemd (via EnvironmentFile) e o CI sobrescreverem sem editar
    arquivo nenhum.
    """
    load_dotenv(base_dir / ".env", override=False)


def get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def require(name: str) -> str:
    """Retorna a variavel ou levanta. Use para segredos e para tudo que nao
    tem default seguro."""
    value = os.environ.get(name)
    if value is None or value == "":
        raise ImproperlyConfigured(
            f"Variavel de ambiente obrigatoria ausente: {name}. "
            f"Veja .env.example para a lista completa."
        )
    return value


def boolean(name: str, default: bool = False) -> bool:
    """Aceita 1/true/yes/on em qualquer caixa. Qualquer outra coisa e False.

    Nao usar `bool(os.environ.get(...))`: a string "False" e truthy em Python,
    e esse engano ja ligou DEBUG em producao em projetos demais.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def integer(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} precisa ser um inteiro, veio {raw!r}") from exc


def decimal(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} precisa ser um numero, veio {raw!r}") from exc


def csv_list(name: str, default: str = "") -> list[str]:
    """Lista separada por virgula, sem itens vazios e sem espacos nas bordas."""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]
