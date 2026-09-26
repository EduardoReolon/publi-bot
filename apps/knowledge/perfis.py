"""Perfis de fonte prontos, e as categorias que um acervo novo ganha.

Cada natureza de fonte traz um jeito tipico de ser usada. Sao valores de
partida: a categoria criada a partir daqui e editavel como qualquer outra.
"""

from __future__ import annotations

from django.utils.text import slugify

# natureza -> (sustenta a ideia central, como citar, confidencial, validade em dias)
PERFIS: dict[str, dict] = {
    "cientifico": {
        "supports_central_idea": True,
        "citation_mode": "link",
        "confidential": False,
        "validity_days": None,
    },
    "normativo": {
        # Norma e tabela oficial valem ate a proxima revisao. Um ano e o ponto
        # de partida; tabela mensal (SINAPI, CUB) pede categoria propria com 31.
        "supports_central_idea": True,
        "citation_mode": "link",
        "confidential": False,
        "validity_days": 365,
    },
    "empresa": {
        "supports_central_idea": True,
        "citation_mode": "atribuicao",
        "confidential": False,
        "validity_days": None,
    },
    "especialista": {
        "supports_central_idea": True,
        "citation_mode": "atribuicao",
        "confidential": False,
        "validity_days": None,
    },
    "veiculo": {
        "supports_central_idea": True,
        "citation_mode": "link",
        "confidential": False,
        "validity_days": 730,
    },
    "comunidade": {
        # Forum e comentario servem para entender a duvida, nunca para afirmar.
        "supports_central_idea": False,
        "citation_mode": "interna",
        "confidential": False,
        "validity_days": 365,
    },
    "video": {
        "supports_central_idea": True,
        "citation_mode": "link",
        "confidential": False,
        "validity_days": 730,
    },
}

CATEGORIAS_PADRAO: list[tuple[str, str]] = [
    ("Artigo cientifico", "cientifico"),
    ("Norma ou documento oficial", "normativo"),
    ("Material da empresa", "empresa"),
    ("Nota do especialista", "especialista"),
    ("Veiculo de referencia", "veiculo"),
    ("Comunidade", "comunidade"),
    ("Video", "video"),
]


def criar_categorias_padrao() -> int:
    """Cria as categorias padrao que ainda nao existem. Devolve quantas criou."""
    from apps.knowledge.models import DocumentCategory

    criadas = 0
    for nome, natureza in CATEGORIAS_PADRAO:
        slug = slugify(nome)
        if DocumentCategory.objects.filter(slug=slug).exists():
            continue
        DocumentCategory.objects.create(
            name=nome, slug=slug, source_class=natureza, **PERFIS[natureza]
        )
        criadas += 1
    return criadas


def categoria_da_natureza(natureza: str):
    """A primeira categoria desta natureza, criada com o perfil padrao se faltar."""
    from apps.knowledge.models import DocumentCategory

    existente = (
        DocumentCategory.objects.filter(source_class=natureza).order_by("created_at").first()
    )
    if existente is not None:
        return existente
    nome = {n: rotulo for rotulo, n in CATEGORIAS_PADRAO}.get(natureza, natureza.title())
    slug = slugify(nome)
    base, n = slug, 2
    while DocumentCategory.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return DocumentCategory.objects.create(
        name=nome, slug=slug, source_class=natureza, **PERFIS[natureza]
    )
