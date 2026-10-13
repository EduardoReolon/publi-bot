"""O destaque no meio do artigo (a frase em letra maior): `>> frase` no
markdown vira o `aside` do contrato, no maximo dois; e os prompts que
escrevem o artigo pedem atencao, destaque e Cialdini."""

from __future__ import annotations

from apps.content import destaque
from apps.content.prompts_iniciais import PROMPTS_INICIAIS, REGRAS_DE_ATENCAO
from apps.content.rendering import markdown_para_html


def test_destaque_vira_aside_e_no_maximo_dois():
    html = markdown_para_html(
        "Abre.\n\n>> A frase que importa [[FONTE_1]]\n\nMeio.\n\n>> Segunda\n\n>> Terceira sai"
    )
    assert (
        '<aside data-publibot="destaque"><p><strong>A frase que importa</strong></p></aside>'
        in html
    )
    assert html.count('data-publibot="destaque"') == 2
    assert "Terceira" not in html and "[[" not in html and "&gt;&gt;" not in html
    assert destaque.contar("a\n>> b\n>> c") == 2


def test_prompts_do_artigo_pedem_atencao_destaque_e_cialdini():
    assert "DESTAQUE" in REGRAS_DE_ATENCAO and "Cialdini" in REGRAS_DE_ATENCAO
    assert REGRAS_DE_ATENCAO in PROMPTS_INICIAIS["section_draft"]["sistema"]
    assert REGRAS_DE_ATENCAO in PROMPTS_INICIAIS["seo_draft"]["sistema"]
    assert "2 segundos" in PROMPTS_INICIAIS["article_framing"]["sistema"]
