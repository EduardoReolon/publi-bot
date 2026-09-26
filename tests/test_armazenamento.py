"""Arquivos por tenant e a entrega pelo Nginx.

* cada tenant grava em MEDIA_ROOT/<schema>/..., e dois tenants nao dividem
  pasta;
* o X-Accel-Redirect aponta para o caminho a partir de MEDIA_ROOT, COM o
  schema. O `name` do arquivo e relativo a pasta do tenant, e manda-lo ao
  Nginx pedia um arquivo que nao existe;
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.knowledge.models import Document, DocumentCategory
from core.arquivos import entregar_arquivo


def _documento(sufixo: str) -> Document:
    return Document.objects.create(
        category=DocumentCategory.objects.get_or_create(name="Artigo", slug="artigo")[0],
        original_file=ContentFile(b"%PDF", name="estudo.pdf"),
        file_sha256=sufixo * 64,
        file_size_bytes=4,
    )


@pytest.mark.django_db
def test_cada_tenant_grava_na_pasta_do_proprio_schema(tenant_factory, settings):
    settings.USAR_X_ACCEL = True
    raiz = Path(settings.MEDIA_ROOT).resolve()
    caminhos = {}
    for nome, sufixo in (("midiaum", "a"), ("midiadois", "b")):
        tenant = tenant_factory(nome)
        with schema_context(tenant.schema_name):
            documento = _documento(sufixo)
            caminho = Path(documento.original_file.path).resolve()
            assert caminho.is_relative_to(raiz / tenant.schema_name)
            resposta = entregar_arquivo(documento.original_file, tipo="application/pdf")
            relativo = caminho.relative_to(raiz).as_posix()
            assert resposta["X-Accel-Redirect"] == f"/protected-media/{relativo}"
            assert relativo.startswith(f"{tenant.schema_name}/documents/")
            caminhos[nome] = caminho
    assert caminhos["midiaum"].parent != caminhos["midiadois"].parent
