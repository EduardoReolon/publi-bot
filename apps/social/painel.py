"""Contagens para o menu (via apps/ops/extensoes.py)."""

from __future__ import annotations


def pendencias() -> dict:
    from apps.social.models import Post

    return {"redes": Post.objects.filter(situacao=Post.Situacao.RASCUNHO).count()}
