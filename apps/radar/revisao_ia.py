"""A volta da outra IA: a resposta colada vira mudanca no radar, com previa.

O pedido (`radar.resumo.texto_para_ia`) leva os temas com um codigo curto e
pede a resposta em blocos fixos. Aqui a resposta e lida, comparada com o que
existe (a previa) e, com a confirmacao da pessoa, aplicada:

* SEMENTES e DORES substituem as listas inteiras, NA ORDEM da resposta — a
  primeira e a que a proxima rodada busca primeiro;
* BONS e RUINS viram etiqueta no tema, com o motivo. Nada e
  descartado sozinho: a tela filtra os ruins, e a pessoa descarta;
* COMENTARIOS aparecem na previa e nao mudam nada.

O pedido so leva temas ainda sem avaliacao. Com o radar rodando semanas sem
ajuste, a lista cresce; a IA recebe sempre o proximo lote, e nao tudo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import CharField
from django.db.models.functions import Cast
from django.utils import timezone

from core.resposta_ia import itens, ler_blocos

ROTULOS = [
    "SEMENTES",
    "DORES",
    "BONS",
    "RUINS",
    "TEMA",
    "PUBLICO",
    "OFERTA",
    "FRENTES",
    "COMENTARIOS",
]
# O pedido pede mudanca gradual; acima disto a previa avisa.
MUDANCA_GRANDE = 5
PARTES = ("sementes", "dores", "temas", "negocio")
TEMAS_POR_PEDIDO = 40
OPORTUNIDADES_POR_PEDIDO = 15
TAMANHO_DO_CODIGO = 6


def codigo(grupo) -> str:
    return f"t-{grupo.pk.hex[:TAMANHO_DO_CODIGO]}"


def temas_para_avaliar(limite: int = TEMAS_POR_PEDIDO):
    """Os temas vivos ainda sem avaliacao, os de maior nota primeiro."""
    from apps.radar.models import GrupoDeDemanda

    return (
        GrupoDeDemanda.objects.filter(avaliacao_ia="")
        .exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO)
        .select_related("pauta")
        .order_by("-nota")[:limite]
    )


@dataclass
class Leitura:
    sementes: list[str] = field(default_factory=list)
    dores: list[str] = field(default_factory=list)
    bons: list[tuple[str, str]] = field(default_factory=list)
    ruins: list[tuple[str, str]] = field(default_factory=list)
    negocio: dict = field(default_factory=dict)
    comentarios: str = ""

    @property
    def vazia(self) -> bool:
        return not (self.sementes or self.dores or self.bons or self.ruins or self.negocio)


def _sem_repetir(lista: list[str]) -> list[str]:
    vistos, saida = set(), []
    for item in lista:
        chave = item.lower()
        if chave not in vistos:
            vistos.add(chave)
            saida.append(item[:200])
    return saida


def _temas(bloco: str) -> list[tuple[str, str]]:
    """[(codigo, motivo)] de linhas como "t-3fa9c1: publico de estudante"."""
    saida = []
    for linha in itens(bloco):
        cabeca, _, motivo = linha.partition(":")
        if not motivo:
            cabeca, _, motivo = linha.partition(" - ")
        codigo_lido = cabeca.strip().strip("`[]()").lower()
        if codigo_lido.startswith("t-") and len(codigo_lido) > 2:
            saida.append((codigo_lido, motivo.strip()[:300]))
    return saida


def ler(resposta: str) -> Leitura:
    from apps.editorial.primeiros_passos import ler_negocio

    blocos = ler_blocos(resposta, ROTULOS)
    negocio = ler_negocio("", blocos=blocos)
    negocio.pop("dores", None)  # as dores daqui sao a lista do radar, acima
    return Leitura(
        negocio=negocio,
        sementes=_sem_repetir(itens(blocos.get("SEMENTES", ""))),
        dores=_sem_repetir(itens(blocos.get("DORES", ""))),
        bons=_temas(blocos.get("BONS", "")),
        ruins=_temas(blocos.get("RUINS", "")),
        comentarios=blocos.get("COMENTARIOS", "").strip(),
    )


def _achar(codigo_lido: str):
    """O tema do codigo, ou None se nao existe ou se o codigo e ambiguo."""
    from apps.radar.models import GrupoDeDemanda

    prefixo = codigo_lido.removeprefix("t-")
    if len(prefixo) < 4 or any(c not in "0123456789abcdef" for c in prefixo):
        return None
    achados = list(
        GrupoDeDemanda.objects.annotate(texto_do_id=Cast("id", CharField()))
        .filter(texto_do_id__startswith=prefixo)
        .exclude(situacao=GrupoDeDemanda.Situacao.DESCARTADO)[:2]
    )
    return achados[0] if len(achados) == 1 else None


def _diferenca(atual: list[str], nova: list[str]) -> dict:
    atuais = {s.lower() for s in atual}
    novas = {s.lower() for s in nova}
    return {
        "lista": nova,
        "entram": [s for s in nova if s.lower() not in atuais],
        "saem": [s for s in atual if s.lower() not in novas],
    }


def _negocio_proposto(leitura: Leitura) -> list[dict]:
    """[{campo, rotulo, atual, proposto}] so do que muda."""
    from apps.editorial.models import PerfilDoNegocio
    from apps.editorial.primeiros_passos import unir

    perfil = PerfilDoNegocio.carregar()
    saida = []
    for campo, rotulo in (("tema", "Tema"), ("publico", "Publico"), ("oferta", "Oferta")):
        proposto = leitura.negocio.get(campo)
        if proposto and proposto.strip() != (getattr(perfil, campo) or "").strip():
            saida.append(
                {
                    "campo": campo,
                    "rotulo": rotulo,
                    "atual": getattr(perfil, campo),
                    "proposto": proposto,
                }
            )
    if leitura.negocio.get("frentes"):
        novas = unir(perfil.frentes, leitura.negocio["frentes"])
        if novas.strip() != perfil.frentes.strip():
            saida.append(
                {
                    "campo": "frentes",
                    "rotulo": "Frentes (somadas)",
                    "atual": perfil.frentes,
                    "proposto": novas,
                }
            )
    return saida


def previa(leitura: Leitura) -> dict:
    """O que aplicar mudaria, para a pessoa conferir antes."""
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    temas, nao_achados = [], []
    for avaliacao, pares in (("boa", leitura.bons), ("ruim", leitura.ruins)):
        for codigo_lido, motivo in pares:
            grupo = _achar(codigo_lido)
            if grupo is None:
                nao_achados.append(codigo_lido)
            else:
                temas.append({"grupo": grupo, "avaliacao": avaliacao, "motivo": motivo})
    sementes = _diferenca(config.lista_de_sementes, leitura.sementes) if leitura.sementes else None
    dores = _diferenca(config.lista_de_dores, leitura.dores) if leitura.dores else None
    grande = any(
        d and max(len(d["entram"]), len(d["saem"])) > MUDANCA_GRANDE for d in (sementes, dores)
    )
    return {
        "sementes": sementes,
        "dores": dores,
        "mudanca_grande": grande,
        "negocio": _negocio_proposto(leitura),
        "temas": temas,
        "bons": sum(t["avaliacao"] == "boa" for t in temas),
        "ruins": sum(t["avaliacao"] == "ruim" for t in temas),
        "nao_achados": nao_achados,
        "comentarios": leitura.comentarios,
    }


def aplicar(leitura: Leitura, partes=PARTES) -> dict:
    """Aplica as partes escolhidas e devolve as contagens para a mensagem.

    O negocio so muda quando a pessoa marca: e a ancora de tudo que o radar
    mede, e mudar a ancora e decisao, nao efeito colateral.
    """
    from apps.radar.models import ConfiguracaoDoRadar

    config = ConfiguracaoDoRadar.carregar()
    campos = []
    if leitura.sementes and "sementes" in partes:
        config.sementes = "\n".join(leitura.sementes)
        campos.append("sementes")
    if leitura.dores and "dores" in partes:
        config.dores = "\n".join(leitura.dores)
        campos.append("dores")
    if campos:
        config.save(update_fields=campos)

    negocio = 0
    if "negocio" in partes:
        from apps.editorial.models import PerfilDoNegocio

        mudancas = _negocio_proposto(leitura)
        if mudancas:
            perfil = PerfilDoNegocio.carregar()
            for mudanca in mudancas:
                setattr(perfil, mudanca["campo"], mudanca["proposto"])
            perfil.save()
            negocio = len(mudancas)

    agora = timezone.now()
    avaliados = 0
    pares_por_avaliacao = (
        (("boa", leitura.bons), ("ruim", leitura.ruins)) if "temas" in partes else ()
    )
    for avaliacao, pares in pares_por_avaliacao:
        for codigo_lido, motivo in pares:
            grupo = _achar(codigo_lido)
            if grupo is None:
                continue
            grupo.avaliacao_ia, grupo.motivo_ia, grupo.avaliado_em = avaliacao, motivo, agora
            grupo.save(update_fields=["avaliacao_ia", "motivo_ia", "avaliado_em", "atualizado_em"])
            avaliados += 1
    return {
        "sementes": len(leitura.sementes) if "sementes" in campos else 0,
        "dores": len(leitura.dores) if "dores" in campos else 0,
        "temas": avaliados,
        "negocio": negocio,
    }
