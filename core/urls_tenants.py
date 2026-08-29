"""Rotas de um tenant — `<slug>.<ROOT_DOMAIN>`.

Este e o urlconf padrao (ROOT_URLCONF). O django-tenants o utiliza sempre que
o host resolve para um tenant, com o search_path ja fixado no schema dele.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sondas de saude, sem autenticacao de proposito: o orquestrador e o
    # balanceador precisam alcanca-las antes de qualquer sessao existir.
    path("", include("apps.ops.urls", namespace="ops")),
    path("", include("apps.accounts.urls_tenant", namespace="accounts")),
    path("documentos/", include("apps.knowledge.urls", namespace="knowledge")),
    path("", include("apps.content.urls", namespace="content")),
    path("site/", include("apps.integrations.urls", namespace="integrations")),
    path("operacao/", include("apps.ops.urls_painel", namespace="operacao")),
]

# Em desenvolvimento o proprio runserver entrega os arquivos enviados — foto de
# autor, PDF do acervo. Em producao quem serve e o Nginx (ver deploy/), e por
# isso a lista so cresce com DEBUG ligado.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
