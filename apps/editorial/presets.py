"""Pontos de partida do perfil editorial, por tipo de negocio.

Um modo e so isso: um ponto de partida. Escolher "negocio local" preenche o
guia com o que costuma valer para uma clinica ou uma oficina; depois disso
cada campo e editavel, e nada aqui e reaplicado sem alguem pedir.

As estruturas por tipo de conteudo seguem os formatos consolidados de busca
organica — guia, passo a passo, comparativo, lista, custo, estudo de caso — e
um formato proprio para pagina de servico, que e o que um negocio local mais
publica. Cada item e o OBJETIVO de uma secao, e nao um titulo: o planejamento
escreve o titulo, e o objetivo impede duas secoes de responderem a mesma
pergunta.
"""

from __future__ import annotations

TIPOS_DE_CONTEUDO = [
    ("guia", "Guia (o que e)"),
    ("passo_a_passo", "Passo a passo"),
    ("comparativo", "Comparativo (X ou Y)"),
    ("lista", "Lista"),
    ("custo", "Custo e preco"),
    ("estudo_de_caso", "Estudo de caso"),
    ("servico", "Servico (educativo)"),
]

ESTRUTURAS_PADRAO: dict[str, list[str]] = {
    "guia": [
        "Definir o assunto em linguagem simples, respondendo a busca na primeira frase",
        "Por que isso importa para quem le",
        "Como funciona, em partes",
        "Erros e mitos comuns",
        "Quando procurar ajuda ou um profissional",
    ],
    "passo_a_passo": [
        "O que se consegue ao final, e o que e preciso antes de comecar",
        "Os passos, na ordem, um objetivo por passo",
        "Onde as pessoas costumam errar",
        "Como conferir se deu certo",
    ],
    "comparativo": [
        "Resposta curta: quando escolher cada opcao",
        "O que cada opcao e",
        "Diferencas que importam para a decisao",
        "Para quem cada uma serve melhor",
    ],
    "lista": [
        "Criterio usado para montar a lista",
        "Os itens, cada um com o porque",
        "Como escolher entre eles",
    ],
    "custo": [
        "Faixa de custo e do que ela depende, logo no inicio",
        "O que entra no preco",
        "O que encarece e o que barateia",
        "Como estimar o proprio caso",
    ],
    "estudo_de_caso": [
        "O ponto de partida e o problema",
        "O que foi feito, e por que",
        "O resultado, com o que se pode e o que nao se pode generalizar",
        "O que o leitor pode levar para o proprio caso",
    ],
    "servico": [
        "O que e, em linguagem simples",
        "Para que e indicado",
        "Como e uma sessao ou atendimento",
        "Para quem e, e para quem nao e",
        "Frequencia e o que esperar",
    ],
}

MODOS = [
    ("saude", "Saude e ciencia"),
    ("negocio_local", "Negocio local"),
    ("produto", "Produto ou SaaS"),
    ("institucional", "Institucional"),
]

# Tom de voz nas quatro dimensoes da Nielsen Norman Group, de 1 a 5:
#   humor:        1 = serio        5 = engracado
#   formalidade:  1 = formal       5 = casual
#   respeito:     1 = respeitoso   5 = irreverente
#   entusiasmo:   1 = objetivo     5 = entusiasmado
PRESETS: dict[str, dict] = {
    "saude": {
        "tom_humor": 1,
        "tom_formalidade": 3,
        "tom_respeito": 1,
        "tom_entusiasmo": 2,
        "regra_de_ouro": "Validar antes de educar, educar antes de convidar.",
        "somos": [
            {"somos": "proximos", "nao_somos": "intimos"},
            {"somos": "tecnicos", "nao_somos": "hermeticos"},
            {"somos": "cuidadosos com promessas", "nao_somos": "vagos"},
        ],
        "termos": [
            {"termo": "cura", "troca": "auxilia", "motivo": "promessa de resultado"},
            {"termo": "trata", "troca": "auxilia no manejo", "motivo": "promessa de resultado"},
            {"termo": "garante", "troca": "pode contribuir para", "motivo": "promessa"},
            {"termo": "milagroso", "troca": "", "motivo": "promessa"},
        ],
        "tipo_padrao": "guia",
    },
    "negocio_local": {
        "tom_humor": 2,
        "tom_formalidade": 4,
        "tom_respeito": 2,
        "tom_entusiasmo": 3,
        "regra_de_ouro": "Responder a duvida primeiro, convidar depois.",
        "somos": [
            {"somos": "acolhedores", "nao_somos": "insistentes"},
            {"somos": "claros", "nao_somos": "simplistas"},
        ],
        "termos": [
            {"termo": "garantido", "troca": "", "motivo": "promessa"},
            {"termo": "o melhor da cidade", "troca": "", "motivo": "superlativo sem prova"},
        ],
        "tipo_padrao": "servico",
    },
    "produto": {
        "tom_humor": 2,
        "tom_formalidade": 4,
        "tom_respeito": 2,
        "tom_entusiasmo": 3,
        "regra_de_ouro": "Resolver o problema do leitor antes de falar do produto.",
        "somos": [
            {"somos": "praticos", "nao_somos": "vendedores"},
            {"somos": "diretos", "nao_somos": "secos"},
        ],
        "termos": [
            {"termo": "revolucionario", "troca": "", "motivo": "superlativo sem prova"},
            {"termo": "100% garantido", "troca": "", "motivo": "promessa"},
        ],
        "tipo_padrao": "passo_a_passo",
    },
    "institucional": {
        "tom_humor": 1,
        "tom_formalidade": 2,
        "tom_respeito": 1,
        "tom_entusiasmo": 2,
        "regra_de_ouro": "Informar com precisao, sem jargao desnecessario.",
        "somos": [
            {"somos": "precisos", "nao_somos": "burocraticos"},
        ],
        "termos": [],
        "tipo_padrao": "guia",
    },
}

# Marcas de texto de maquina em portugues. Nao sao proibidas — sao AVISOS para
# quem revisa, porque uma ou outra aparece em texto humano. Em quantidade, sao
# o que faz o leitor desconfiar do texto inteiro.
#
# Cada item: (expressao, sugestao). A comparacao ignora caixa e acento.
MARCAS_DE_MAQUINA: list[tuple[str, str]] = [
    ("vale ressaltar", "corte: diga a coisa"),
    ("vale destacar", "corte: diga a coisa"),
    ("e importante destacar", "corte: diga a coisa"),
    ("e importante ressaltar", "corte: diga a coisa"),
    ("e fundamental", "diga por que importa"),
    ("no cenario atual", "corte ou seja especifico"),
    ("nos dias de hoje", "corte ou seja especifico"),
    ("em um mundo cada vez mais", "corte"),
    ("desempenha um papel crucial", "diga o que faz"),
    ("desempenha um papel fundamental", "diga o que faz"),
    ("mergulhar", "use um verbo concreto"),
    ("jornada", "use a palavra concreta"),
    ("potencializar", "aumentar, melhorar"),
    ("alavancar", "usar, aumentar"),
    ("proporcionar", "dar, trazer, permitir"),
    ("em suma", "corte"),
    ("em resumo", "corte, ou resuma de fato"),
    ("sendo assim", "entao"),
    ("nesse sentido", "corte"),
    ("robusto", "diga em que e forte"),
    ("abrangente", "diga o que cobre"),
    ("otimizar", "melhorar, acelerar, reduzir"),
    ("nao e apenas", "diga o que e"),
]
