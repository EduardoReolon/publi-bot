"""Possiveis parceiros: sites que aparecem nas mesmas buscas sem disputar.

Parceria aqui nao e troca de links. Troca de link em quantidade e um dos
padroes que o Google trata como spam de links; o que funciona, e o que a
proposta pede, e conteudo que serve aos dois publicos: artigo convidado,
conteudo em conjunto, um estudo com dados dos dois. Link pago ou trocado leva
rel="sponsored" (ou "nofollow").

O PubliBot nao mede a "forca" de um dominio (DA/DR): isso e metrica de
ferramenta paga, e a DataForSEO cobra a parte por ela. O pedido diz o que
se sabe — as buscas em comum, o que cada um publicou — e proibe inventar numero.
"""

from __future__ import annotations


def _nosso_site() -> dict:
    from apps.content.models import Article
    from apps.editorial.models import perfil_do_negocio
    from apps.integrations.models import Site

    site = Site.objects.first()
    negocio = perfil_do_negocio()
    publicados = Article.objects.filter(status=Article.Status.PUBLISHED)
    return {
        "endereco": getattr(site, "base_url", "") or "",
        "tema": getattr(negocio, "tema", "") or "",
        "publico": getattr(negocio, "publico", "") or "",
        "oferta": getattr(negocio, "oferta", "") or "",
        "publicados": publicados.count(),
        "titulos": list(publicados.order_by("-published_at").values_list("title", flat=True)[:10]),
    }


def proposta(parceiro, nosso: dict | None = None) -> str:
    nosso = nosso or _nosso_site()
    buscas = sorted(parceiro.consultas.items(), key=lambda par: par[1])[:15]
    exemplos = [
        f"- {e.get('titulo') or e.get('url')} ({e.get('url')})" for e in parceiro.exemplos[:5]
    ]
    titulos = [f"- {t}" for t in nosso["titulos"]]
    return f"""\
Voce e um especialista em parcerias de conteudo e SEO etico. Me ajude a propor
uma parceria ao site {parceiro.dominio}.

MEU SITE
- Endereco: {nosso["endereco"] or "(nao informado)"}
- Tema: {nosso["tema"] or "(nao informado)"}
- Publico: {nosso["publico"] or "(nao informado)"}
- O que vendo: {nosso["oferta"] or "(nao informado)"}
- Artigos publicados: {nosso["publicados"]}
{chr(10).join(titulos) or "- (nenhum ainda)"}

O SITE DELES ({parceiro.dominio})
Aparece na primeira pagina do Google nestas buscas (posicao entre parenteses):
{chr(10).join(f"- {consulta} ({posicao})" for consulta, posicao in buscas)}
Paginas deles que apareceram:
{chr(10).join(exemplos) or "- (nenhuma registrada)"}

REGRAS
- Nada de troca de links pura ("eu linko voce, voce me linka"): em quantidade,
  o Google trata como esquema de links e pode punir os dois. Proponha conteudo
  que sirva aos dois publicos: artigo convidado, conteudo feito em conjunto,
  estudo ou levantamento com dados dos dois, material de referencia citavel.
- Se houver pagamento ou troca combinada, o link leva rel="sponsored" (ou
  "nofollow"); diga isso na proposta.
- Nao invente numeros: nem trafego, nem autoridade de dominio (DA/DR), nem
  resultados. Se algo depender de numero que eu nao dei, diga o que eu devo
  medir antes.
- Se o site parecer concorrente direto, ou irrelevante para o meu publico,
  diga isso logo e pare.

RESPONDA
1. Complementam ou disputam? Em 2 ou 3 frases, pelo que as buscas mostram.
2. Tres formatos de parceria, cada um com uma ideia concreta de conteudo
   tirada das buscas em comum, e o que cada lado ganha (publico, citacao,
   dado, credibilidade).
3. O que eu devo conferir no site deles antes de escrever (qualidade,
   publico, se ja aceitam convidados, se vendem link — sinal ruim).
4. Um primeiro e-mail curto (ate 120 palavras), em portugues, sem bajulacao,
   que proponha a melhor das tres ideias.
"""


def parceiros_com_proposta() -> list:
    from apps.radar.models import ConcorrenteSugerido

    parceiros = list(
        ConcorrenteSugerido.objects.filter(situacao=ConcorrenteSugerido.Situacao.PARCEIRO)[:20]
    )
    if parceiros:
        nosso = _nosso_site()
        for parceiro in parceiros:
            parceiro.proposta = proposta(parceiro, nosso)
    return parceiros
