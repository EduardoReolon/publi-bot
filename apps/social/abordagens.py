"""As abordagens de partida. Genericas de proposito (servem a uma clinica, a
uma construtora, a uma consultoria): o negocio de cada cliente entra pelo
publico da conta, pelas dores e pelas instrucoes, nao aqui.

Sao sementes: criadas uma vez, nunca sobrescritas (a pessoa edita, desliga e
cria outras pela tela). O placar de cada uma diz qual funciona em cada conta.
"""

from __future__ import annotations

NAO_INVENTE = " Nao invente caso, paciente, cliente, historia ou numero: use so o MATERIAL."

SEMENTES = [
    {
        "nome": "E o meu caso",
        "instrucao": (
            "Identificacao. Abra descrevendo, no presente e nas palavras de quem vive, uma "
            "situacao concreta do PUBLICO — tirada das DORES e das frases de identificacao "
            "do material — para a pessoa pensar 'e exatamente o que acontece comigo'. Nada "
            "de abstracao ('muitas pessoas sofrem com...'): uma cena. Depois, o que o artigo "
            "explica sobre isso e por que vale ler." + NAO_INVENTE
        ),
        "redes": [],
    },
    {
        "nome": "Nossa, que incrivel",
        "instrucao": (
            "Surpresa. Abra com o achado mais surpreendente do material, com o numero exato, "
            "de um jeito que contrarie o que a maioria acha. Depois diga em uma ou duas "
            "frases por que isso importa para o publico." + NAO_INVENTE
        ),
        "redes": [],
    },
    {
        "nome": "Mito ou verdade",
        "instrucao": (
            "Apresente uma crenca comum sobre o tema (que o artigo desfaz ou matiza) e mostre "
            "o que o artigo mostra de fato. Sem ridicularizar quem acredita." + NAO_INVENTE
        ),
        "redes": [],
    },
    {
        "nome": "O erro comum",
        "instrucao": (
            "Mostre um erro que o publico costuma cometer, segundo o artigo, e a "
            "consequencia — sem culpar a pessoa. Termine com o que fazer em vez disso."
            + NAO_INVENTE
        ),
        "redes": [],
    },
    {
        "nome": "Lista pratica",
        "instrucao": (
            "Transforme o artigo em sinais, passos ou cuidados numerados, curtos, que a "
            "pessoa pode usar hoje. Um item por linha (ou por lamina)." + NAO_INVENTE
        ),
        "redes": ["instagram", "linkedin"],
    },
    {
        "nome": "Pergunta que incomoda",
        "instrucao": (
            "Abra com a pergunta que o publico se faz e nem sempre tem coragem de fazer em "
            "voz alta, sobre o tema do artigo. Responda com o que o artigo diz, sem rodeio."
            + NAO_INVENTE
        ),
        "redes": [],
    },
    {
        "nome": "Olhar de quem pratica",
        "instrucao": (
            "Em primeira pessoa, como o profissional ou a empresa ve este tema no dia a dia: "
            "uma opiniao clara, sustentada por um dado do material, e uma pergunta para "
            "quem tambem trabalha com isso." + NAO_INVENTE
        ),
        "redes": ["linkedin"],
    },
]


def garantir_abordagens() -> int:
    """Cria as sementes que faltam (por nome). Devolve quantas criou."""
    from apps.social.models import Abordagem

    criadas = 0
    for semente in SEMENTES:
        _obj, nova = Abordagem.objects.get_or_create(
            nome=semente["nome"],
            defaults={
                "instrucao": semente["instrucao"],
                "redes": semente["redes"],
                "semente": True,
            },
        )
        criadas += nova
    return criadas
