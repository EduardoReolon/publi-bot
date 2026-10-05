"""Os numeros da estrategia, num lugar so, todos ajustaveis pela tela.

Os padroes sao pontos de partida razoaveis (boas praticas correntes de
mercado, arredondadas), nao verdades: cada negocio tem um publico, e o numero
certo para uma clinica de bairro nao e o de uma consultoria de nicho. Por
isso cada um diz para que serve, e a pagina de estrategia mostra o que mudou
quando a pessoa ajusta.

Gravados em `ConfiguracaoSocial.parametros` (so o que a pessoa mudou).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parametro:
    nome: str
    rotulo: str
    padrao: float
    ajuda: str
    grupo: str
    inteiro: bool = False


PARAMETROS = [
    # -- Fases (por seguidores) ---------------------------------------------------------
    Parametro(
        "seguidores_tracao",
        "Seguidores para sair do Comeco",
        300,
        "Abaixo disto, quase todo alcance vem de fora dos seguidores: o objetivo e "
        "descobrir o que o publico quer, nao crescer.",
        "Fases",
        inteiro=True,
    ),
    Parametro(
        "seguidores_crescimento",
        "Seguidores para a fase de Crescimento",
        1000,
        "A partir daqui, ja ha base para medir com o organico e o impulso pago passa a "
        "amplificar o que funciona.",
        "Fases",
        inteiro=True,
    ),
    Parametro(
        "seguidores_escala",
        "Seguidores para a fase de Escala",
        10000,
        "Conta estabelecida: o foco vai para converter (do post ao artigo, do artigo a oferta).",
        "Fases",
        inteiro=True,
    ),
    # -- Medicao --------------------------------------------------------------------------
    Parametro(
        "alcance_minimo",
        "Alcance minimo para julgar um post",
        100,
        "Post que menos pessoas viram fica como 'inconclusivo', nem bom nem ruim: com "
        "poucas pessoas, a taxa e sorte. Publico pequeno e de nicho: baixe com cuidado.",
        "Medicao",
        inteiro=True,
    ),
    Parametro(
        "dias_para_medir",
        "Dias no ar antes de julgar",
        7,
        "Instagram e LinkedIn entregam quase tudo nos primeiros 2-3 dias; 7 e folga.",
        "Medicao",
        inteiro=True,
    ),
    Parametro(
        "taxa_instagram",
        "Taxa de referencia no Instagram (%)",
        2.0,
        "Interacoes que importam (salvos, compartilhamentos, comentarios, cliques) sobre o "
        "alcance. So referencia na tela: o 'funcionou' e sempre contra a mediana da propria "
        "conta, entao publico pequeno ou de nicho nao e punido.",
        "Medicao",
    ),
    Parametro(
        "taxa_linkedin",
        "Taxa de referencia no LinkedIn (%)",
        3.0,
        "Interacoes sobre impressoes. Mesma ressalva: e referencia, nao meta.",
        "Medicao",
    ),
    # -- Aprendizado -------------------------------------------------------------------------
    Parametro(
        "abordagens_no_comeco",
        "Abordagens em teste no Comeco",
        3,
        "Com amostra pequena, testar 7 jeitos ao mesmo tempo nao ensina nada. Comece com "
        "poucos; os outros entram quando houver placar.",
        "Aprendizado",
        inteiro=True,
    ),
    Parametro(
        "peso_coletivo",
        "Peso do que funcionou em outras contas do PubliBot",
        0.25,
        "0 = so a propria conta. O placar somado de todos os clientes (por abordagem e "
        "rede, sem conteudo) e o ponto de partida de quem esta comecando.",
        "Aprendizado",
    ),
    # -- Temas -----------------------------------------------------------------------------------
    Parametro(
        "peso_dores",
        "Peso: perto das dores do publico",
        1.0,
        "O tema fala do que o publico diz que sente (Negocio > dores).",
        "Temas",
    ),
    Parametro(
        "peso_demanda",
        "Peso: buscado no Google",
        1.0,
        "Volume de busca dos grupos de demanda do Radar perto do tema.",
        "Temas",
    ),
    Parametro(
        "peso_conversoes",
        "Peso: os artigos do tema trazem clientes",
        1.0,
        "Conversoes no site com o artigo na jornada.",
        "Temas",
    ),
    Parametro(
        "peso_amplitude",
        "Peso: aparece em varios artigos",
        0.5,
        "Tema que atravessa varios artigos tem mais material e mais fundamento.",
        "Temas",
    ),
    Parametro(
        "peso_referencias",
        "Peso: engaja no nicho (hashtags)",
        1.0,
        "Posts de outras contas, nas hashtags de referencia, que falam do tema e engajaram.",
        "Temas",
    ),
    # -- Impulso pago ---------------------------------------------------------------------------
    Parametro(
        "orcamento_mensal",
        "Orcamento mensal de impulso (R$)",
        0,
        "0 = so organico (a pagina mostra o que recomendaria). O PubliBot nunca gasta: "
        "ele diz qual post, quanto e como; voce paga na rede e registra aqui.",
        "Impulso pago",
    ),
    Parametro(
        "valor_teste",
        "Valor por versao num teste pago (R$)",
        10,
        "No Comeco, as duas versoes do mesmo tema recebem o mesmo valor, para o mesmo "
        "publico: a diferenca de resultado e da abordagem, nao da sorte.",
        "Impulso pago",
    ),
    Parametro("dias_de_teste", "Dias de um teste pago", 2, "", "Impulso pago", inteiro=True),
    Parametro(
        "valor_amplificar",
        "Valor para amplificar um post que ja funciona (R$)",
        20,
        "Depois do Comeco: so o que ja foi bem no organico ganha impulso.",
        "Impulso pago",
    ),
    # -- Fotos e mistura -------------------------------------------------------------------------
    Parametro(
        "nitidez_minima",
        "Nitidez minima de uma foto",
        30,
        "Abaixo disto a foto e descartada como tremida (variancia do Laplaciano). Fundo liso "
        "e foto de produto em estudio tem pouca borda: se boas fotos estao sendo "
        "descartadas, baixe.",
        "Fotos e mistura",
    ),
    Parametro(
        "brilho_minimo",
        "Brilho minimo (0 a 255)",
        35,
        "Foto mais escura que isto e descartada. Ambiente escuro de proposito (bar, "
        "estudio): baixe.",
        "Fotos e mistura",
        inteiro=True,
    ),
    Parametro(
        "brilho_maximo",
        "Brilho maximo (0 a 255)",
        235,
        "Foto mais clara que isto (estourada) e descartada.",
        "Fotos e mistura",
        inteiro=True,
    ),
    Parametro(
        "ajustar_mistura",
        "Ajustar sozinho a mistura artigos x fotos (1 = sim, 0 = nao)",
        1,
        "Com a conta entre 1% e 99% de fotos: a cada 30 dias, se um tipo funcionar bem "
        "mais que o outro (5 posts julgados de cada, 20 pontos de diferenca), a mistura "
        "anda 10 pontos para ele, entre 10% e 90%.",
        "Fotos e mistura",
        inteiro=True,
    ),
]

POR_NOME = {p.nome: p for p in PARAMETROS}


def valor(nome: str, config=None) -> float:
    from apps.social.models import ConfiguracaoSocial

    config = config or ConfiguracaoSocial.carregar()
    p = POR_NOME[nome]
    bruto = (config.parametros or {}).get(nome, p.padrao)
    try:
        numero = float(bruto)
    except (TypeError, ValueError):
        numero = float(p.padrao)
    return int(numero) if p.inteiro else numero


def todos(config=None) -> list[dict]:
    """Para a tela: cada parametro com o valor atual e se foi mudado."""
    from apps.social.models import ConfiguracaoSocial

    config = config or ConfiguracaoSocial.carregar()
    return [
        {
            "p": p,
            "valor": valor(p.nome, config),
            "mudado": p.nome in (config.parametros or {}),
        }
        for p in PARAMETROS
    ]


def gravar(config, dados: dict) -> list[str]:
    """Grava o que veio do formulario. Igual ao padrao: tira (volta a seguir o
    padrao se ele mudar). Devolve os nomes invalidos."""
    parametros = dict(config.parametros or {})
    invalidos = []
    for p in PARAMETROS:
        if p.nome not in dados:
            continue
        texto = str(dados[p.nome]).strip().replace(",", ".")
        try:
            numero = float(texto)
        except ValueError:
            invalidos.append(p.rotulo)
            continue
        if numero < 0:
            invalidos.append(p.rotulo)
            continue
        if numero == float(p.padrao):
            parametros.pop(p.nome, None)
        else:
            parametros[p.nome] = int(numero) if p.inteiro else numero
    config.parametros = parametros
    config.save(update_fields=["parametros"])
    return invalidos
