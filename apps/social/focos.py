"""O foco de cada post: para que ele existe, como e escrito e como e medido.

Uma conta precisa de posts com focos diferentes, em proporcoes que mudam com a
fase (ver `estrategia.FASES`):

* **alcance** — para quem AINDA NAO SEGUE. O Instagram mostra o post a quem nao
  segue pelo que ele promete a um estranho e pelos envios por mensagem (o
  sinal que mais pesa com nao seguidores, segundo o proprio Instagram). E o
  post que traz seguidor: tema amplo da dor, valor inteiro no carrossel, feito
  para ser mandado a alguem. Medido pelo alcance (e seguidores ganhos).
* **engajamento** — para quem ja segue: conversa, salvar, mandar. Medido pela
  taxa (comentarios + salvos + envios) por alcance.
* **site** — para levar ao artigo: deixa no ar algo que so o artigo responde.
  Medido por cliques (e pela leitura no site, quando o site manda).

A escolha do foco de um post novo e por algoritmo: o que esta mais abaixo da
mistura da fase, nas ultimas semanas. A pessoa (ou a outra IA) pode fixar.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

FOCOS = {
    "alcance": {
        "nome": "Alcance (quem nao segue)",
        "curto": "alcance",
        "instrucao": (
            "FOCO DO POST: ALCANCE — feito para quem AINDA NAO segue a conta. O tema e "
            "amplo, a dor que muita gente vive, dita de um jeito que um estranho entende "
            "sem contexto. O carrossel entrega o valor inteiro (a pessoa aprende algo "
            "sem sair dali), feito para ser MANDADO a alguem ('isso e a sua cara'). Nada "
            "de pedir para seguir: quem gostar segue sozinho."
        ),
        "impulso": "Alcance (ou 'Mais visitas ao perfil')",
    },
    "engajamento": {
        "nome": "Engajamento (quem ja segue)",
        "curto": "engajamento",
        "instrucao": (
            "FOCO DO POST: ENGAJAMENTO — feito para quem ja segue: provoca conversa. Uma "
            "opiniao clara sobre o achado, um 'voce tambem?' concreto, e termina com UMA "
            "pergunta simples de responder nos comentarios (escolha entre opcoes, ou a "
            "experiencia da pessoa). Vale tambem o 'salve para consultar depois'."
        ),
        "impulso": "Engajamento",
    },
    "site": {
        "nome": "Site (levar ao artigo)",
        "curto": "site",
        "instrucao": (
            "FOCO DO POST: SITE — feito para levar ao artigo. Mostra o problema e o "
            "primeiro passo, e deixa no ar o que so o artigo responde (o como, o numero "
            "completo, a lista inteira), sem enganar: a promessa tem de estar no artigo. "
            "A chamada para o link e o centro do final."
        ),
        "impulso": "Trafego para o site (o artigo)",
    },
}

# A mistura de focos de cada fase (soma 1). Comecando, o que mais importa e ser
# visto por quem nao segue; na escala, levar ao site e converter.
MISTURA = {
    "comeco": {"alcance": 0.5, "engajamento": 0.35, "site": 0.15},
    "tracao": {"alcance": 0.35, "engajamento": 0.35, "site": 0.3},
    "crescimento": {"alcance": 0.4, "engajamento": 0.25, "site": 0.35},
    "escala": {"alcance": 0.25, "engajamento": 0.25, "site": 0.5},
}
# Quanto do passado conta para saber o que falta na mistura.
JANELA = timedelta(days=21)


def valido(foco: str) -> str:
    return foco if foco in FOCOS else ""


def do_post(post) -> str:
    return valido((post.extras or {}).get("foco", ""))


def instrucao(post) -> str:
    foco = do_post(post)
    return FOCOS[foco]["instrucao"] + "\n" if foco else ""


def mistura(destino) -> dict:
    from apps.social.estrategia import fase

    return MISTURA.get(fase(destino), MISTURA["tracao"])


def foco_da_vez(destino) -> str:
    """O foco mais abaixo da mistura da fase, nos posts recentes da conta."""
    from apps.social.models import Post

    alvo = mistura(destino)
    contagem = dict.fromkeys(FOCOS, 0)
    recentes = destino.posts.filter(criado_em__gte=timezone.now() - JANELA).exclude(
        motivo=Post.Motivo.HISTORICO
    )
    for extras in recentes.values_list("extras", flat=True):
        foco = valido((extras or {}).get("foco", ""))
        if foco:
            contagem[foco] += 1
    total = sum(contagem.values()) + 1  # o post que vai nascer
    # O maior deficit (meta - parte atual, contando o novo como do foco).
    return max(FOCOS, key=lambda f: alvo.get(f, 0) - contagem[f] / total)


def valor(post) -> float | None:
    """O numero do foco do post (para comparar com os do mesmo foco)."""
    m = post.metricas or {}
    alcance = m.get("alcance") or m.get("visualizacoes")
    foco = do_post(post)
    if foco == "alcance":
        return float(alcance) if alcance else None
    if not alcance:
        return None
    if foco == "engajamento":
        conversa = sum(int(m.get(k) or 0) for k in ("comentarios", "salvos", "compartilhamentos"))
        return conversa / alcance
    if foco == "site":
        cliques = max(int(post.cliques or 0), int((m.get("site") or {}).get("aberturas") or 0))
        return cliques / alcance
    return None


def desempenho(post) -> dict:
    """Como o post foi NO SEU FOCO, contra os da conta com o mesmo foco (so
    organicos): {"foco", "posicao" (0 a 1, 1 = o melhor), "comparados"}."""
    from apps.social.models import Post

    foco = do_post(post)
    meu = valor(post) if foco else None
    if meu is None or post.impulsionado:
        return {}
    outros = [
        v
        for p in post.destino.posts.filter(
            situacao=Post.Situacao.PUBLICADO, impulsionado=False, extras__foco=foco
        ).exclude(pk=post.pk)
        if (v := valor(p)) is not None
    ]
    if len(outros) < 3:
        return {"foco": foco, "posicao": None, "comparados": len(outros)}
    abaixo = sum(1 for v in outros if v < meu)
    return {"foco": foco, "posicao": abaixo / len(outros), "comparados": len(outros)}
