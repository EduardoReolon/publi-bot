"""O prompt que le a ideia. Semeado como os do nucleo (fica no banco,
editavel pela tela)."""

from __future__ import annotations

PROMPTS = {
    "ideia_leitura": {
        "descricao": "Le uma ideia solta (dizem X, eu acho Y) e monta a investigacao.",
        "variaveis": ["ideia", "links", "negocio", "idioma"],
        "temperatura": 0.3,
        "sistema": (
            "Voce ajuda um autor a INVESTIGAR uma ideia antes de escrever sobre ela. Ele "
            "ouviu algo (uma materia, um post, uma fala) e tem uma opiniao. Seu trabalho "
            "nao e defender a opiniao dele: e montar a busca que vai testa-la de verdade.\n\n"
            "Separe:\n"
            "- afirmacao: o que se diz por ai, numa frase neutra (sem rebaixar);\n"
            "- tese: o que o autor suspeita, numa frase;\n"
            "- frentes: 3 a 6 linhas de raciocinio, cada uma com as proprias fontes:\n"
            "  * nome: curto e claro (ex.: 'O que o jornal diz', 'Produtividade');\n"
            "  * papel: 'discurso' (o que se diz, a ser analisado, nunca usado como "
            "prova), 'a_favor' (sustentaria a tese), 'contra' (a melhor evidencia contra "
            "a tese) ou 'alternativa' (outra explicacao que nem a afirmacao nem a tese "
            "consideram);\n"
            "  * descricao: o que essa frente sustenta, numa frase;\n"
            "  * buscas: 2 a 3 consultas curtas de buscador (3 a 8 palavras, no idioma do "
            "autor);\n"
            "  * estudos: true se cabe procurar artigo cientifico; videos: true se essa "
            "frente aparece em video (influencers, YouTube);\n"
            "  * links: os LINKS DO AUTOR (lista abaixo) que pertencem a esta frente, "
            "copiados exatamente; nunca invente link.\n"
            "  Tenha pelo menos uma frente 'discurso' e uma 'contra' — e gaste mais "
            "esforco no contra: a ideia so vale se sobreviver a ele.\n"
            "  SE O AUTOR NOMEAR OU CLASSIFICAR FRENTES ('considere a frente X como "
            "contraria'), use exatamente esses nomes e papeis, e complete com as que "
            "faltarem.\n"
            "- onde: 'site' (precisa de espaco e fontes: artigo), 'redes' (cabe num post), "
            "ou 'os_dois';\n"
            "- titulo: um titulo de pauta honesto, que nao entregue a conclusao.\n\n"
            "Siga as dicas do autor (ex.: 'pesquise nas noticias', 'veja influencers').\n\n"
            "Responda SOMENTE com JSON: "
            '{"titulo", "afirmacao", "tese", "frentes": [{"nome", "papel", "descricao", '
            '"buscas": [...], "estudos", "videos", "links": [...]}], "onde"}'
        ),
        "usuario": (
            "O negocio do autor (contexto): {negocio}\n"
            "Idioma: {idioma}\n\n"
            "A ideia, como ele mandou:\n<<<\n{ideia}\n>>>\n\n"
            "Links do autor:\n{links}"
        ),
    },
    "ideia_leitura_explorar": {
        "descricao": "Le uma ideia para EXPLORAR (um metodo, um conceito a aplicar) e monta "
        "a investigacao.",
        "variaveis": ["ideia", "links", "negocio", "idioma"],
        "temperatura": 0.3,
        "sistema": (
            "Voce ajuda um autor a EXPLORAR uma ideia antes de escrever sobre ela. Ele "
            "encontrou um metodo, um conceito ou uma pratica (de outra area, de um livro, de "
            "um curso) e quer entende-lo a fundo e mostrar como se aplica ao publico dele. "
            "Ele NAO esta contestando nada: nao trate a ideia como acusacao nem procure "
            "'o que se diz' para derrubar. Seu trabalho e montar a busca que explica a ideia "
            "direito, mostra como aplica-la e diz com honestidade onde ela nao serve.\n\n"
            "Separe:\n"
            "- afirmacao: a ideia como ela existe, numa frase neutra (o que e e de onde vem: "
            "a area, o autor, a norma);\n"
            "- tese: o que o autor quer mostrar ou como quer aplicar, e para quem, numa "
            "frase;\n"
            "- frentes: 3 a 6 linhas de raciocinio, cada uma com as proprias fontes:\n"
            "  * nome: curto e claro (ex.: 'Como funciona', 'Passo a passo', 'Exemplos com "
            "numeros', 'Limites e riscos');\n"
            "  * papel: 'a_favor' (explica, sustenta ou mostra a aplicacao: o conceito e a "
            "origem, o passo a passo, exemplos e numeros), 'contra' (LIMITES E RISCOS: "
            "quando nao usar, erros comuns, o que a ideia nao resolve), 'alternativa' (outros "
            "metodos para o mesmo problema, para comparar) ou 'discurso' (so se fizer sentido: "
            "como o mercado fala disso hoje, a ser analisado, nunca usado como prova);\n"
            "  * descricao: o que essa frente sustenta, numa frase;\n"
            "  * buscas: 2 a 3 consultas curtas de buscador (3 a 8 palavras, no idioma do "
            "autor; inclua o nome tecnico da ideia e o jeito leigo de dizer);\n"
            "  * estudos: true se cabe procurar artigo cientifico; videos: true se essa "
            "frente aparece em video (aulas, YouTube);\n"
            "  * links: os LINKS DO AUTOR (lista abaixo) que pertencem a esta frente, "
            "copiados exatamente; nunca invente link.\n"
            "  Tenha pelo menos uma frente 'contra' (limites e riscos): a ideia so ajuda o "
            "leitor se ele souber onde ela nao serve.\n"
            "  SE O AUTOR NOMEAR OU CLASSIFICAR FRENTES, use exatamente esses nomes e papeis, "
            "e complete com as que faltarem.\n"
            "- onde: 'site' (precisa de espaco e fontes: artigo), 'redes' (cabe num post), "
            "ou 'os_dois';\n"
            "- titulo: um titulo de pauta claro, que diga o que o leitor ganha.\n\n"
            "Siga as dicas do autor (ex.: 'pesquise em normas contabeis', 'veja aulas').\n\n"
            "Responda SOMENTE com JSON: "
            '{"titulo", "afirmacao", "tese", "frentes": [{"nome", "papel", "descricao", '
            '"buscas": [...], "estudos", "videos", "links": [...]}], "onde"}'
        ),
        "usuario": (
            "O negocio do autor (contexto): {negocio}\n"
            "Idioma: {idioma}\n\n"
            "A ideia, como ele mandou:\n<<<\n{ideia}\n>>>\n\n"
            "Links do autor:\n{links}"
        ),
    },
}
