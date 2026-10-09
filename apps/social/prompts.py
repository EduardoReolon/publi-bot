"""Os prompts das redes, um por rede (`social_<rede>`). Semeados como os do
nucleo (ficam no banco, editaveis pela tela; `semear_prompts --atualizar --so
social_instagram` publica a semente nova). O estilo de cada rede vem do modulo
dela (`apps/social/redes/<rede>.py`)."""

from __future__ import annotations

from apps.social.redes import REDES

_CAMPOS = {
    "linkedin": (
        '  "gancho": a primeira linha (ate {dobra} caracteres), a que aparece antes do '
        '"ver mais";\n'
        '  "texto": o resto do post, sem repetir o gancho;\n'
        '  "hashtags": ate {hashtags} hashtags, sem o #;\n'
        '  "primeiro_comentario": uma frase que chama para ler o artigo (o PubliBot poe o '
        "link depois dela)."
    ),
    "instagram": (
        '  "gancho": a primeira linha da legenda (ate {dobra} caracteres);\n'
        '  "texto": o resto da legenda, curto, terminando com a chamada para o link na bio;\n'
        '  "hashtags": ate {hashtags} hashtags, sem o #;\n'
        '  "laminas": de {min_laminas} a {max_laminas} itens {{"titulo": ate 8 palavras, '
        '"texto": ate 20 palavras}}. A primeira e o gancho (texto pode ser vazio): '
        "se houver achado com numero, abra com o mais surpreendente, copiado exato. A "
        "segunda tambem prende sozinha. Uma ideia por lamina; a ultima convida a mandar "
        "o post para quem precisa ver isso e lembra o link na bio."
    ),
    "gmn": (
        '  "gancho": a primeira frase (ate {dobra} caracteres);\n'
        '  "texto": o resto do post, terminando com o convite para o botao "Saiba mais".'
    ),
}

_SISTEMA = (
    "Voce escreve posts para redes sociais a partir de um artigo ja publicado, escrito "
    "com fontes. O post leva a pessoa ao artigo; ele nao substitui o artigo.\n\n"
    "REDE: {nome}. {estilo}\n\n"
    "Regras:\n"
    "- Use SO o que esta no MATERIAL do artigo: nenhum numero, estudo, caso ou promessa "
    "que nao esteja la. Numero, so copiado exatamente como esta.\n"
    "- Nada de link ou endereco no texto: o PubliBot poe o link no lugar certo.\n"
    "- Nao comece com 'Voce sabia' nem repetindo o titulo do artigo.\n"
    "- Escreva para o PUBLICO desta conta, no TOM pedido, seguindo a ABORDAGEM.\n"
    "- Respeite as REGRAS QUE NUNCA SE QUEBRAM e as instrucoes do negocio.\n\n"
    "Responda SOMENTE com um objeto JSON:\n{campos}"
)

_USUARIO = (
    "Titulo do artigo: {titulo}\n"
    "Resumo: {resumo}\n"
    "Secoes: {secoes}\n\n"
    "MATERIAL — frases do artigo proximas das dores do publico (identificacao):\n"
    "{identificacao}\n\n"
    "MATERIAL — achados com numero:\n{achados}\n\n"
    "Dores do publico que o artigo toca:\n{dores}\n\n"
    "PUBLICO desta conta: {publico}\n"
    "TOM: {tom}\n"
    "ABORDAGEM: {abordagem}\n"
    "Instrucoes do negocio: {instrucoes}\n"
    "REGRAS QUE NUNCA SE QUEBRAM: {regras}\n"
    "Limites: {limites}\n"
    "Idioma: {idioma}\n"
    "{ajuste}\n"
    "Produza o JSON."
)

VARIAVEIS = [
    "titulo",
    "resumo",
    "secoes",
    "identificacao",
    "achados",
    "dores",
    "publico",
    "tom",
    "abordagem",
    "instrucoes",
    "regras",
    "limites",
    "idioma",
    "ajuste",
]


def _semente(rede) -> dict:
    f = rede.formato
    campos = _CAMPOS[rede.codigo].format(
        dobra=f.dobra,
        hashtags=f.max_hashtags,
        min_laminas=f.min_laminas,
        max_laminas=f.max_laminas,
    )
    return {
        "descricao": f"Post para {rede.nome}, a partir de um artigo publicado.",
        "variaveis": VARIAVEIS,
        "temperatura": 0.7,
        "sistema": _SISTEMA.format(nome=rede.nome, estilo=rede.estilo, campos=campos),
        "usuario": _USUARIO,
    }


PROMPTS = {rede.prompt: _semente(rede) for rede in REDES.values()}

# Descrever uma foto do banco (modelo que enxerga imagem; opcional, ver
# ConfiguracaoSocial.descrever_fotos). A descricao entra no material do post.
PROMPTS["social_foto"] = {
    "descricao": "Descreve uma foto do trabalho do negocio, para a legenda do post.",
    "variaveis": ["tema", "oferta"],
    "temperatura": 0.2,
    "sistema": (
        "Voce descreve uma foto do trabalho de um negocio para quem vai escrever a legenda "
        "de um post. Descreva so o que se ve sobre o TRABALHO, o servico ou o produto "
        "(ex.: 'corte degrade com risca lateral', 'prato de massa com molho vermelho'). "
        "Nunca descreva a pessoa: nada de idade, etnia, corpo, aparencia ou nome. Nao "
        "invente o que nao da para ver. Uma ou duas frases, em portugues.\n\n"
        'Responda SOMENTE com JSON: {"descricao": "..."}'
    ),
    "usuario": "O negocio: {tema}. O que oferece: {oferta}. Descreva a foto.",
}
