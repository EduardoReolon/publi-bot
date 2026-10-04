"""As duas regras que mantem o modulo de redes separado do nucleo.

1. Nenhum arquivo do nucleo importa `apps.social` (o nucleo nao sabe que o
   modulo existe; ele entra por apps/ops/extensoes.py).
2. Dentro do modulo, so `fontes.py` importa o nucleo (content, knowledge,
   radar, editorial, integrations, dados). Infraestrutura (accounts, ops,
   inference) pode.

Quebrar uma regra aqui e o primeiro passo para o modulo nao poder mais ser
tirado, trocado ou separado num servico proprio.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
NUCLEO = {"content", "knowledge", "radar", "editorial", "integrations", "dados"}


def _importados(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes |= {a.name for a in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module)
    return nomes


def test_o_nucleo_nao_importa_o_modulo_de_redes():
    culpados = []
    for pasta in [RAIZ / "apps", RAIZ / "core"]:
        for arquivo in pasta.rglob("*.py"):
            if "social" in arquivo.relative_to(RAIZ).parts:
                continue
            if any(
                n == "apps.social" or n.startswith("apps.social.") for n in _importados(arquivo)
            ):
                culpados.append(str(arquivo.relative_to(RAIZ)))
    assert culpados == []


def test_so_fontes_py_importa_o_nucleo():
    pasta = RAIZ / "apps" / "social"
    culpados = []
    for arquivo in pasta.rglob("*.py"):
        partes = arquivo.relative_to(pasta).parts
        if partes[0] in {"tests", "migrations"} or arquivo.name == "fontes.py":
            continue
        for nome in _importados(arquivo):
            if nome.startswith("apps.") and nome.split(".")[1] in NUCLEO:
                culpados.append(f"{arquivo.relative_to(RAIZ)}: {nome}")
    assert culpados == []


def test_o_modulo_se_declara_ao_nucleo():
    from apps.ops import extensoes

    assert ("redes/", "apps.social.urls", "social") in extensoes.rotas()
    assert any(m["url"] == "social:inicio" for m in extensoes.menus())
    assert {"social_linkedin", "social_instagram", "social_gmn"} <= set(
        extensoes.prompts_iniciais()
    )
    assert "social_instagram" in extensoes.prompts_com_guia()
    assert "social/_no_artigo.html" in extensoes.blocos_do_artigo()
