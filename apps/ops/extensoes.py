"""Pontos de extensao: modulos que se ligam ao nucleo sem o nucleo conhece-los.

O nucleo (pautas, acervo, artigos, publicacao) nao importa nenhum modulo
opcional. Um modulo declara, no proprio AppConfig, o que acrescenta, e o nucleo
le essas declaracoes daqui:

    class MeuModuloConfig(AppConfig):
        urls = ("redes/", "apps.meu.urls", "meu")           # prefixo, urlconf, namespace
        menu = [{"rotulo": "Redes", "url": "meu:inicio", "aba": "redes",
                 "pendencia": "redes", "depois_de": "artigos"}]
        prompts_iniciais = "apps.meu.prompts.PROMPTS"     # caminho pontilhado
        prompts_com_guia = {"meu_prompt"}                 # recebem o guia editorial
        blocos_do_artigo = ["meu/_no_artigo.html"]        # incluidos na tela do artigo
        pendencias = "apps.meu.painel.pendencias"         # () -> {"chave": numero}

Tirar o modulo de INSTALLED_APPS tira tudo junto: o nucleo continua igual.
"""

from __future__ import annotations

import logging
from functools import cache

from django.apps import apps
from django.utils.module_loading import import_string

logger = logging.getLogger("publibot.ops")


def _declaracoes(atributo: str) -> list:
    return [
        getattr(config, atributo)
        for config in apps.get_app_configs()
        if getattr(config, atributo, None)
    ]


def rotas() -> list[tuple[str, str, str]]:
    """(prefixo, urlconf, namespace) de cada modulo."""
    return _declaracoes("urls")


def menus() -> list[dict]:
    return [item for lista in _declaracoes("menu") for item in lista]


@cache
def prompts_iniciais() -> dict:
    """As sementes de prompt do nucleo e as de cada modulo, numa lista so."""
    from apps.content.prompts_iniciais import PROMPTS_INICIAIS

    todos = dict(PROMPTS_INICIAIS)
    for caminho in _declaracoes("prompts_iniciais"):
        for chave, dados in import_string(caminho).items():
            if chave in todos:
                raise ValueError(f"prompt {chave!r} declarado duas vezes ({caminho}).")
            todos[chave] = dados
    return todos


def prompts_com_guia() -> frozenset:
    return frozenset(chave for conjunto in _declaracoes("prompts_com_guia") for chave in conjunto)


def blocos_do_artigo() -> list[str]:
    return [bloco for lista in _declaracoes("blocos_do_artigo") for bloco in lista]


def pendencias() -> dict:
    """As contagens do menu que cada modulo acrescenta. Um modulo com
    problema nao derruba o menu de todas as telas."""
    saida: dict = {}
    for caminho in _declaracoes("pendencias"):
        try:
            saida.update(import_string(caminho)() or {})
        except Exception:
            logger.exception("Pendencias de %s indisponiveis.", caminho)
    return saida
