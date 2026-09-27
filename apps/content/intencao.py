"""Analise de intencao de busca de uma pauta, com um modelo grande de fora.

Antes de gerar, vale saber o que quem busca espera encontrar: aprender o que e
(informacional), comparar opcoes (investigacao comercial), contratar
(transacional). Errar a intencao e o motivo mais comum de artigo bom que nao
posiciona. O pedido leva a palavra-chave, os sinais que o radar achou (com
volume) e o negocio; a resposta colada vira titulo, tipo de conteudo e
orientacao da pauta — o que o planejamento do artigo ja usa.
"""

from __future__ import annotations

from core.resposta_ia import itens, ler_blocos

ROTULOS = ["TITULO", "INTENCAO", "TIPO", "ORIENTACAO"]


def pedido(pauta) -> str:
    from apps.editorial.models import perfil_do_negocio
    from apps.editorial.presets import TIPOS_DE_CONTEUDO

    perfil = perfil_do_negocio()
    sinais = [
        f"- {s.get('texto')}"
        + (f" ({s['volume']} buscas/mes)" if s.get("volume") is not None else "")
        for s in (pauta.evidence or {}).get("sinais", [])[:15]
    ]
    tipos = ", ".join(chave for chave, _rotulo in TIPOS_DE_CONTEUDO)
    return f"""\
Voce e um analista de intencao de busca, especializado em SEO e estrategia de
conteudo. Analise a pauta abaixo antes de o artigo ser escrito.

Pauta: {pauta.title}
Palavra-chave alvo: {pauta.target_keyword or pauta.title}
O que as pessoas buscam em torno dela (do Google):
{chr(10).join(sinais) or "- (sem sinais registrados)"}

O site:
Tema: {perfil.tema if perfil else ""}
Publico: {perfil.publico if perfil else ""}
Oferta (para onde o artigo pode levar, sem forcar): {perfil.oferta if perfil else ""}

Regras:
- A intencao manda no formato: quem quer aprender o que e nao quer comparativo,
  quem compara nao quer definicao de dicionario.
- TITULO com 55 a 60 caracteres, com a palavra-chave e uma palavra-gatilho, sem
  exagero nem promessa que o texto nao cumpre.
- Se a intencao estiver longe da oferta, diga na orientacao como ligar os dois
  sem virar anuncio (ou que nao vale ligar).
- Nao invente numeros de busca.

Responda EXATAMENTE neste formato, sem nada antes, e termine com a linha FIM:

TITULO: o titulo do artigo
INTENCAO: informacional, navegacional, transacional ou investigacao comercial
TIPO: um destes: {tipos}
ORIENTACAO:
- o objetivo principal de quem busca
- o que essa pessoa espera encontrar no texto
- o que evitar
- elementos que precisam estar no texto
- como o texto leva a oferta, ou por que nao deve levar
FIM
"""


def ler(resposta: str) -> dict:
    from apps.editorial.presets import TIPOS_DE_CONTEUDO

    blocos = ler_blocos(resposta, ROTULOS)
    saida: dict = {}
    titulo = " ".join(blocos.get("TITULO", "").split()).strip('"')
    if titulo:
        saida["title"] = titulo[:300]
    tipo = blocos.get("TIPO", "").strip().lower()
    for chave, rotulo in TIPOS_DE_CONTEUDO:
        if tipo.startswith(chave) or tipo.startswith(rotulo.lower().split(" (")[0]):
            saida["content_type"] = chave
            break
    orientacao = itens(blocos.get("ORIENTACAO", ""))
    intencao = " ".join(blocos.get("INTENCAO", "").split())
    linhas = [f"Intencao de busca: {intencao}."] if intencao else []
    linhas += [f"- {item}" for item in orientacao]
    if linhas:
        saida["briefing"] = "\n".join(linhas)
    return saida


def aplicar(pauta, resposta: str) -> list[str]:
    """Grava na pauta o que a resposta trouxe. Devolve os campos mudados."""
    lido = ler(resposta)
    for campo, valor in lido.items():
        setattr(pauta, campo, valor)
    if lido:
        pauta.save(update_fields=list(lido))
    return list(lido)
