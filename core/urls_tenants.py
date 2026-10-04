"""Rotas de um tenant — `<slug>.<ROOT_DOMAIN>`.

Este e o urlconf padrao (ROOT_URLCONF). O django-tenants o utiliza sempre que
o host resolve para um tenant, com o search_path ja fixado no schema dele.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.ops.extensoes import rotas as rotas_dos_modulos
from core.arquivos import servir_midia

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sondas de saude, sem autenticacao de proposito: o orquestrador e o
    # balanceador precisam alcanca-las antes de qualquer sessao existir.
    path("", include("apps.ops.urls", namespace="ops")),
    path("", include("apps.accounts.urls_tenant", namespace="accounts")),
    path("documentos/", include("apps.knowledge.urls", namespace="knowledge")),
    path("", include("apps.content.urls", namespace="content")),
    path("site/", include("apps.integrations.urls", namespace="integrations")),
    path("editorial/", include("apps.editorial.urls", namespace="editorial")),
    path("radar/", include("apps.radar.urls", namespace="radar")),
    path("dados/", include("apps.dados.urls", namespace="dados")),
    path("operacao/", include("apps.ops.urls_painel", namespace="operacao")),
]

# Modulos opcionais trazem as proprias rotas (ver apps/ops/extensoes.py).
urlpatterns += [
    path(prefixo, include(urlconf, namespace=namespace))
    for prefixo, urlconf, namespace in rotas_dos_modulos()
]

# Os arquivos do tenant (foto de autor, capa em revisao), com login. Em
# desenvolvimento e em producao pela mesma rota: com USAR_X_ACCEL quem envia
# os bytes e o Nginx (/protected-media/, internal).
urlpatterns += [
    path(
        f"{settings.MEDIA_URL.strip('/')}/<str:schema>/<path:caminho>", servir_midia, name="midia"
    ),
]
