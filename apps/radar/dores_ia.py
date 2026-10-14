"""As dores descobertas revisadas por outra IA (copiar o pedido, colar a resposta).

O filtro de palavras acha a frase com cara de dor, mas nao entende: um
"nunca consegui aprender ate ver esse video" e gratidao, e "esquecer o Excel
por falta de uso" pode ser a mesma dor que "a equipe perde tempo com trabalho
manual" — ou nao. Quem le as falas e decide e a IA grande; o PubliBot so
monta o pedido e aplica a resposta:

* ``nao e dor`` — a descoberta e descartada (fica na lista, com o motivo);
* ``dor`` — ganha um nome curto, nas palavras do publico; e, se a IA disser
  que e a mesma de uma hipotese (H3), a tela a junta na hipotese, que fica
  confirmada.

Nada aqui chama modelo: a resposta colada e lida por algoritmo.
"""

from __future__ import annotations

import re

from apps.radar import dores as banco
from apps.radar.models import DorDoPublico
from core.resposta_ia import itens, ler_blocos

MAXIMO_DE_DESCOBERTAS = 40
FALAS_POR_DOR = 4


def codigo(dor: DorDoPublico) -> str:
    """D (descoberta) ou H (hipotese) e o comeco do id: o mesmo codigo vale
    entre copiar e colar, mesmo que entrem falas novas no meio."""
    return ("H" if dor.digitada else "D") + dor.pk.hex[:6].upper()


def _codigos() -> tuple[dict[str, DorDoPublico], dict[str, DorDoPublico]]:
    """As descobertas (as de mais falas primeiro) e as hipoteses, por codigo."""
    descobertas = DorDoPublico.objects.filter(digitada=False, descartada=False).order_by(
        "-falas_total", "-nota"
    )[:MAXIMO_DE_DESCOBERTAS]
    hipoteses = DorDoPublico.objects.filter(digitada=True, descartada=False).order_by("rotulo")
    return {codigo(d): d for d in descobertas}, {codigo(h): h for h in hipoteses}


def pedido() -> str:
    """O texto para copiar e colar na outra IA. Vazio se nao ha descoberta."""
    from apps.editorial.models import perfil_do_negocio

    descobertas, hipoteses = _codigos()
    if not descobertas:
        return ""
    perfil = perfil_do_negocio()
    negocio = [
        f"- Tema: {getattr(perfil, 'tema', '') or '(nao informado)'}",
        f"- Publico: {getattr(perfil, 'publico', '') or '(nao informado)'}",
        f"- Oferta: {getattr(perfil, 'oferta', '') or '(nao informada)'}",
    ]
    blocos = []
    for codigo, dor in descobertas.items():
        falas = "\n".join(f"   - “{f.texto[:300]}”" for f in dor.falas.all()[:FALAS_POR_DOR])
        blocos.append(f"{codigo} ({dor.falas_total} falas):\n{falas}")
    linhas_h = [f"{c}: {h.rotulo}" for c, h in hipoteses.items()] or ["(nenhuma)"]
    return f"""\
Sou dono de um negocio e junto o que o meu publico diz em comentarios (YouTube,
redes, avaliacoes de concorrentes no Google) para achar as DORES dele: o
problema que a pessoa sente, nas palavras dela — e NAO o meu servico.

O negocio:
{chr(10).join(negocio)}

Um filtro automatico separou as falas abaixo em grupos (codigos que comecam com D). Ele erra:
agradecimento ao video ("nunca consegui ate ver seu video, obrigada"), elogio,
pedido de conteudo e conversa nao sao dor. Para cada grupo, diga:

- se e dor do publico deste negocio (o problema que ele sente), ou nao;
- se for, um nome curto para a dor (ate 10 palavras), do jeito que o publico
  diria, sem jargao;
- se for a mesma dor de uma das minhas hipoteses (codigos que comecam com H), qual.

Minhas hipoteses (o que eu suponho que o publico sente):
{chr(10).join(linhas_h)}

Os grupos:

{chr(10).join(blocos)}

Responda EXATAMENTE neste formato, uma linha por grupo, sem nada antes:

DORES:
- D1A2B3C | nao e dor | o motivo em poucas palavras
- D4D5E6F | dor | nome curto da dor | H7A8B9C
- D0F1E2D | dor | nome curto da dor | -
FIM
"""


_NAO = re.compile(r"\bnao\s*(e|eh)?\s*dor\b")


def ler(resposta: str) -> list[dict]:
    """[{"codigo": "D2", "dor": bool, "nome": ..., "hipotese": "H3"|"", "motivo": ...}]."""
    bloco = ler_blocos(resposta, ["DORES"]).get("DORES", "")
    saida = []
    for linha in itens(bloco):
        partes = [p.strip().strip('`*"“”') for p in linha.split("|")]
        if len(partes) < 2 or not re.fullmatch(r"[Dd][0-9A-Fa-f]{6}", partes[0]):
            continue
        veredito = banco.normalizar(partes[1])
        e_dor = not _NAO.search(veredito) and "dor" in veredito
        item = {"codigo": partes[0].upper(), "dor": e_dor, "nome": "", "hipotese": "", "motivo": ""}
        if e_dor:
            item["nome"] = partes[2] if len(partes) > 2 else ""
            h = partes[3].upper() if len(partes) > 3 else ""
            item["hipotese"] = h if re.fullmatch(r"H[0-9A-F]{6}", h) else ""
        else:
            item["motivo"] = partes[2] if len(partes) > 2 else ""
        saida.append(item)
    return saida


def aplicar(resposta: str) -> dict:
    """Aplica a resposta colada. Os codigos sao os do pedido: a pagina monta
    o mesmo pedido na mesma ordem enquanto nada novo entra."""
    descobertas, hipoteses = _codigos()
    feito = {"descartadas": 0, "renomeadas": 0, "juntadas": 0, "nao_achados": []}
    for item in ler(resposta):
        dor = descobertas.get(item["codigo"])
        if dor is None:
            feito["nao_achados"].append(item["codigo"])
            continue
        if not item["dor"]:
            banco.descartar(dor, motivo=item["motivo"] or "a outra IA disse que nao e dor")
            feito["descartadas"] += 1
            continue
        if item["nome"]:
            dor.rotulo = item["nome"][:300]
            dor.save(update_fields=["rotulo", "atualizada_em"])
            feito["renomeadas"] += 1
        hipotese = hipoteses.get(item["hipotese"])
        if hipotese is not None:
            banco.juntar(dor, hipotese)
            feito["juntadas"] += 1
    return feito
