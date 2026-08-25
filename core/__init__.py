"""Pacote raiz do projeto.

O import abaixo garante que a app do Celery exista assim que o Django carregar
— e o que habilita o decorador @shared_task nos apps. Sem ele, `celery -A core`
sobe, mas o processo web nao consegue despachar task nenhuma.
"""

from core.celery import app as celery_app

__all__ = ("celery_app",)
