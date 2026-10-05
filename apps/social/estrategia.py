"""A estrategia de cada conta: em que fase ela esta, o que fazer agora e por que.

As fases seguem o que o mercado ja aprendeu sobre conta que comeca do zero
(e o que a matematica da amostra pequena exige):

* **Comeco** — quase ninguem segue; o alcance vem de fora (explorar, hashtags,
  sugestoes). Objetivo: DESCOBRIR o que o publico quer. Poucas abordagens,
  temas com sinal de fora (dores, busca, nicho), e — com orcamento — teste
  pago: duas versoes do mesmo tema, mesmo valor, mesmo publico.
* **Tracao** — ja ha quem volte. Objetivo: CONSISTENCIA. Frequencia fixa, os
  temas e abordagens que funcionaram, e o impulso so no que ja foi bem.
* **Crescimento** — o organico ja mede sozinho. Objetivo: AMPLIAR. Mais temas,
  colaboracoes, impulso nos melhores para gente parecida com quem engaja.
* **Escala** — conta estabelecida. Objetivo: CONVERTER. Do post ao artigo, do
  artigo a oferta; repostar o que traz cliente.

Os limites sao seguidores (ajustaveis em parametros); a pessoa pode fixar a
fase. Nada aqui gasta dinheiro: o PubliBot diz qual post impulsionar, quanto,
por quantos dias, com que objetivo e publico — a pessoa paga na rede e
registra o valor aqui, para o resultado entrar na conta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.social import experimentos, fontes, parametros, proprio
from apps.social.models import ConfiguracaoSocial, Destino, Post, ReferenciaDoNicho, Tema

FASES = {
    "comeco": {
        "nome": "Comeco",
        "objetivo": "Descobrir o que o publico quer ver.",
        "por_que": (
            "Com poucos seguidores, quase todo o alcance vem de quem ainda nao segue (explorar, "
            "hashtags, sugestoes). O numero de curtidas diz pouco; o que importa e a proporcao: "
            "de quem viu, quantos salvaram, compartilharam, comentaram ou clicaram. E com amostra "
            "pequena, testar muita coisa ao mesmo tempo nao ensina nada."
        ),
        "fazer": [
            "Postar com frequencia fixa (o teto da semana), sempre nos mesmos dias e horarios.",
            "Usar os temas de nota mais alta: eles tem sinal de interesse que nao depende de "
            "seguidores (dores, busca no Google, o que engaja no nicho).",
            "Testar poucas abordagens ao mesmo tempo (o sorteio ja se limita a elas).",
            "Responder todo comentario na primeira hora: conversa e o sinal que mais pesa para a "
            "rede mostrar o post a mais gente.",
            "Com orcamento: teste pago A/B (abaixo). E o jeito de ter amostra em dias, e nao em "
            "meses.",
        ],
        "medir": "Taxa por alcance (salvos + compartilhamentos + comentarios + cliques).",
    },
    "tracao": {
        "nome": "Tracao",
        "objetivo": "Consistencia: repetir o que funcionou, com variacao.",
        "por_que": (
            "Ja ha quem volte. O que funcionou no Comeco vira padrao; o impulso pago deixa de "
            "testar e passa a amplificar so o que ja foi bem sozinho."
        ),
        "fazer": [
            "Manter a frequencia; nao trocar tudo de uma vez.",
            "Metade dos posts nos temas e abordagens que ganharam, metade testando um tema novo.",
            "Impulsionar so o post que foi bem nas primeiras 48 horas (abaixo).",
            "Repostar, com outra abordagem, o tema que trouxe mais cliques.",
        ],
        "medir": "Taxa por alcance e cliques no link.",
    },
    "crescimento": {
        "nome": "Crescimento",
        "objetivo": "Ampliar: levar o que funciona a mais gente parecida.",
        "por_que": (
            "O organico ja mede sozinho. O impulso pago, agora, e para alcancar pessoas "
            "parecidas com quem ja engaja, nao para descobrir."
        ),
        "fazer": [
            "Impulsionar os melhores posts para publico parecido com quem engaja (publico "
            "semelhante, na rede).",
            "Colaborar com contas do nicho (post em conjunto, comentario util nos posts delas).",
            "Abrir temas novos a partir das dores que ainda nao viraram post.",
        ],
        "medir": "Cliques no link e conversoes provaveis.",
    },
    "escala": {
        "nome": "Escala",
        "objetivo": "Converter: do post ao artigo, do artigo a oferta.",
        "por_que": "Conta estabelecida: o que importa e quantos viram cliente.",
        "fazer": [
            "Priorizar temas cujos artigos trazem conversao.",
            "Impulsionar com objetivo de trafego para os artigos com chamada para a oferta.",
            "Repostar o que converte a cada poucos meses, com abordagem nova.",
        ],
        "medir": "Conversoes provaveis e custo por conversao (quando ha impulso).",
    },
}
ORDEM = ["comeco", "tracao", "crescimento", "escala"]

COMO_IMPULSIONAR = {
    "instagram": [
        "No app do Instagram, abra o post e toque em 'Impulsionar' (ou use o Gerenciador de "
        "Anuncios da Meta, adsmanager.facebook.com, para mais controle).",
        "Objetivo: o que a recomendacao diz ('Mais visitas ao perfil/engajamento' para teste; "
        "'Mais visitas ao site' para levar ao artigo).",
        "Publico: 'Criar o meu' — o local e os interesses que a recomendacao sugere. Nos testes "
        "A/B, o MESMO publico nas duas versoes.",
        "Orcamento e duracao: o valor e os dias da recomendacao.",
        "Pagamento: cartao de credito, ou saldo pre-pago (Pix ou boleto) na conta de "
        "anuncios da Meta. O valor sai da conta de anuncios, nao do PubliBot.",
        "Volte aqui e toque em 'Registrei o impulso' com o valor: o resultado do post passa a "
        "contar como pago (fica fora da comparacao organica, e o teste e julgado).",
    ],
    "linkedin": [
        "No post, 'Impulsionar' (pagina da empresa) ou o Campaign Manager "
        "(linkedin.com/campaignmanager).",
        "O clique no LinkedIn custa bem mais que no Instagram: so vale para post com chance "
        "de virar cliente (B2B, servico de valor alto).",
        "Publico: cargo, setor e regiao do publico desta conta.",
        "Pagamento: cartao de credito na conta do Campaign Manager.",
        "Registre aqui o valor depois.",
    ],
    "gmn": [
        "O Perfil da Empresa no Google nao tem impulso de post. O equivalente e um anuncio "
        "local no Google Ads — fora do escopo destas recomendacoes.",
    ],
}


def fase(destino: Destino, config: ConfiguracaoSocial | None = None) -> str:
    if destino.fase_manual in FASES:
        return destino.fase_manual
    n = destino.seguidores or 0
    config = config or ConfiguracaoSocial.carregar()
    if n >= parametros.valor("seguidores_escala", config):
        return "escala"
    if n >= parametros.valor("seguidores_crescimento", config):
        return "crescimento"
    if n >= parametros.valor("seguidores_tracao", config):
        return "tracao"
    return "comeco"


def registrar_seguidores(destino: Destino, n: int) -> None:
    hoje = timezone.localdate().isoformat()
    historico = [h for h in (destino.seguidores_historico or []) if h.get("dia") != hoje]
    historico.append({"dia": hoje, "n": int(n)})
    destino.seguidores = int(n)
    destino.seguidores_historico = historico[-180:]
    destino.save(update_fields=["seguidores", "seguidores_historico"])


def crescimento_semanal(destino: Destino) -> float | None:
    """Seguidores ganhos por semana, nas ultimas 4 semanas (None sem historico)."""
    historico = destino.seguidores_historico or []
    if len(historico) < 2:
        return None
    limite = (timezone.localdate() - timedelta(days=28)).isoformat()
    janela = [h for h in historico if h["dia"] >= limite] or historico[-2:]
    if len(janela) < 2:
        return None
    from datetime import date

    dias = (date.fromisoformat(janela[-1]["dia"]) - date.fromisoformat(janela[0]["dia"])).days
    if dias <= 0:
        return None
    return round((janela[-1]["n"] - janela[0]["n"]) * 7 / dias, 1)


def gasto_do_mes() -> Decimal:
    inicio = timezone.localdate().replace(day=1)
    return Post.objects.filter(impulsionado=True, impulso_em__date__gte=inicio).aggregate(
        total=Sum("custo_impulso")
    )["total"] or Decimal("0")


@dataclass
class Recomendacao:
    tipo: str  # "teste" | "amplificar" | "gerar_par"
    titulo: str
    por_que: str
    valor: float = 0
    dias: int = 0
    objetivo: str = ""
    publico: str = ""
    posts: list = field(default_factory=list)
    tema: Tema | None = None


def _publico_sugerido(destino: Destino, tema_ou_titulo: str) -> str:
    regioes = fontes.negocio().get("regioes") or []
    onde = ", ".join(regioes[:3]) or "a regiao onde o negocio atende (ou o Brasil)"
    return f"Local: {onde}. Interesses ligados a: {tema_ou_titulo[:80]}."


def impulsos(destino: Destino, config: ConfiguracaoSocial | None = None) -> list[Recomendacao]:
    """O que impulsionar agora nesta conta (vazio no Google)."""
    if destino.rede == "gmn":
        return []
    config = config or ConfiguracaoSocial.carregar()
    etapa = fase(destino, config)
    agora = timezone.now()
    saida: list[Recomendacao] = []
    if etapa == "comeco":
        dias = parametros.valor("dias_de_teste", config)
        valor_teste = parametros.valor("valor_teste", config)
        # Um par A/B pronto (publicado ou aprovado) e ainda nao impulsionado.
        par = (
            destino.posts.filter(
                variante_de__isnull=False,
                impulsionado=False,
                situacao__in=[Post.Situacao.PUBLICADO, Post.Situacao.APROVADO],
            )
            .select_related("variante_de", "abordagem", "variante_de__abordagem")
            .order_by("-criado_em")
            .first()
        )
        if par is not None and not par.variante_de.impulsionado:
            a, b = par.variante_de, par
            saida.append(
                Recomendacao(
                    tipo="teste",
                    titulo=f'Teste A/B: "{a.abordagem}" x "{b.abordagem}"',
                    por_que=(
                        "Mesmo tema, duas abordagens, mesmo valor e mesmo publico: em "
                        f"{dias} dias a diferenca de taxa diz qual jeito de falar funciona com "
                        "este publico — o que levaria meses no organico com poucos seguidores."
                    ),
                    valor=valor_teste,
                    dias=dias,
                    objetivo="Engajamento (ou 'Mais visitas ao perfil')",
                    publico=_publico_sugerido(destino, a.artigo_titulo),
                    posts=[a, b],
                )
            )
        else:
            tema = Tema.objects.filter(ativo=True).order_by("-nota").first()
            if tema is not None:
                saida.append(
                    Recomendacao(
                        tipo="gerar_par",
                        titulo="Preparar um teste A/B com o melhor tema",
                        por_que=(
                            "Ainda nao ha duas versoes prontas para comparar. O PubliBot "
                            "escreve o mesmo tema com duas abordagens; depois de aprovadas e "
                            "publicadas, impulsione as duas com o mesmo valor."
                        ),
                        valor=valor_teste * 2,
                        dias=dias,
                        tema=tema,
                    )
                )
        return saida

    # Depois do Comeco: amplificar o que ja foi bem nas primeiras 48 horas.
    minimo = parametros.valor("alcance_minimo", config)
    recentes = list(
        destino.posts.filter(
            situacao=Post.Situacao.PUBLICADO,
            impulsionado=False,
            publicado_em__gte=agora - timedelta(days=4),
            publicado_em__lte=agora - timedelta(hours=48),
        )
    )
    historico = [
        v
        for p in destino.posts.filter(situacao=Post.Situacao.PUBLICADO, impulsionado=False)
        if (v := experimentos.valor(p, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo))
        is not None
    ]
    if len(historico) < 4:
        return saida
    corte = sorted(historico)[int(len(historico) * 0.75)]
    for post in recentes:
        v = experimentos.valor(post, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo)
        if v is None or v < corte:
            continue
        converte = etapa == "escala" or bool((post.metricas or {}).get("conversoes"))
        saida.append(
            Recomendacao(
                tipo="amplificar",
                titulo=f"Amplificar: {post.artigo_titulo[:80]}",
                por_que=(
                    "Nas primeiras 48 horas este post ficou entre os 25% melhores da conta. "
                    "Impulsionar o que ja funciona sozinho e o uso mais eficiente do dinheiro."
                ),
                valor=parametros.valor("valor_amplificar", config),
                dias=3,
                objetivo="Trafego para o site (o artigo)" if converte else "Engajamento",
                publico=_publico_sugerido(destino, post.artigo_titulo)
                + (" Ou: publico semelhante a quem engajou." if etapa != "tracao" else ""),
                posts=[post],
            )
        )
    return saida


def formatos_do_nicho(destino: Destino) -> list[dict]:
    """Engajamento medio por formato nas referencias do nicho."""
    totais: dict = {}
    for ref in destino.referencias.all():
        item = totais.setdefault(ref.formato or "?", [0, 0])
        item[0] += ref.engajamento
        item[1] += 1
    linhas = [
        {"formato": f, "media": round(s / n), "posts": n} for f, (s, n) in totais.items() if n
    ]
    return sorted(linhas, key=lambda x: -x["media"])


def plano(destino: Destino) -> dict:
    """Tudo o que a pagina de estrategia mostra para uma conta."""
    config = ConfiguracaoSocial.carregar()
    etapa = fase(destino, config)
    indice = ORDEM.index(etapa)
    proxima = ORDEM[indice + 1] if indice + 1 < len(ORDEM) else None
    limite_proxima = parametros.valor(f"seguidores_{proxima}", config) if proxima else None
    semana = timezone.now() - timedelta(days=7)
    minimo = parametros.valor("alcance_minimo", config)
    publicados = list(
        destino.posts.filter(
            situacao=Post.Situacao.PUBLICADO, publicado_em__gte=timezone.now() - timedelta(days=60)
        )
    )
    taxas = [
        v
        for p in publicados
        if not p.impulsionado
        and (v := experimentos.valor(p, ConfiguracaoSocial.Metrica.TAXA, alcance_minimo=minimo))
        is not None
        and isinstance((p.metricas or {}).get("alcance"), int | float)
    ]
    referencia = (
        parametros.valor(f"taxa_{destino.rede}", config)
        if destino.rede in ("instagram", "linkedin")
        else None
    )
    orcamento = Decimal(str(parametros.valor("orcamento_mensal", config)))
    return {
        "destino": destino,
        "fase": etapa,
        "info": FASES[etapa],
        "proxima": FASES[proxima]["nome"] if proxima else "",
        "faltam": max(int(limite_proxima) - (destino.seguidores or 0), 0) if proxima else 0,
        "crescimento": crescimento_semanal(destino),
        "semana": {
            "feitos": destino.posts.filter(
                situacao__in=[Post.Situacao.PUBLICADO, Post.Situacao.APROVADO],
                criado_em__gte=semana,
            ).count(),
            "teto": destino.teto_semanal,
            "esperando": destino.posts.filter(situacao=Post.Situacao.RASCUNHO).count(),
        },
        "temas": list(Tema.objects.filter(ativo=True).order_by("-nota")[:5]),
        "abordagens": experimentos.quadro(destino),
        "impulsos": impulsos(destino, config),
        "como_impulsionar": COMO_IMPULSIONAR.get(destino.rede, []),
        "orcamento": orcamento,
        "gasto": gasto_do_mes(),
        "taxa_media": round(100 * sum(taxas) / len(taxas), 2) if taxas else None,
        "taxa_referencia": referencia,
        "medidos": len(taxas),
        "inconclusivos": sum(
            1
            for p in publicados
            if isinstance((p.metricas or {}).get("alcance"), int | float)
            and (p.metricas or {}).get("alcance", 0) < minimo
        ),
        "formatos": formatos_do_nicho(destino),
        "por_tipo": proprio.placar_por_tipo(destino),
        "estoque": proprio.estoque(destino) if destino.fotos_por_cento else None,
        "referencias": list(destino.referencias.order_by("-curtidas")[:6]),
        "tem_referencias": ReferenciaDoNicho.objects.filter(destino=destino).exists(),
    }


def primeiros_passos(destino: Destino) -> list[dict]:
    """O que falta para a conta andar sozinha, do essencial ao opcional. Cada
    item diz o que e, se esta feito, e onde fazer."""
    from django.conf import settings

    from apps.social.redes import rede

    r = rede(destino.rede)
    app = {
        "linkedin": "SOCIAL_LINKEDIN_CLIENT_ID",
        "instagram": "SOCIAL_META_APP_ID",
        "gmn": "SOCIAL_GOOGLE_CLIENT_ID",
    }[destino.rede]
    tem_app = bool(getattr(settings, app, ""))
    itens = [
        {
            "feito": bool(destino.publico.strip()),
            "titulo": "Dizer quem esta nesta conta",
            "explica": (
                "O publico da conta muda o texto (um LinkedIn de gestores nao e um Instagram "
                "de pacientes). Vazio, vale o padrao da rede: '" + r.publico_padrao + "'"
            ),
            "onde": "configurar",
            "essencial": True,
        },
        {
            "feito": destino.posts.filter(
                situacao__in=[Post.Situacao.APROVADO, Post.Situacao.PUBLICADO]
            ).exists(),
            "titulo": "Aprovar o primeiro post",
            "explica": "Os posts sao escritos sozinhos; nada sai sem voce aprovar.",
            "onde": "revisar",
            "essencial": True,
        },
        {
            "feito": destino.conectado,
            "titulo": "Conectar a conta (publicar sozinho)",
            "explica": (
                "Opcional. Sem conectar, voce copia e cola o post — e os cliques sao medidos "
                "do mesmo jeito. Conectado, o post sai sozinho na hora e o PubliBot le "
                "seguidores, alcance e comentarios."
                + ("" if tem_app else f" Antes, o app da rede precisa estar no .env ({app}).")
            ),
            "onde": "configurar",
            "essencial": False,
        },
    ]
    if destino.rede == "instagram":
        itens.append(
            {
                "feito": bool(destino.hashtags_de_referencia.strip()),
                "titulo": "Hashtags de referencia do nicho",
                "explica": (
                    "Opcional. Ate 10 hashtags que o seu publico segue: o PubliBot le os posts "
                    "que mais engajam nelas (com a conta conectada) e usa na nota dos temas."
                ),
                "onde": "configurar",
                "essencial": False,
            }
        )
    if destino.rede != "gmn":
        itens.append(
            {
                "feito": parametros.valor("orcamento_mensal") > 0,
                "titulo": "Orcamento para impulso (se quiser)",
                "explica": (
                    "Opcional. Com algum valor por mes, o PubliBot recomenda testes pagos que "
                    "ensinam em dias o que o organico levaria meses. Zero: so organico."
                ),
                "onde": "parametros",
                "essencial": False,
            }
        )
    return itens
